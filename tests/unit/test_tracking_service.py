from __future__ import annotations

from datetime import datetime, timezone

from fund_agent.agent.reviewer import EvidenceReviewer
from fund_agent.alerts.service import AlertService
from fund_agent.agent.reviewer import ReviewResult
from fund_agent.domain.models import AlertStatus, Evidence, EvidenceStatus, FundAnalysis, FundShare, NavMetrics, SourceType
from fund_agent.persistence.repository import InMemoryRepository
from fund_agent.tracking.rules import RuleEngine
from fund_agent.tracking.service import TrackingService


NOW = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)


def analysis(code: str, *, drawdown: float | None = None) -> FundAnalysis:
    return FundAnalysis(fund=FundShare(code=code), metrics=NavMetrics(max_drawdown=drawdown))


class StaticSource:
    def __init__(self, source_type: SourceType, records: list[Evidence] | None = None, error: Exception | None = None):
        self.source_type = source_type
        self.records = records or []
        self.error = error
        self.calls: list[str] = []

    def fetch(self, subject: str, since=None) -> list[Evidence]:
        self.calls.append(subject)
        if self.error:
            raise self.error
        return [item.model_copy(update={"subject": subject}) for item in self.records]


def test_tracking_fetches_each_source_persists_evidence_and_creates_alerts():
    repository = InMemoryRepository()
    market = StaticSource(
        SourceType.MARKET,
        [Evidence(source_type=SourceType.MARKET, subject="000001", collected_at=NOW, confidence=1, content="nav")],
    )
    official = StaticSource(
        SourceType.OFFICIAL,
        [Evidence(source_type=SourceType.OFFICIAL, subject="000001", collected_at=NOW, confidence=.95, content="notice")],
    )
    service = TrackingService(
        repository=repository,
        source_adapters=[market, official],
        rule_engine=RuleEngine(clock=lambda: NOW),
        reviewer=EvidenceReviewer(),
        alert_service=AlertService(clock=lambda: NOW),
        clock=lambda: NOW,
    )

    result = service.run([analysis("000001", drawdown=-.3)])

    assert market.calls == ["000001"]
    assert official.calls == ["000001"]
    assert len(repository.list_evidence("000001")) == 2
    assert len(result.alerts) == 1
    assert result.alerts[0].level.value == "high_risk"
    assert result.source_statuses["market"].status == "available"
    assert result.source_statuses["official"].status == "available"


def test_tracking_isolates_source_failure_and_exposes_failed_status():
    repository = InMemoryRepository()
    failed = StaticSource(SourceType.NEWS, error=RuntimeError("news unavailable"))
    healthy = StaticSource(
        SourceType.MARKET,
        [Evidence(source_type=SourceType.MARKET, subject="000001", collected_at=NOW, confidence=1, content="nav")],
    )
    service = TrackingService(
        repository=repository,
        source_adapters={"news-feed": failed, "market-feed": healthy},
        rule_engine=RuleEngine(clock=lambda: NOW),
        reviewer=EvidenceReviewer(),
        alert_service=AlertService(clock=lambda: NOW),
        clock=lambda: NOW,
    )

    result = service.run([analysis("000001")])

    assert result.source_statuses["news-feed"].status == "failed"
    assert "news unavailable" in (result.source_statuses["news-feed"].error or "")
    assert result.source_statuses["market-feed"].status == "available"
    failed_records = [item for item in repository.list_evidence("000001") if item.source_type is SourceType.NEWS]
    assert failed_records and failed_records[0].status is EvidenceStatus.FAILED


def test_tracking_includes_repository_candidates_and_current_snapshot_funds():
    repository = InMemoryRepository()
    repository.save_fund(FundShare(code="000001"))
    source = StaticSource(SourceType.MARKET)
    service = TrackingService(
        repository=repository,
        source_adapters=[source],
        rule_engine=RuleEngine(clock=lambda: NOW),
        reviewer=EvidenceReviewer(),
        alert_service=AlertService(clock=lambda: NOW),
        clock=lambda: NOW,
    )

    result = service.run()

    assert source.calls == ["000001"]
    assert result.subjects == ["000001"]


