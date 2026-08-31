from datetime import datetime, time, timezone

from fund_agent.config.settings import Settings
from fund_agent.tracking.scheduler import TrackingScheduler, is_full_analysis_due


def test_full_analysis_is_due_only_on_configured_weekday_after_schedule():
    settings = Settings.from_env({"FUND_AGENT_FULL_ANALYSIS_TIME": "16:30"})

    # 08:31 UTC is 16:31 in Asia/Shanghai on Friday.
    assert is_full_analysis_due(datetime(2026, 8, 28, 8, 31, tzinfo=timezone.utc), settings)
    assert not is_full_analysis_due(datetime(2026, 8, 28, 8, 29, tzinfo=timezone.utc), settings)
    assert not is_full_analysis_due(datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc), settings)


def test_scheduler_run_once_invokes_callback_and_can_stop():
    calls: list[str] = []
    scheduler = TrackingScheduler(lambda: calls.append("run"), interval_seconds=60)

    scheduler.run_once()
    scheduler.stop()

    assert calls == ["run"]
    assert not scheduler.running


def test_scheduler_tick_runs_evidence_interval_and_full_analysis_once_per_day():
    settings = Settings.from_env(
        {
            "FUND_AGENT_EVIDENCE_INTERVAL_HOURS": "4",
            "FUND_AGENT_FULL_ANALYSIS_TIME": "16:30",
        }
    )
    events: list[str] = []
    scheduler = TrackingScheduler(
        lambda: events.append("evidence"),
        full_analysis_callback=lambda: events.append("full"),
        settings=settings,
    )

    scheduler.tick(datetime(2026, 8, 28, 4, 0, tzinfo=timezone.utc))
    scheduler.tick(datetime(2026, 8, 28, 7, 0, tzinfo=timezone.utc))
    scheduler.tick(datetime(2026, 8, 28, 8, 31, tzinfo=timezone.utc))
    scheduler.tick(datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc))

    assert events == ["evidence", "full"]
