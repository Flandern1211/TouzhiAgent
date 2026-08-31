import pytest

from fund_agent.domain.models import FundAnalysis, FundShare, NavMetrics, RiskLevel
from fund_agent.screening.service import PreferenceProfile, ScreeningService


def analysis(code: str, *, total=0.1, drawdown=-0.1, volatility=0.1, sharpe=1.0, quality=1.0):
    return FundAnalysis(
        fund=FundShare(code=code, name=f"基金 {code}"),
        metrics=NavMetrics(total_return=total, max_drawdown=drawdown, volatility=volatility, sharpe=sharpe),
        quality_score=quality,
    )


def test_rank_returns_objective_and_personalized_scores_with_deterministic_labels():
    results = ScreeningService().rank([analysis("000001", total=0.50, sharpe=1.5), analysis("000002", total=-0.05, sharpe=-0.5)])
    assert [item.fund.code for item in results] == ["000001", "000002"]
    assert results[0].objective_score > results[1].objective_score
    assert results[0].personalized_score == results[0].objective_score
    assert results[0].label is RiskLevel.FOCUS
    assert results[1].label is RiskLevel.HIGH_RISK
    assert results[0].reasons
    assert any("收益" in reason or "夏普" in reason for reason in results[1].risk_reasons)


def test_rank_personalization_changes_scores_without_changing_objective_score():
    funds = [analysis("000001", total=0.50, drawdown=-0.25, volatility=0.30), analysis("000002", total=0.02, drawdown=-0.05, volatility=0.08)]
    objective = ScreeningService().rank(funds)
    conservative = ScreeningService().rank(funds, PreferenceProfile(risk="conservative", horizon="short_term"))
    assert [x.fund.code for x in objective] != [x.fund.code for x in conservative]
    objective_by_code = {x.fund.code: x for x in objective}
    conservative_by_code = {x.fund.code: x for x in conservative}
    assert conservative_by_code["000002"].personalized_score > objective_by_code["000002"].personalized_score


def test_rank_accepts_configured_weights_and_thresholds():
    service = ScreeningService(weights={"quality": 1.0}, thresholds={"focus": 0.8, "observe": 0.5, "neutral": 0.2})
    result = service.rank([analysis("000001", quality=0.9)])[0]
    assert result.label is RiskLevel.FOCUS


def test_rank_custom_thresholds_change_the_label():
    fund = analysis("000001", total=0.50, sharpe=1.5)
    default_result = ScreeningService().rank([fund])[0]
    custom_result = ScreeningService(thresholds={"focus": 0.99, "observe": 0.80, "neutral": 0.60}).rank([fund])[0]

    assert default_result.label is RiskLevel.FOCUS
    assert custom_result.label is RiskLevel.OBSERVE


def test_rank_custom_weights_change_the_objective_score():
    fund = analysis("000001", total=0.8, quality=0.2)
    default_result = ScreeningService().rank([fund])[0]
    quality_result = ScreeningService(weights={"quality": 1.0}).rank([fund])[0]

    assert quality_result.objective_score == pytest.approx(0.2)
    assert quality_result.objective_score != default_result.objective_score


def test_high_risk_result_always_has_a_risk_reason():
    result = ScreeningService(thresholds={"focus": 0.95, "observe": 0.85, "neutral": 0.70}).rank(
        [analysis("000001", quality=0.0, total=0.2, drawdown=0.0, volatility=0.0, sharpe=1.0)]
    )[0]

    assert result.label is RiskLevel.HIGH_RISK
    assert any("质量" in reason for reason in result.risk_reasons)


def test_missing_performance_metrics_stays_neutral_instead_of_focus():
    fund = FundAnalysis(fund=FundShare(code="000001"), metrics=NavMetrics(), quality_score=1.0)

    result = ScreeningService().rank([fund])[0]

    assert result.label is RiskLevel.NEUTRAL
    assert any("指标不足" in warning for warning in result.warnings)
