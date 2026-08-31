from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from fund_agent.domain.models import (
    AlertStatus,
    Evidence,
    EvidenceStatus,
    FundAnalysis,
    FundShare,
    HoldingSnapshot,
    NavMetrics,
    RiskAlert,
    RiskLevel,
    SourceType,
)


def test_fund_share_requires_six_digit_code_and_preserves_share_class():
    fund = FundShare(code=" 000001 ", product_id="000001", name="示例基金 C", share_class="C")

    assert fund.code == "000001"
    assert fund.share_class == "C"


def test_fund_share_rejects_non_numeric_code():
    with pytest.raises(ValidationError):
        FundShare(code="ABC", name="无效基金")


def test_holding_snapshot_requires_amount_or_units_and_positive_investment():
    fund = FundShare(code="000001")
    timestamp = datetime(2026, 8, 28, tzinfo=timezone.utc)

    holding = HoldingSnapshot(
        fund=fund,
        amount=1000,
        invested=900,
        as_of=timestamp,
    )

    assert holding.amount == 1000
    assert holding.units is None

    with pytest.raises(ValidationError):
        HoldingSnapshot(fund=fund, invested=900, as_of=timestamp)

    with pytest.raises(ValidationError):
        HoldingSnapshot(fund=fund, amount=1000, invested=0, as_of=timestamp)


def test_evidence_validates_confidence_and_keeps_status():
    evidence = Evidence(
        source_type=SourceType.OFFICIAL,
        subject="000001",
        collected_at=datetime.now(timezone.utc),
        url="https://example.test/notice",
        content="公告内容",
        confidence=0.95,
        status=EvidenceStatus.AVAILABLE,
    )

    assert evidence.status is EvidenceStatus.AVAILABLE
    assert evidence.confidence == 0.95

    with pytest.raises(ValidationError):
        Evidence(
            source_type=SourceType.NEWS,
            subject="000001",
            collected_at=datetime.now(timezone.utc),
            confidence=1.5,
        )


def test_analysis_and_alert_models_are_serializable():
    fund = FundShare(code="000001", name="示例基金")
    metrics = NavMetrics(total_return=0.12, max_drawdown=-0.08, volatility=0.16, sharpe=0.7, calmar=1.5)
    analysis = FundAnalysis(fund=fund, metrics=metrics, quality_score=0.9)
    alert = RiskAlert(
        subject="000001",
        level=RiskLevel.HIGH_RISK,
        reason_code="drawdown",
        triggered_at=datetime.now(timezone.utc),
        summary="回撤超过阈值",
        evidence_ids=["e1"],
        status=AlertStatus.ACTIVE,
    )

    assert analysis.model_dump()["fund"]["code"] == "000001"
    assert alert.model_dump()["status"] == "active"


def test_domain_timestamps_are_normalized_to_aware_utc():
    naive = datetime(2026, 8, 28, 16, 30)
    fund = FundShare(code="000001")

    holding = HoldingSnapshot(fund=fund, amount=100, invested=100, as_of=naive)
    evidence = Evidence(source_type=SourceType.MARKET, subject="000001", collected_at=naive, confidence=1)

    assert holding.as_of.tzinfo is timezone.utc
    assert evidence.collected_at.tzinfo is timezone.utc


def test_fund_identity_key_keeps_share_classes_distinct():
    product_a = FundShare(code="000001", product_id="product-1", share_class="A")
    product_c = FundShare(code="000001", product_id="product-1", share_class="C")

    assert product_a.identity_key != product_c.identity_key
