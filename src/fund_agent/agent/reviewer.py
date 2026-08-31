from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from fund_agent.domain.models import Evidence, EvidenceStatus, RiskLevel, SourceType
from fund_agent.tracking.rules import RuleHit


class ReviewResult(BaseModel):
    subject: str
    level: RiskLevel = RiskLevel.NEUTRAL
    reason_code: str = "none"
    summary: str = "没有发现需要升级的异常"
    evidence_ids: list[str] = Field(default_factory=list)
    uncertainty: str | None = None
    followups: list[str] = Field(default_factory=list)


class EvidenceReviewer:
    def review(self, subject: str, hits: Sequence[RuleHit], evidence: Sequence[Evidence]) -> ReviewResult:
        hits = list(hits)
        evidence = list(evidence)
        if not hits:
            return ReviewResult(subject=subject, uncertainty="当前没有规则命中，仍需按频率更新数据")
        sources = {item.source_type for item in evidence if item.status is EvidenceStatus.AVAILABLE}
        strongest = max(hits, key=lambda hit: hit.severity)
        level = {1: RiskLevel.FOCUS, 2: RiskLevel.OBSERVE, 3: RiskLevel.OBSERVE, 4: RiskLevel.HIGH_RISK}[strongest.severity]
        sentiment_only = bool(sources) and sources <= {SourceType.SENTIMENT}
        if sentiment_only:
            level = RiskLevel.OBSERVE if strongest.severity >= 2 else RiskLevel.FOCUS
        corroborated = len(sources - {SourceType.SENTIMENT}) >= 1
        uncertainty = None
        if not sources:
            uncertainty = "没有可用证据，无法确认高严重度风险"
            if level is RiskLevel.HIGH_RISK:
                level = RiskLevel.OBSERVE
        elif sentiment_only:
            uncertainty = "仅有未经证实的舆情线索，不能据此形成高风险结论"
        elif not corroborated:
            uncertainty = "可用证据有限，结论需进一步核查"
            if level is RiskLevel.HIGH_RISK:
                level = RiskLevel.OBSERVE
        # Source adapters may not assign IDs in an in-memory run. Still expose
        # deterministic references, in source order, so every review remains traceable.
        source_refs: list[str] = []
        for index, item in enumerate(evidence, 1):
            reference = item.id or f"evidence:{subject}:{index}"
            if reference not in source_refs:
                source_refs.append(reference)
        hit_ids = [item_id for hit in hits for item_id in hit.evidence_ids]
        ids = list(source_refs)
        ids.extend(item_id for item_id in hit_ids if item_id not in ids)
        return ReviewResult(subject=subject, level=level, reason_code=strongest.reason_code,
                            summary="；".join(hit.summary for hit in hits), evidence_ids=ids,
                            uncertainty=uncertainty,
                            followups=["核对官方公告及最新正式净值"] if uncertainty else [])
