from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, Field

from fund_agent.domain.models import FundAnalysis, FundShare, RiskLevel


class PreferenceProfile(BaseModel):
    risk: str = "balanced"
    horizon: str = "long_term"


class ScreeningResult(BaseModel):
    fund: FundShare
    objective_score: float = Field(ge=0, le=1)
    personalized_score: float = Field(ge=0, le=1)
    label: RiskLevel
    components: dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    risk_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    objective_rank: int = 0
    personalized_rank: int = 0

    @property
    def score(self) -> float:
        return self.personalized_score

    @property
    def rank(self) -> int:
        return self.personalized_rank


_DEFAULT_WEIGHTS = {"quality": .20, "return": .30, "drawdown": .15, "volatility": .10, "sharpe": .15, "calmar": .10}
_RISK_WEIGHTS = {
    "balanced": _DEFAULT_WEIGHTS,
    "conservative": {"quality": .25, "return": .10, "drawdown": .30, "volatility": .25, "sharpe": .05, "calmar": .05},
    "aggressive": {"quality": .15, "return": .35, "drawdown": .10, "volatility": .10, "sharpe": .20, "calmar": .10},
}
_HORIZON_ADJUSTMENTS = {
    "short_term": {"return": .05, "drawdown": .05, "volatility": .05},
    "long_term": {"return": .05, "drawdown": -.02, "volatility": -.03},
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


class ScreeningService:
    def __init__(self, *, weights: Mapping[str, float] | None = None, thresholds: Mapping[str, float] | None = None):
        self.weights = dict(weights or _DEFAULT_WEIGHTS)
        self.thresholds = {"focus": .75, "observe": .55, "neutral": .35, **(thresholds or {})}

    @staticmethod
    def _components(fund: FundAnalysis) -> dict[str, float | None]:
        m = fund.metrics
        return {
            "quality": fund.quality_score,
            "return": None if m.total_return is None else _clamp((m.total_return + 1) / 2),
            "drawdown": None if m.max_drawdown is None else _clamp(1 + m.max_drawdown),
            "volatility": None if m.volatility is None else 1 / (1 + max(0, m.volatility) * 5),
            "sharpe": None if m.sharpe is None else _clamp((m.sharpe + 2) / 4),
            "calmar": None if m.calmar is None else _clamp(m.calmar / 3),
        }

    @staticmethod
    def _score(components: Mapping[str, float | None], weights: Mapping[str, float]) -> float:
        available = [(key, value, weights.get(key, 0.0)) for key, value in components.items() if value is not None and weights.get(key, 0) > 0]
        total = sum(weight for _, _, weight in available)
        return 0.0 if not total else _clamp(sum(float(value) * weight for _, value, weight in available) / total)

    def _personalized_weights(self, preference: PreferenceProfile) -> dict[str, float]:
        weights = dict(_RISK_WEIGHTS.get(preference.risk, _RISK_WEIGHTS["balanced"]))
        for key, adjustment in _HORIZON_ADJUSTMENTS.get(preference.horizon, {}).items():
            weights[key] = max(0.0, weights.get(key, 0) + adjustment)
        return weights

    def _label(self, score: float, components: Mapping[str, float | None] | None = None) -> RiskLevel:
        if components and components.get("return") is not None and components.get("sharpe") is not None and components["return"] < .5 and components["sharpe"] < .5:
            return RiskLevel.HIGH_RISK
        if score >= self.thresholds["focus"]:
            return RiskLevel.FOCUS
        if score >= self.thresholds["observe"]:
            return RiskLevel.OBSERVE
        if score >= self.thresholds["neutral"]:
            return RiskLevel.NEUTRAL
        return RiskLevel.HIGH_RISK

    def rank(self, funds: Sequence[FundAnalysis], preference: PreferenceProfile | None = None) -> list[ScreeningResult]:
        profile = preference or PreferenceProfile()
        prepared = []
        for fund in funds:
            raw = self._components(fund)
            objective = self._score(raw, self.weights)
            personalized = objective if preference is None else self._score(raw, self._personalized_weights(profile))
            reasons = []
            risk_reasons = []
            for key, value in raw.items():
                if value is not None and value >= .7:
                    reasons.append(f"{key}表现较好")
            if raw["quality"] is not None and raw["quality"] < .5:
                risk_reasons.append("质量评分偏低")
            if raw["return"] is not None and raw["return"] < .5:
                risk_reasons.append("收益偏低")
            if raw["drawdown"] is not None and raw["drawdown"] < .8:
                risk_reasons.append("最大回撤较高")
            if raw["volatility"] is not None and raw["volatility"] < 1 / 2:
                risk_reasons.append("波动率较高")
            if raw["sharpe"] is not None and raw["sharpe"] < .5:
                risk_reasons.append("夏普比率偏低")
            if raw["calmar"] is not None and raw["calmar"] < 1 / 3:
                risk_reasons.append("卡玛比率偏低")
            warnings = list(fund.warnings)
            missing = [key for key, value in raw.items() if value is None]
            if missing:
                warnings.append("指标缺失: " + ", ".join(missing))
            insufficient = all(raw[key] is None for key in ("return", "drawdown", "volatility", "sharpe", "calmar"))
            if insufficient:
                warnings.append("指标不足，暂不形成关注建议")
            label = RiskLevel.NEUTRAL if insufficient else self._label(personalized, raw)
            if label is RiskLevel.HIGH_RISK and not risk_reasons:
                risk_reasons.append("综合评分偏低")
            prepared.append((fund, objective, personalized, raw, reasons, risk_reasons, warnings, label))
        objective_order = sorted(prepared, key=lambda item: (-item[1], item[0].fund.code))
        personalized_order = sorted(prepared, key=lambda item: (-item[2], item[0].fund.code))
        objective_ranks = {id(item): index for index, item in enumerate(objective_order, 1)}
        personalized_ranks = {id(item): index for index, item in enumerate(personalized_order, 1)}
        return [ScreeningResult(fund=item[0].fund, objective_score=item[1], personalized_score=item[2], label=item[7], components={k: float(v) for k, v in item[3].items() if v is not None}, reasons=item[4], risk_reasons=item[5], warnings=item[6], evidence_ids=item[0].evidence_ids, objective_rank=objective_ranks[id(item)], personalized_rank=personalized_ranks[id(item)]) for item in personalized_order]
