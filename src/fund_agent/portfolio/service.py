from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import prod

from pydantic import BaseModel, Field

from fund_agent.analytics.metrics import compute_nav_metrics, compute_portfolio_returns
from fund_agent.domain.models import FundShare, HoldingSnapshot, NavMetrics, RiskLevel


class HoldingAnalysis(BaseModel):
    fund: FundShare
    value: float
    invested: float
    gain: float
    units: float | None = None
    weight: float = 0.0
    contribution: float = 0.0
    warnings: list[str] = Field(default_factory=list)

    @property
    def current_value(self) -> float:
        return self.value


class PortfolioAnalysis(BaseModel):
    total_value: float = 0.0
    total_invested: float = 0.0
    total_gain: float = 0.0
    return_rate: float | None = None
    holdings: list[HoldingAnalysis] = Field(default_factory=list)
    concentration: float | None = None
    metrics: NavMetrics = Field(default_factory=NavMetrics)
    risk_level: RiskLevel = RiskLevel.NEUTRAL
    warnings: list[str] = Field(default_factory=list)
    risk_reasons: list[str] = Field(default_factory=list)

    @property
    def total_return(self) -> float | None:
        return self.return_rate

    @property
    def portfolio_metrics(self) -> NavMetrics:
        return self.metrics


class PortfolioService:
    def analyze(self, snapshot: Sequence[HoldingSnapshot], latest_values: Mapping[str, float], history: Mapping[str, Sequence[float]]) -> PortfolioAnalysis:
        warnings: list[str] = []
        rows: list[HoldingAnalysis] = []
        for holding in snapshot:
            code = holding.fund.code
            identity = holding.fund.identity_key
            value = holding.manual_value
            latest = latest_values.get(identity, latest_values.get(code))
            if value is None and holding.units is not None and latest is not None:
                value = holding.units * float(latest)
            if value is None and holding.amount is not None:
                value = holding.amount
            if value is None:
                warning = f"缺少 {code} 的当前估值"
                warnings.append(warning)
                rows.append(HoldingAnalysis(fund=holding.fund, value=0, invested=holding.invested, gain=-holding.invested, units=holding.units, warnings=[warning]))
            else:
                rows.append(HoldingAnalysis(fund=holding.fund, value=float(value), invested=holding.invested, gain=float(value) - holding.invested, units=holding.units))
        total_value = sum(row.value for row in rows)
        total_invested = sum(row.invested for row in rows)
        for row in rows:
            row.weight = row.value / total_value if total_value else 0.0
            price_history = history.get(row.fund.identity_key, history.get(row.fund.code))
            period_gain = 0.0
            if price_history and len(price_history) >= 2 and price_history[0] > 0 and price_history[-1] > 0:
                if row.units is not None:
                    period_gain = row.units * (float(price_history[-1]) - float(price_history[0]))
                else:
                    period_return = float(price_history[-1]) / float(price_history[0]) - 1.0
                    period_gain = row.value * period_return / (1.0 + period_return)
            row.contribution = period_gain / total_value if total_value else 0.0
        total_gain = total_value - total_invested
        return_rate = total_gain / total_invested if total_invested else None
        concentration = sum(row.weight ** 2 for row in rows) if total_value else None
        weights = {
            row.fund.identity_key: row.value
            for row in rows
            if row.value > 0 and (row.fund.identity_key in history or row.fund.code in history)
        }
        series: dict[str, list[float]] = {}
        for code in weights:
            values = history.get(code)
            if values is None:
                values = history.get(code.split(":", 1)[0])
            if values is None or len(values) < 2 or not all(float(value) > 0 for value in values):
                continue
            series[code] = [float(values[index]) / float(values[index - 1]) - 1.0 for index in range(1, len(values))]
        if len(series) < len([row for row in rows if row.value > 0]):
            warnings.append("部分持仓缺少历史净值，组合风险指标可能不完整")
        portfolio_returns = compute_portfolio_returns(weights, series) if series else []
        metrics = compute_nav_metrics([1.0] + [prod(1 + x for x in portfolio_returns[:i + 1]) for i in range(len(portfolio_returns))]) if portfolio_returns else NavMetrics()
        reasons = []
        if metrics.max_drawdown is not None and metrics.max_drawdown <= -.2:
            reasons.append("组合最大回撤较高")
        if concentration is not None and concentration >= .5:
            reasons.append("组合持仓集中度较高")
        risk = RiskLevel.HIGH_RISK if (metrics.max_drawdown is not None and metrics.max_drawdown <= -.3) else RiskLevel.OBSERVE if reasons else RiskLevel.NEUTRAL
        return PortfolioAnalysis(total_value=total_value, total_invested=total_invested, total_gain=total_gain, return_rate=return_rate, holdings=rows, concentration=concentration, metrics=metrics, risk_level=risk, warnings=warnings, risk_reasons=reasons)
