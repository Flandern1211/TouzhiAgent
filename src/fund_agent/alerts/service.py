from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fund_agent.agent.reviewer import ReviewResult
from fund_agent.domain.models import AlertStatus, RiskAlert, RiskLevel


_RANK = {RiskLevel.FOCUS: 1, RiskLevel.OBSERVE: 2, RiskLevel.NEUTRAL: 0, RiskLevel.HIGH_RISK: 3}


class AlertService:
    def __init__(self, *, dedupe_hours: float = 24.0, clock=None):
        self.dedupe_window = timedelta(hours=dedupe_hours)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._active: dict[tuple[str, str], RiskAlert] = {}
        self._history: list[RiskAlert] = []

    def hydrate(self, alerts: list[RiskAlert]) -> None:
        """Load persisted alert history and rebuild the active-alert index."""

        self._history = list(alerts)
        self._active = {}
        for alert in sorted(alerts, key=lambda item: item.triggered_at):
            key = (alert.subject, alert.reason_code)
            if alert.status is AlertStatus.ACTIVE:
                self._active[key] = alert
            elif alert.status is AlertStatus.RESOLVED:
                self._active.pop(key, None)

    def upsert(self, review: ReviewResult, *, at: datetime | None = None) -> RiskAlert | None:
        if review.level is RiskLevel.NEUTRAL:
            return None
        at = at or self.clock()
        key = (review.subject, review.reason_code)
        previous = self._active.get(key)
        if previous is not None:
            if _RANK[review.level] <= _RANK[previous.level] and at - previous.triggered_at < self.dedupe_window:
                return None
        alert = RiskAlert(subject=review.subject, level=review.level, reason_code=review.reason_code,
                          triggered_at=at, summary=review.summary, evidence_ids=review.evidence_ids,
                          uncertainty=review.uncertainty)
        self._active[key] = alert
        self._history.append(alert)
        return alert

    def resolve(self, subject: str, reason_code: str, at: datetime, *, summary: str = "风险已恢复") -> RiskAlert | None:
        key = (subject, reason_code)
        alert = self._active.pop(key, None)
        if alert is None:
            return None
        resolved = alert.model_copy(update={"id": uuid4().hex, "status": AlertStatus.RESOLVED, "triggered_at": at, "summary": summary})
        self._history.append(resolved)
        return resolved

    def history(self, subject: str | None = None) -> list[RiskAlert]:
        if subject is None:
            return list(self._history)
        return [alert for alert in self._history if alert.subject == subject]

    def list_alerts(self, subject: str | None = None, *, active_only: bool = False) -> list[RiskAlert]:
        if active_only:
            return [alert for (alert_subject, _), alert in self._active.items()
                    if subject is None or alert_subject == subject]
        return self.history(subject)
