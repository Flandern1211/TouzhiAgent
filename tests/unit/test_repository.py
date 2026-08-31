from datetime import datetime, timedelta, timezone

from fund_agent.domain.models import (AlertStatus, Evidence, FundShare, HoldingSnapshot,
                                      RiskAlert, RiskLevel, SourceType)
from fund_agent.persistence.repository import InMemoryRepository


def _fund(code="000001"):
    return FundShare(code=code, name=f"基金-{code}")


def test_in_memory_repository_round_trips_funds_snapshots_evidence_and_alerts():
    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    fund = _fund()
    snapshot = HoldingSnapshot(fund=fund, amount=1000, invested=900, as_of=now)
    evidence = Evidence(source_type=SourceType.MARKET, subject=fund.code, collected_at=now,
                        content="nav", confidence=1)
    alert = RiskAlert(subject=fund.code, level=RiskLevel.OBSERVE, reason_code="nav_drop",
                      triggered_at=now, summary="drop")

    repo.save_fund(fund)
    repo.save_snapshot(snapshot)
    evidence = repo.save_evidence(evidence)
    alert = repo.save_alert(alert)

    assert repo.list_funds() == [fund]
    assert repo.latest_snapshots()[0] == snapshot
    assert repo.list_evidence(fund.code) == [evidence]
    assert repo.list_alerts(fund.code) == [alert]


def test_latest_snapshots_selects_latest_per_fund_and_filters_subject():
    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    repo.save_snapshot(HoldingSnapshot(fund=_fund(), amount=1, invested=1, as_of=now - timedelta(days=1)))
    latest = HoldingSnapshot(fund=_fund(), amount=2, invested=1, as_of=now)
    repo.save_snapshot(latest)
    repo.save_snapshot(HoldingSnapshot(fund=_fund("000002"), amount=3, invested=1, as_of=now))

    assert repo.latest_snapshots("000001") == [latest]
    assert {item.fund.code for item in repo.latest_snapshots()} == {"000001", "000002"}


def test_repository_assigns_stable_ids_and_can_filter_alert_status():
    repo = InMemoryRepository()
    now = datetime.now(timezone.utc)
    evidence = repo.save_evidence(Evidence(source_type=SourceType.NEWS, subject="000001",
                                           collected_at=now, confidence=0))
    alert = repo.save_alert(RiskAlert(subject="000001", level=RiskLevel.HIGH_RISK, reason_code="x",
                                      triggered_at=now, summary="x", status=AlertStatus.RESOLVED))

    assert evidence.id
    assert alert.id
    assert repo.list_alerts(status=AlertStatus.RESOLVED) == [alert]


def test_repository_can_delete_a_candidate_without_deleting_history():
    repo = InMemoryRepository()
    fund = _fund()
    repo.save_fund(fund)

    assert repo.delete_fund(fund.code) is True
    assert repo.list_funds() == []
    assert repo.delete_fund(fund.code) is False


def test_repository_keeps_same_code_share_classes_separate():
    repo = InMemoryRepository()
    repo.save_fund(FundShare(code="000001", product_id="product-1", share_class="A"))
    repo.save_fund(FundShare(code="000001", product_id="product-1", share_class="C"))

    assert {fund.share_class for fund in repo.list_funds()} == {"A", "C"}
