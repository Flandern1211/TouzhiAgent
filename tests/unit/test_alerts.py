from datetime import datetime, timedelta, timezone

from fund_agent.agent.reviewer import EvidenceReviewer, ReviewResult
from fund_agent.alerts.service import AlertService
from fund_agent.domain.models import AlertStatus, Evidence, FundAnalysis, FundShare, NavMetrics, RiskLevel, SourceType
from fund_agent.tracking.rules import RuleEngine, RuleHit


NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def ev(kind, confidence=.9, content="公告说明"):
    return Evidence(source_type=kind, subject="000001", collected_at=NOW, confidence=confidence,
                    content=content, url="https://origin.test/item")


def test_sentiment_only_evidence_cannot_produce_high_risk():
    fund = FundAnalysis(fund=FundShare(code="000001"), metrics=NavMetrics())
    hits = RuleEngine().evaluate(fund, [ev(SourceType.SENTIMENT, content="传闻")])
    review = EvidenceReviewer().review("000001", hits, [ev(SourceType.SENTIMENT, content="传闻")])
    assert review.level is not RiskLevel.HIGH_RISK
    assert review.uncertainty


def test_high_severity_without_usable_evidence_is_downgraded():
    hit = RuleEngine().evaluate(
        FundAnalysis(fund=FundShare(code="000001"), metrics=NavMetrics(max_drawdown=-0.3)),
        [],
    )

    review = EvidenceReviewer().review("000001", hit, [])

    assert review.level is not RiskLevel.HIGH_RISK
    assert review.uncertainty


def test_official_notice_can_produce_high_risk_with_corroboration():
    fund = FundAnalysis(fund=FundShare(code="000001"), metrics=NavMetrics(max_drawdown=-.3))
    evidence = [ev(SourceType.MARKET), ev(SourceType.OFFICIAL)]
    review = EvidenceReviewer().review("000001", RuleEngine().evaluate(fund, evidence), evidence)
    assert review.level is RiskLevel.HIGH_RISK
    assert review.evidence_ids


def test_reviewer_assigns_stable_evidence_references_when_source_ids_are_missing():
    fund = FundAnalysis(fund=FundShare(code="000001"), metrics=NavMetrics(max_drawdown=-.3))
    evidence = [ev(SourceType.MARKET), ev(SourceType.OFFICIAL)]
    review = EvidenceReviewer().review("000001", RuleEngine().evaluate(fund, evidence), evidence)
    assert review.evidence_ids
    assert all(reference.startswith("evidence:") for reference in review.evidence_ids)


def test_reviewer_preserves_evidence_input_order_for_traceable_references():
    fund = FundAnalysis(fund=FundShare(code="000001"), metrics=NavMetrics(max_drawdown=-.3))
    evidence = [
        ev(SourceType.OFFICIAL, content="基金经理变更公告").model_copy(update={"id": "official-1"}),
        ev(SourceType.MARKET, content="正式净值").model_copy(update={"id": "market-1"}),
    ]
    hits = RuleEngine().evaluate(fund, evidence)

    review = EvidenceReviewer().review("000001", hits, evidence)

    assert review.evidence_ids == ["official-1", "market-1"]


def test_alert_service_deduplicates_for_24h_and_escalates():
    service = AlertService()
    fund = FundAnalysis(fund=FundShare(code="000001"), metrics=NavMetrics(max_drawdown=-.3))
    evidence = [ev(SourceType.MARKET), ev(SourceType.OFFICIAL)]
    review = EvidenceReviewer().review("000001", RuleEngine().evaluate(fund, evidence), evidence)
    first = service.upsert(review, at=NOW)
    assert first is not None
    assert service.upsert(review, at=NOW + timedelta(hours=1)) is None
    review.level = RiskLevel.HIGH_RISK
    review.reason_code = "official_notice"
    escalated = service.upsert(review, at=NOW + timedelta(hours=2))
    assert escalated is not None
    assert escalated.level is RiskLevel.HIGH_RISK


def test_alert_service_escalates_across_reason_codes_for_same_subject():
    service = AlertService()
    first = service.upsert(
        EvidenceReviewer().review("000001", [], []), at=NOW
    )
    assert first is None
    from fund_agent.agent.reviewer import ReviewResult
    service.upsert(ReviewResult(subject="000001", level=RiskLevel.OBSERVE,
                                reason_code="drawdown", summary="回撤"), at=NOW)
    escalated = service.upsert(ReviewResult(subject="000001", level=RiskLevel.HIGH_RISK,
                                            reason_code="official_notice", summary="公告"),
                               at=NOW + timedelta(hours=1))
    assert escalated is not None
    assert escalated.level is RiskLevel.HIGH_RISK


def test_alert_service_resolves_active_alert_and_keeps_history():
    service = AlertService()
    review = EvidenceReviewer().review("000001", [], [])
    review.level = RiskLevel.OBSERVE
    review.reason_code = "drawdown"
    service.upsert(review, at=NOW)
    resolved = service.resolve("000001", "drawdown", NOW + timedelta(hours=3))
    assert resolved is not None and resolved.status.value == "resolved"
    assert resolved.id != service.history("000001")[0].id
    assert service.history("000001")[-1].status.value == "resolved"


def test_alert_service_hydrates_persisted_active_and_resolved_history():
    service = AlertService()
    active = service.upsert(
        ReviewResult(subject="000001", level=RiskLevel.OBSERVE, reason_code="drawdown", summary="回撤"),
        at=NOW,
    )
    assert active is not None
    resolved = active.model_copy(update={"status": AlertStatus.RESOLVED, "triggered_at": NOW + timedelta(hours=1)})

    restored = AlertService()
    restored.hydrate([active, resolved])

    assert restored.list_alerts("000001", active_only=True) == []


def test_reviewer_downgrades_high_severity_hit_without_usable_evidence():
    hit = RuleHit(subject="000001", reason_code="drawdown", severity=4, summary="回撤")
    failed = ev(SourceType.MARKET).model_copy(update={"status": "failed"})
    stale = ev(SourceType.OFFICIAL).model_copy(update={"status": "stale"})

    review = EvidenceReviewer().review("000001", [hit], [failed, stale])

    assert review.level is not RiskLevel.HIGH_RISK
    assert review.uncertainty
