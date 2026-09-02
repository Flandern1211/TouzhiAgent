from datetime import datetime, timezone

import pytest

from fund_agent.domain.models import FundShare, HoldingSnapshot, RiskLevel
from fund_agent.portfolio.service import PortfolioService


def holding(code, *, amount=None, units=None, invested=100, manual_value=None):
    return HoldingSnapshot(fund=FundShare(code=code), amount=amount, units=units, invested=invested, manual_value=manual_value, as_of=datetime.now(timezone.utc))


def test_analyze_values_positions_weights_contribution_and_concentration():
    result = PortfolioService().analyze(
        [holding("000001", units=10, invested=80), holding("000002", amount=100, invested=120)],
        latest_values={"000001": 12, "000002": 100},
        history={"000001": [10, 12], "000002": [100, 100]},
    )
    assert result.total_value == pytest.approx(220)
    assert result.total_invested == pytest.approx(200)
    assert result.total_gain == pytest.approx(20)
    assert result.return_rate == pytest.approx(0.1)
    assert result.holdings[0].weight == pytest.approx(120 / 220)
    assert result.holdings[0].contribution == pytest.approx(20 / 220)
    assert result.holdings[1].contribution == pytest.approx(0)
    assert result.concentration == pytest.approx((120 / 220) ** 2 + (100 / 220) ** 2)
    assert result.risk_level in RiskLevel


def test_analyze_uses_manual_value_and_warns_for_missing_values():
    result = PortfolioService().analyze(
        [holding("000001", units=10, invested=90), holding("000002", units=5, invested=50)],
        latest_values={"000001": 11}, history={"000001": [10, 11]},
    )
    assert result.total_value == pytest.approx(110)
    assert any("000002" in warning for warning in result.warnings)


def test_contribution_uses_each_holding_position_when_amount_and_units_are_mixed():
    result = PortfolioService().analyze(
        [holding("000002", amount=100, invested=120), holding("000001", units=10, invested=80)],
        latest_values={"000001": 12},
        history={"000001": [10, 12], "000002": [100, 120]},
    )

    assert result.holdings[0].contribution == pytest.approx((100 * (120 / 100 - 1) / (120 / 100)) / 220)
    assert result.holdings[1].contribution == pytest.approx(20 / 220)