def test_tracking_uses_latest_evidence_per_source_for_current_review():
    repository = InMemoryRepository()
    repository.save_fund(FundShare(code="000001"))
    repository.save_evidence(
        Evidence(
            source_type=SourceType.OFFICIAL,
            subject="000001",
            collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
            confidence=1,
            content="基金经理变更公告",
        )
    )
    repository.save_evidence(
        Evidence(
            source_type=SourceType.OFFICIAL,
            subject="000001",
            collected_at=NOW,
            confidence=1,
            content="基金定期报告公告",
        )
    )
    service = TrackingService(
        repository=repository,
        source_adapters=[],
        rule_engine=RuleEngine(clock=lambda: NOW),
        reviewer=EvidenceReviewer(),
        alert_service=AlertService(clock=lambda: NOW),
        clock=lambda: NOW,
    )

    result = service.run()

    assert result.reviews["000001"].reason_code == "none"


def test_tracking_marks_empty_source_results_as_failed_with_no_data_reason():
    repository = InMemoryRepository()
    source = StaticSource(SourceType.NEWS)
    service = TrackingService(
        repository=repository,
        source_adapters=[source],
        rule_engine=RuleEngine(clock=lambda: NOW),
        reviewer=EvidenceReviewer(),
        alert_service=AlertService(clock=lambda: NOW),
        clock=lambda: NOW,
    )

    result = service.run([analysis("000001")])

    source_status = result.source_statuses["news"]
    assert source_status.status is EvidenceStatus.FAILED
    assert "no data" in (source_status.error or "").lower()


def test_tracking_resolves_active_alert_when_rule_is_no_longer_hit():
    repository = InMemoryRepository()
    repository.save_evidence(
        Evidence(
            source_type=SourceType.MARKET,
            subject="000001",
            collected_at=NOW,
            confidence=1,
            content="fresh nav",
            metadata={"nav": [1.0, 1.01]},
        )
    )
    alert_service = AlertService(clock=lambda: NOW)
    active = alert_service.upsert(
        ReviewResult(subject="000001", level="observe", reason_code="drawdown", summary="回撤"),
        at=NOW,
    )
    assert active is not None
    service = TrackingService(
        repository=repository,
        source_adapters=[],
        rule_engine=RuleEngine(clock=lambda: NOW),
        reviewer=EvidenceReviewer(),
        alert_service=alert_service,
        clock=lambda: NOW,
    )

    result = service.run([analysis("000001")])

    assert len(result.alerts) == 1
    assert result.alerts[0].status is AlertStatus.RESOLVED
    assert not alert_service.list_alerts("000001", active_only=True)


def test_tracking_does_not_resolve_active_alert_when_source_failed():
    repository = InMemoryRepository()
    alert_service = AlertService(clock=lambda: NOW)
    alert_service.upsert(
        ReviewResult(subject="000001", level="observe", reason_code="official_notice", summary="公告风险"),
        at=NOW,
    )
    service = TrackingService(
        repository=repository,
        source_adapters=[StaticSource(SourceType.OFFICIAL, error=RuntimeError("offline"))],
        rule_engine=RuleEngine(clock=lambda: NOW),
        reviewer=EvidenceReviewer(),
        alert_service=alert_service,
        clock=lambda: NOW,
    )

    service.run([analysis("000001")])

    assert alert_service.list_alerts("000001", active_only=True)


def test_tracking_keeps_latest_record_per_provider_not_only_source_type():
    repository = InMemoryRepository()
    service = TrackingService(
        repository=repository,
        source_adapters=[],
        rule_engine=RuleEngine(clock=lambda: NOW),
        reviewer=EvidenceReviewer(),
        alert_service=AlertService(clock=lambda: NOW),
        clock=lambda: NOW,
    )
    evidence = [
        Evidence(source_type=SourceType.NEWS, subject="000001", collected_at=NOW, confidence=0.8, url="https://a.test/n", metadata={"provider": "a"}),
        Evidence(source_type=SourceType.NEWS, subject="000001", collected_at=NOW, confidence=0.8, url="https://b.test/n", metadata={"provider": "b"}),
    ]

    latest = service._latest_evidence(evidence)

    assert len(latest) == 2


