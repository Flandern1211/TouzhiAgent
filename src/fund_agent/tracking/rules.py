from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from fund_agent.domain.models import Evidence, EvidenceStatus, FundAnalysis, SourceType


class RuleHit(BaseModel):
    subject: str
    reason_code: str
    severity: int = Field(default=1, ge=1, le=4)
    summary: str
    value: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class RuleEngine:
    DEFAULTS = {
        "nav_drop_threshold": -0.05,
        "drawdown_threshold": -0.20,
        "volatility_threshold": 0.30,
        "stale_hours": 24.0,
        "official_confidence": 0.80,
    }
    _OFFICIAL_RISK_KEYWORDS = (
        "经理变更",
        "基金经理变更",
        "基金经理离任",
        "基金经理更换",
        "变更基金经理",
        "清盘",
        "清算",
        "终止运作",
        "暂停申购",
        "暂停赎回",
        "暂停大额申购",
        "重大违规",
        "违规处罚",
        "监管处罚",
        "立案调查",
        "巨额赎回",
        "manager change",
        "liquidation",
        "suspend subscription",
        "material violation",
        "regulatory penalty",
    )

    def __init__(self, *, settings: Any = None, clock=None):
        self.settings = dict(self.DEFAULTS)
        if settings is not None:
            values = settings if isinstance(settings, dict) else getattr(settings, "model_dump", lambda: vars(settings))()
            for key in self.settings:
                if key in values and values[key] is not None:
                    self.settings[key] = float(values[key])
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def evaluate(self, fund: FundAnalysis, evidence: list[Evidence] | tuple[Evidence, ...]) -> list[RuleHit]:
        subject = fund.fund.code
        evidence = list(evidence)
        market_ids = self._references(evidence, subject, lambda item: item.source_type is SourceType.MARKET)
        hits: list[RuleHit] = []
        metrics = fund.metrics
        if metrics.total_return is not None and metrics.total_return <= self.settings["nav_drop_threshold"]:
            hits.append(RuleHit(subject=subject, reason_code="nav_drop", severity=3,
                                summary=f"净值跌幅 {metrics.total_return:.1%} 超过阈值",
                                value=metrics.total_return, evidence_ids=market_ids))
        if metrics.max_drawdown is not None and metrics.max_drawdown <= self.settings["drawdown_threshold"]:
            hits.append(RuleHit(subject=subject, reason_code="drawdown", severity=4,
                                summary=f"最大回撤 {metrics.max_drawdown:.1%} 超过阈值",
                                value=metrics.max_drawdown, evidence_ids=market_ids))
        if metrics.volatility is not None and metrics.volatility >= self.settings["volatility_threshold"]:
            hits.append(RuleHit(subject=subject, reason_code="volatility", severity=3,
                                summary=f"波动率 {metrics.volatility:.1%} 超过阈值",
                                value=metrics.volatility, evidence_ids=market_ids))
        cutoff = self.clock() - timedelta(hours=self.settings["stale_hours"])
        stale = [item for item in evidence if item.status is EvidenceStatus.STALE or item.collected_at < cutoff]
        if stale:
            hits.append(RuleHit(subject=subject, reason_code="stale_data", severity=2,
                                summary="跟踪数据已过期",
                                evidence_ids=self._references(evidence, subject, lambda item: item in stale)))
        official = [item for item in evidence if item.source_type is SourceType.OFFICIAL
                    and item.status is EvidenceStatus.AVAILABLE
                    and item.confidence >= self.settings["official_confidence"]
                    and self._is_risk_notice(item)]
        if official:
            hits.append(RuleHit(subject=subject, reason_code="official_notice", severity=4,
                                summary="发现高可信官方公告",
                                evidence_ids=self._references(evidence, subject, lambda item: item in official)))
        return hits

    @staticmethod
    def _references(evidence: list[Evidence], subject: str, predicate) -> list[str]:
        references: list[str] = []
        for index, item in enumerate(evidence, 1):
            if not predicate(item):
                continue
            reference = item.id or f"evidence:{subject}:{index}"
            if reference not in references:
                references.append(reference)
        return references

    @classmethod
    def _is_risk_notice(cls, item: Evidence) -> bool:
        risk_flag = item.metadata.get("risk") if item.metadata else None
        if risk_flag is True:
            return True
        if isinstance(risk_flag, str) and risk_flag.strip().lower() in {"true", "1", "yes"}:
            return True
        content = (item.content or "").lower()
        return any(keyword in content for keyword in cls._OFFICIAL_RISK_KEYWORDS)
