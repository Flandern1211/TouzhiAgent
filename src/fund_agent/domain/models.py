from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceType(StrEnum):
    MARKET = "market"
    OFFICIAL = "official"
    NEWS = "news"
    SENTIMENT = "sentiment"


class EvidenceStatus(StrEnum):
    AVAILABLE = "available"
    ESTIMATED = "estimated"
    STALE = "stale"
    FAILED = "failed"
    CONFLICTING = "conflicting"


class RiskLevel(StrEnum):
    FOCUS = "focus"
    OBSERVE = "observe"
    NEUTRAL = "neutral"
    HIGH_RISK = "high_risk"


class AlertStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    ACKNOWLEDGED = "acknowledged"


class FundShare(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    code: str
    product_id: str | None = None
    name: str | None = None
    category: str | None = None
    share_class: str | None = None

    @property
    def identity_key(self) -> str:
        """Stable storage identity that keeps share classes distinct."""

        return f"{self.code}:{self.share_class or '-'}"

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        if not value.isdigit() or len(value) != 6:
            raise ValueError("fund code must contain exactly six digits")
        return value

    @field_validator("share_class")
    @classmethod
    def normalize_share_class(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized and (len(normalized) != 1 or not normalized.isalpha()):
            raise ValueError("share class must be a single alphabetic letter")
        return normalized or None


class HoldingSnapshot(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    fund: FundShare
    amount: float | None = Field(default=None, gt=0)
    units: float | None = Field(default=None, gt=0)
    invested: float = Field(gt=0)
    as_of: datetime
    manual_value: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_position_size(self) -> "HoldingSnapshot":
        if self.amount is None and self.units is None:
            raise ValueError("holding requires amount or units")
        return self

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return _utc_datetime(value)


class Evidence(BaseModel):
    id: str | None = None
    source_type: SourceType
    subject: str
    collected_at: datetime
    effective_at: datetime | None = None
    url: str | None = None
    content: str | None = None
    confidence: float = Field(ge=0, le=1)
    status: EvidenceStatus = EvidenceStatus.AVAILABLE
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("collected_at", "effective_at")
    @classmethod
    def normalize_evidence_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _utc_datetime(value)


class NavMetrics(BaseModel):
    total_return: float | None = None
    max_drawdown: float | None = None
    volatility: float | None = None
    sharpe: float | None = None
    calmar: float | None = None


class FundAnalysis(BaseModel):
    fund: FundShare
    metrics: NavMetrics
    quality_score: float = Field(default=1.0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class RiskAlert(BaseModel):
    id: str | None = None
    subject: str
    level: RiskLevel
    reason_code: str
    triggered_at: datetime
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)
    status: AlertStatus = AlertStatus.ACTIVE
    uncertainty: str | None = None

    @field_validator("triggered_at")
    @classmethod
    def normalize_triggered_at(cls, value: datetime) -> datetime:
        return _utc_datetime(value)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