def test_tracking_filters_failed_and_stale_records_without_hiding_usable_provider_data():
    service = TrackingService(
        repository=InMemoryRepository(),
        source_adapters=[],
        rule_engine=RuleEngine(clock=lambda: NOW),
        reviewer=EvidenceReviewer(),
        alert_service=AlertService(clock=lambda: NOW),
        clock=lambda: NOW,
    )
    old_usable = Evidence(
        source_type=SourceType.NEWS,
        subject="000001",
        collected_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        confidence=0.8,
        url="https://a.test/n",
        metadata={"provider": "a"},
    )
    newer_failed = old_usable.model_copy(update={
        "id": "a-failed",
        "collected_at": NOW,
        "status": EvidenceStatus.FAILED,
    })
    stale_other = Evidence(
        source_type=SourceType.NEWS,
        subject="000001",
        collected_at=NOW,
        confidence=0.8,
        url="https://b.test/n",
        metadata={"provider": "b"},
        status=EvidenceStatus.STALE,
    )
    fresh_other = stale_other.model_copy(update={"id": "b-fresh", "status": EvidenceStatus.AVAILABLE})

    latest = service._latest_evidence([old_usable, newer_failed, stale_other, fresh_other])

    assert latest == [old_usable, fresh_other]


def test_tracking_keeps_latest_provider_records_in_source_order():
    service = TrackingService(
        repository=InMemoryRepository(),
        source_adapters=[],
        rule_engine=RuleEngine(clock=lambda: NOW),
        reviewer=EvidenceReviewer(),
        alert_service=AlertService(clock=lambda: NOW),
        clock=lambda: NOW,
    )
    first_provider = Evidence(
        id="provider-a",
        source_type=SourceType.NEWS,
        subject="000001",
        collected_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        confidence=0.8,
        url="https://a.test/n",
        metadata={"provider": "a"},
    )
    second_provider = Evidence(
        id="provider-b",
        source_type=SourceType.NEWS,
        subject="000001",
        collected_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        confidence=0.8,
        url="https://b.test/n",
        metadata={"provider": "b"},
    )

    latest = service._latest_evidence([second_provider, first_provider])

    assert [item.id for item in latest] == ["provider-b", "provider-a"]


class SelectiveSource(StaticSource):
    def __init__(self, source_type: SourceType, records_by_subject: dict[str, list[Evidence]]):
        super().__init__(source_type)
        self.records_by_subject = records_by_subject

    def fetch(self, subject: str, since=None) -> list[Evidence]:
        self.calls.append(subject)
        return [item.model_copy(update={"subject": subject}) for item in self.records_by_subject.get(subject, [])]


def test_tracking_does_not_resolve_subject_when_its_source_returns_no_data():
    repository = InMemoryRepository()
    repository.save_evidence(Evidence(
        source_type=SourceType.MARKET,
        subject="000001",
        collected_at=NOW,
        confidence=1,
        content="cached nav",
    ))
    alert_service = AlertService(clock=lambda: NOW)
    alert_service.upsert(
        ReviewResult(subject="000001", level="observe", reason_code="drawdown", summary="回撤"),
        at=NOW,
    )
    source = SelectiveSource(SourceType.MARKET, {
        "000002": [Evidence(source_type=SourceType.MARKET, subject="000002", collected_at=NOW,
                             confidence=1, content="fresh nav")],
    })
    service = TrackingService(
        repository=repository,
        source_adapters=[source],
        rule_engine=RuleEngine(clock=lambda: NOW),
        reviewer=EvidenceReviewer(),
        alert_service=alert_service,
        clock=lambda: NOW,
    )

    service.run([analysis("000001"), analysis("000002")])

    assert alert_service.list_alerts("000001", active_only=True)
