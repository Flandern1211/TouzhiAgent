import math

import pytest

from fund_agent.analytics.metrics import (
    compute_drawdown,
    compute_nav_metrics,
    compute_portfolio_returns,
)


def test_compute_drawdown_reports_depth_and_recovery_indices():
    drawdown = compute_drawdown([1.0, 1.2, 0.9, 1.0, 1.25])

    assert drawdown.max_drawdown == pytest.approx(-0.25)
    assert drawdown.peak_index == 1
    assert drawdown.trough_index == 2
    assert drawdown.recovery_index == 4


def test_compute_nav_metrics_for_growing_series():
    metrics = compute_nav_metrics([1.0, 1.05, 1.02, 1.10], periods_per_year=3)

    assert metrics.total_return == pytest.approx(0.10)
    assert metrics.max_drawdown == pytest.approx(1.02 / 1.05 - 1)
    assert metrics.volatility is not None and metrics.volatility > 0
    assert metrics.sharpe is not None and math.isfinite(metrics.sharpe)
    assert metrics.calmar is not None and metrics.calmar > 0


def test_compute_nav_metrics_constant_and_missing_series_are_explicit():
    constant = compute_nav_metrics([1.0, 1.0, 1.0], periods_per_year=2)
    empty = compute_nav_metrics([])

    assert constant.volatility == 0
    assert constant.sharpe is None
    assert constant.calmar is None
    assert empty.total_return is None
    assert empty.max_drawdown is None


def test_compute_nav_metrics_rejects_non_positive_nav():
    with pytest.raises(ValueError, match="positive"):
        compute_nav_metrics([1.0, 0.0, 1.1])


def test_compute_portfolio_returns_aligns_common_periods_and_normalizes_weights():
    result = compute_portfolio_returns(
        {"a": 3, "b": 1},
        {"a": [0.10, -0.10, 0.05], "b": [0.0, 0.20]},
    )

    assert result == pytest.approx([0.075, -0.025])


def test_compute_portfolio_returns_rejects_missing_series():
    with pytest.raises(ValueError, match="missing returns"):
        compute_portfolio_returns({"a": 1, "b": 1}, {"a": [0.1]})
