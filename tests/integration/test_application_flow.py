from datetime import datetime, timezone

import pytest

from fund_agent.application import FundAgentApplication
from fund_agent.config.settings import Settings
from fund_agent.domain.models import Evidence, FundAnalysis, FundShare, HoldingSnapshot, NavMetrics, SourceType
from fund_agent.persistence.repository import InMemoryRepository


NOW = datetime(2026, 8, 28, 16, 30, tzinfo=timezone.utc)


class MarketSource:
    source_type = SourceType.MARKET

    def fetch(self, subject: str, since=None):
        return [
            Evidence(
                source_type=SourceType.MARKET,
                subject=subject,
                collected_at=NOW,
                confidence=1,
                content="正式净值",
                url="https://example.test/nav",
                metadata={"nav": [10.0, 11.0, 10.5], "latest_value": 10.5},
            )
        ]


def test_application_tracking_persists_nav_history_for_portfolio_analysis():
    repository = InMemoryRepository()
    app = FundAgentApplication(
        repository=repository,
        source_adapters=[MarketSource()],
        settings=Settings.from_env({}),
        clock=lambda: NOW,
    )
    fund = FundShare(code="000001", name="示例基金 C", share_class="C")
    app.add_fund(fund)
    app.add_holding(HoldingSnapshot(fund=fund, units=10, invested=95, as_of=NOW))

    tracking = app.run_tracking()
    portfolio = app.analyze_portfolio()

    assert tracking.evidence[0].metadata["nav"] == [10.0, 11.0, 10.5]
    assert portfolio.total_value == 105
    assert portfolio.metrics.max_drawdown == pytest.approx(10.5 / 11.0 - 1)


def test_application_can_run_tracking_from_repository_candidates_without_manual_payload():
    repository = InMemoryRepository()
    app = FundAgentApplication(
        repository=repository,
        source_adapters=[MarketSource()],
        settings=Settings.from_env({"FUND_AGENT_DRAWDOWN_THRESHOLD": "-0.04"}),
        clock=lambda: NOW,
    )
    app.add_fund(FundShare(code="000001"))

    result = app.run_tracking()

    assert result.subjects == ["000001"]
    assert result.source_statuses["market"].evidence_count == 1
    assert result.reviews["000001"].reason_code == "drawdown"


def test_application_run_tracking_uses_supplied_evidence_for_review():
    app = FundAgentApplication(repository=InMemoryRepository(), source_adapters=[], clock=lambda: NOW)
    app.add_fund(FundShare(code="000001"))
    evidence = Evidence(
        source_type=SourceType.OFFICIAL,
        subject="000001",
        collected_at=NOW,
        confidence=0.95,
        content="基金经理变更公告",
        url="https://example.test/notice",
    )

    result = app.run_tracking([FundAnalysis(fund=FundShare(code="000001"), metrics=NavMetrics())], [evidence])

    assert result.reviews["000001"].reason_code == "official_notice"
    assert result.alerts[0].evidence_ids


def test_portfolio_uses_latest_available_nav_and_ignores_stale_history():
    repository = InMemoryRepository()
    app = FundAgentApplication(repository=repository, source_adapters=[], clock=lambda: NOW)
    fund = FundShare(code="000001")
    app.add_holding(HoldingSnapshot(fund=fund, units=10, invested=100, as_of=NOW))
    repository.save_evidence(
        Evidence(
            source_type=SourceType.MARKET,
            subject="000001",
            collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
            confidence=1,
            status="stale",
            metadata={"nav": [10, 20], "latest_value": 20},
        )
    )
    repository.save_evidence(
        Evidence(
            source_type=SourceType.MARKET,
            subject="000001",
            collected_at=NOW,
            confidence=1,
            metadata={"nav": [10, 11], "latest_value": 11},
        )
    )

    portfolio = app.analyze_portfolio()

    assert portfolio.total_value == 110


def test_screening_derives_missing_metrics_from_tracked_market_evidence():
    repository = InMemoryRepository()
    app = FundAgentApplication(repository=repository, source_adapters=[], clock=lambda: NOW)
    fund = FundShare(code="000001")
    repository.save_evidence(
        Evidence(
            source_type=SourceType.MARKET,
            subject="000001",
            collected_at=NOW,
            confidence=1,
            metadata={"nav": [1.0, 1.1, 1.05]},
        )
    )

    result = app.screen([FundAnalysis(fund=fund, metrics=NavMetrics())])

    assert result[0].components["return"] > 0
    assert not any("指标不足" in warning for warning in result[0].warnings)
