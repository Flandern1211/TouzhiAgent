from __future__ import annotations

from datetime import date, datetime, timezone
from threading import Event, Thread, current_thread
from typing import Callable
from zoneinfo import ZoneInfo

from fund_agent.config.settings import Settings


def is_full_analysis_due(at: datetime, settings: Settings) -> bool:
    local = at.astimezone(ZoneInfo(settings.analysis_timezone))
    return local.weekday() in settings.analysis_weekdays and local.time() >= settings.full_analysis_time


class TrackingScheduler:
    """Small process-local scheduler; the callback owns the tracking workflow."""

    def __init__(
        self,
        callback: Callable[[], object],
        *,
        full_analysis_callback: Callable[[], object] | None = None,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        interval_seconds: float = 60.0,
    ):
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.callback = callback
        self.full_analysis_callback = full_analysis_callback or callback
        self.settings = settings or Settings.from_env()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.interval_seconds = interval_seconds
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._last_evidence_at: datetime | None = None
        self._last_full_date: date | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_once(self) -> object:
        return self.callback()

    def tick(self, at: datetime | None = None) -> object | None:
        now = at or self.clock()
        local_date = now.astimezone(ZoneInfo(self.settings.analysis_timezone)).date()
        if is_full_analysis_due(now, self.settings) and self._last_full_date != local_date:
            self._last_full_date = local_date
            self._last_evidence_at = now
            return self.full_analysis_callback()
        if self._last_evidence_at is None or now - self._last_evidence_at >= self.settings.evidence_interval:
            self._last_evidence_at = now
            return self.callback()
        return None

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()

        def loop() -> None:
            while not self._stop_event.is_set():
                self.tick()
                self._stop_event.wait(self.interval_seconds)

        self._thread = Thread(target=loop, name="fund-agent-tracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread is not current_thread():
            self._thread.join(timeout=min(self.interval_seconds, 2.0))
        self._thread = None
