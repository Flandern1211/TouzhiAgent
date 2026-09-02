from datetime import datetime, timedelta, timezone

from fund_agent.domain.models import Evidence, FundAnalysis, FundShare, NavMetrics, SourceType
from fund_agent.tracking.rules import RuleEngine


NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def analysis(**kwargs):
    return FundAnalysis(
        fund=FundShare(code="000001", name="测试基金"),
        metrics=NavMetrics(**kwargs),
    )


def evidence(source_type, *, confidence=.9, collected_at=NOW, content="事实"):
    return Evidence(source_type=source_type, subject="000001", collected_at=collected_at,
                    confidence=confidence, content=content, url="https://origin.test/item")


def test_rules_hit_nav_drop_drawdown_and_volatility_thresholds():
    hits = RuleEngine().evaluate(
        analysis(total_return=-.08, max_drawdown=-.25, volatility=.35),
        [evidence(SourceType.MARKET)],
    )
    assert {hit.reason_code for hit in hits} >= {"nav_drop", "drawdown", "volatility"}
    assert all("000001" in hit.subject for hit in hits)


def test_rules_mark_stale_data_and_official_notice():
    stale = evidence(SourceType.MARKET, collected_at=NOW - timedelta(hours=30))
    notice = evidence(SourceType.OFFICIAL, content="基金经理变更公告")
    hits = RuleEngine().evaluate(analysis(), [stale, notice])
    assert {hit.reason_code for hit in hits} == {"stale_data", "official_notice"}


def test_ordinary_official_announcement_does_not_trigger_official_notice_rule():
    ordinary = evidence(SourceType.OFFICIAL, content="基金定期报告公告", confidence=1.0)

    hits = RuleEngine().evaluate(analysis(), [ordinary])

    assert "official_notice" not in {hit.reason_code for hit in hits}


def test_official_risk_metadata_triggers_official_notice_rule():
    flagged = evidence(SourceType.OFFICIAL, content="公告说明")
    flagged.metadata["risk"] = True

    hits = RuleEngine(clock=lambda: NOW).evaluate(analysis(), [flagged])

    assert [hit.reason_code for hit in hits] == ["official_notice"]


def test_rules_accept_settings_backed_thresholds():
    engine = RuleEngine(settings={"nav_drop_threshold": -.10, "stale_hours": 2})
    fresh = evidence(SourceType.MARKET, collected_at=NOW - timedelta(hours=3))
    assert [h.reason_code for h in engine.evaluate(analysis(total_return=-.08), [fresh])] == ["stale_data"]
