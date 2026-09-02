"""Runtime configuration for the local fund-analysis service.

The project deliberately keeps configuration independent from the web layer.  Values are
read at process start, while secrets are omitted from the default representation used by
diagnostic and UI code.
"""

from __future__ import annotations

import os
from datetime import time, timedelta
from typing import Any, Mapping
from urllib.parse import quote_plus

from pydantic import BaseModel, ConfigDict, Field, field_validator


_WEEKDAY_NAMES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


class RiskThresholds(BaseModel):
    """Fixed-rule thresholds used by the tracking rule engine."""

    model_config = ConfigDict(extra="ignore")

    nav_drop_threshold: float = -0.05
    drawdown_threshold: float = -0.20
    volatility_threshold: float = 0.30
    stale_hours: float = 24.0
    official_confidence: float = 0.80


class Settings(BaseModel):
    """Local service and data-source settings.

    ``from_env`` is the supported construction path.  The flat threshold fields mirror
    :class:`fund_agent.tracking.rules.RuleEngine` so a Settings instance can be passed to
    that boundary without an adapter.
    """

    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)

    # A complete URL takes precedence over individual MySQL settings.  These fields are
    # intentionally hidden from repr/model_dump because they may contain credentials.
    database_url: str | None = Field(default=None, repr=False)
    mysql_host: str | None = None
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: str | None = None
    mysql_user: str | None = None
    mysql_password: str | None = Field(default=None, repr=False)

    crawler_endpoint: str | None = None
    crawler_api_key: str | None = Field(default=None, repr=False)
    crawler_allowed_domains: tuple[str, ...] = ()
    crawler_timeout_seconds: float = Field(default=10.0, gt=0)
    crawler_max_retries: int = Field(default=2, ge=0, le=10)
    crawler_max_response_bytes: int = Field(default=2_000_000, gt=0)
    crawler_min_interval_seconds: float = Field(default=0.25, ge=0)
    crawler_user_agent: str = "TouzhiAgent/0.1"
    crawler_follow_redirects: bool = True
    crawler_respect_robots: bool = True
    market_endpoint: str | None = "https://fundmobapi.eastmoney.com/FundMApi/FundNetDiagram.ashx?FCODE={subject}&RANGE=y&deviceid=fund-agent&plat=Iphone&product=EFund&version=6.6.0"
    official_endpoint: str | None = None
    news_endpoint: str | None = None
    sentiment_endpoint: str | None = None

    full_analysis_time: time = time(16, 30)
    analysis_timezone: str = "Asia/Shanghai"
    analysis_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    evidence_interval_hours: float = Field(default=4.0, gt=0)
    scheduler_enabled: bool = True

    nav_drop_threshold: float = -0.05
    drawdown_threshold: float = -0.20
    volatility_threshold: float = 0.30
    stale_hours: float = Field(default=24.0, gt=0)
    official_confidence: float = Field(default=0.80, ge=0, le=1)
    screening_focus_threshold: float = Field(default=0.75, ge=0, le=1)
    screening_observe_threshold: float = Field(default=0.55, ge=0, le=1)
    screening_neutral_threshold: float = Field(default=0.35, ge=0, le=1)
    screening_weights: dict[str, float] = {
        "quality": 0.20,
        "return": 0.30,
        "drawdown": 0.15,
        "volatility": 0.10,
        "sharpe": 0.15,
        "calmar": 0.10,
    }

    @field_validator("analysis_weekdays")
    @classmethod
    def validate_weekdays(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        normalized = tuple(sorted(set(value)))
        if not normalized or any(day < 0 or day > 6 for day in normalized):
            raise ValueError("analysis_weekdays must contain weekday values from 0 to 6")
        return normalized

    @field_validator("analysis_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("analysis_timezone cannot be empty")
        return value

    @property
    def evidence_interval(self) -> timedelta:
        return timedelta(hours=self.evidence_interval_hours)

    @property
    def evidence_fetch_interval(self) -> timedelta:
        return self.evidence_interval

    @property
    def full_analysis_schedule(self) -> str:
        return self.full_analysis_time.strftime("%H:%M")

    @property
    def analysis_schedule(self) -> str:
        return self.full_analysis_schedule

    @property
    def risk_thresholds(self) -> RiskThresholds:
        return RiskThresholds(
            nav_drop_threshold=self.nav_drop_threshold,
            drawdown_threshold=self.drawdown_threshold,
            volatility_threshold=self.volatility_threshold,
            stale_hours=self.stale_hours,
            official_confidence=self.official_confidence,
        )

    @property
    def source_endpoints(self) -> dict[str, str]:
        """Return only configured source endpoints, keyed by source type."""

        return {
            name: value
            for name, value in {
                "market": self.market_endpoint,
                "official": self.official_endpoint,
                "news": self.news_endpoint,
                "sentiment": self.sentiment_endpoint,
            }.items()
            if value
        }

    @property
    def screening_thresholds(self) -> dict[str, float]:
        return {
            "focus": self.screening_focus_threshold,
            "observe": self.screening_observe_threshold,
            "neutral": self.screening_neutral_threshold,
        }

    @property
    def screening_score_weights(self) -> dict[str, float]:
        return dict(self.screening_weights)

    @property
    def full_analysis_weekdays(self) -> tuple[int, ...]:
        return self.analysis_weekdays

    @property
    def analysis_time(self) -> time:
        return self.full_analysis_time

    @property
    def trading_weekdays(self) -> tuple[int, ...]:
        return self.analysis_weekdays

    @property
    def full_analysis_timezone(self) -> str:
        return self.analysis_timezone

    @property
    def mysql_connection_url(self) -> str | None:
        """Build a DSN only when the minimum individual MySQL fields are present."""

        if not self.mysql_host or not self.mysql_database:
            return None
        user = quote_plus(self.mysql_user or "")
        password = quote_plus(self.mysql_password or "")
        credentials = f"{user}:{password}@" if self.mysql_user is not None or self.mysql_password is not None else ""
        return f"mysql+pymysql://{credentials}{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"

    def __repr__(self) -> str:  # pragma: no cover - exercised indirectly by security tests
        safe = self.model_dump()
        return f"Settings({safe!r})"

    def model_dump(self, *, include_secrets: bool = False, **kwargs: Any) -> dict[str, Any]:
        """Return a UI-safe mapping by default.

        ``include_secrets=True`` is intentionally explicit for the runtime connector that
        needs raw credentials.  The ordinary Pydantic ``model_dump`` call is therefore
        safe to use in logs and API responses.
        """

        dumped = super().model_dump(**kwargs)
        if include_secrets:
            return dumped
        for field_name in (
            "database_url",
            "mysql_host",
            "mysql_port",
            "mysql_database",
            "mysql_user",
            "mysql_password",
            "crawler_api_key",
        ):
            dumped.pop(field_name, None)
        return dumped

    def model_dump_json(self, *, include_secrets: bool = False, **kwargs: Any) -> str:
        """Serialize the same UI-safe view as :meth:`model_dump` by default."""

        if include_secrets:
            return super().model_dump_json(**kwargs)
        excluded = kwargs.pop("exclude", None)
        if excluded is None:
            excluded = set()
        elif isinstance(excluded, set):
            excluded = set(excluded)
        elif isinstance(excluded, Mapping):
            excluded = dict(excluded)
        else:
            excluded = set(excluded)
        if isinstance(excluded, dict):
            excluded.update({
                "database_url": True,
                "mysql_host": True,
                "mysql_port": True,
                "mysql_database": True,
                "mysql_user": True,
                "mysql_password": True,
                "crawler_api_key": True,
            })
        else:
            excluded.update({
                "database_url",
                "mysql_host",
                "mysql_port",
                "mysql_database",
                "mysql_user",
                "mysql_password",
                "crawler_api_key",
            })
        return super().model_dump_json(exclude=excluded, **kwargs)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        values = dict(os.environ if env is None else env)

        def first(*names: str) -> str | None:
            for name in names:
                value = values.get(name)
                if value is not None and value.strip() != "":
                    return value.strip()
            return None

        def integer(default: int | None, *names: str) -> int | None:
            raw = first(*names)
            return default if raw is None else int(raw)

        def number(default: float | None, *names: str) -> float | None:
            raw = first(*names)
            return default if raw is None else float(raw)

        raw_time = first(
            "FUND_AGENT_FULL_ANALYSIS_TIME",
            "FUND_AGENT_FULL_ANALYSIS_SCHEDULE",
            "FUND_AGENT_ANALYSIS_TIME",
        )
        analysis_time = _parse_time(raw_time) if raw_time is not None else time(16, 30)
        raw_weekdays = first("FUND_AGENT_ANALYSIS_WEEKDAYS", "FUND_AGENT_FULL_ANALYSIS_WEEKDAYS")
        weekdays = _parse_weekdays(raw_weekdays) if raw_weekdays is not None else (0, 1, 2, 3, 4)

        return cls(
            host=first("FUND_AGENT_HOST") or "127.0.0.1",
            port=integer(8000, "FUND_AGENT_PORT") or 8000,
            database_url=first("FUND_AGENT_DATABASE_URL", "FUND_AGENT_MYSQL_URL"),
            mysql_host=first("FUND_AGENT_MYSQL_HOST", "FUND_AGENT_DB_HOST"),
            mysql_port=integer(3306, "FUND_AGENT_MYSQL_PORT", "FUND_AGENT_DB_PORT") or 3306,
            mysql_database=first("FUND_AGENT_MYSQL_DATABASE", "FUND_AGENT_MYSQL_DB", "FUND_AGENT_DB_NAME"),
            mysql_user=first("FUND_AGENT_MYSQL_USER", "FUND_AGENT_DB_USER"),
            mysql_password=first("FUND_AGENT_MYSQL_PASSWORD", "FUND_AGENT_DB_PASSWORD"),
            crawler_endpoint=first("FUND_AGENT_CRAWLER_ENDPOINT", "FUND_AGENT_CRAWLER_URL"),
            crawler_api_key=first("FUND_AGENT_CRAWLER_API_KEY", "FUND_AGENT_CRAWLER_API"),
            crawler_allowed_domains=_parse_csv(first("FUND_AGENT_CRAWLER_ALLOWED_DOMAINS")),
            crawler_timeout_seconds=number(10.0, "FUND_AGENT_CRAWLER_TIMEOUT_SECONDS") or 10.0,
            crawler_max_retries=integer(2, "FUND_AGENT_CRAWLER_MAX_RETRIES") or 0,
            crawler_max_response_bytes=integer(2_000_000, "FUND_AGENT_CRAWLER_MAX_RESPONSE_BYTES") or 2_000_000,
            crawler_min_interval_seconds=number(0.25, "FUND_AGENT_CRAWLER_MIN_INTERVAL_SECONDS") or 0.0,
            crawler_user_agent=first("FUND_AGENT_CRAWLER_USER_AGENT") or "TouzhiAgent/0.1",
            crawler_follow_redirects=_parse_bool(first("FUND_AGENT_CRAWLER_FOLLOW_REDIRECTS"), True),
            crawler_respect_robots=_parse_bool(first("FUND_AGENT_CRAWLER_RESPECT_ROBOTS"), True),
            market_endpoint=first("FUND_AGENT_MARKET_ENDPOINT", "FUND_AGENT_MARKET_URL") or cls.model_fields["market_endpoint"].default,
            official_endpoint=first("FUND_AGENT_OFFICIAL_ENDPOINT", "FUND_AGENT_OFFICIAL_URL"),
            news_endpoint=first("FUND_AGENT_NEWS_ENDPOINT", "FUND_AGENT_NEWS_URL"),
            sentiment_endpoint=first("FUND_AGENT_SENTIMENT_ENDPOINT", "FUND_AGENT_SENTIMENT_URL"),
            full_analysis_time=analysis_time,
            analysis_timezone=first("FUND_AGENT_ANALYSIS_TIMEZONE", "FUND_AGENT_TIMEZONE") or "Asia/Shanghai",
            analysis_weekdays=weekdays,
            evidence_interval_hours=_interval_hours(first("FUND_AGENT_EVIDENCE_INTERVAL_HOURS", "FUND_AGENT_EVIDENCE_INTERVAL"), 4.0),
            scheduler_enabled=_parse_bool(first("FUND_AGENT_SCHEDULER_ENABLED"), True),
            nav_drop_threshold=_number_or_default(number(None, "FUND_AGENT_NAV_DROP_THRESHOLD", "FUND_AGENT_RISK_NAV_DROP_THRESHOLD"), -0.05),
            drawdown_threshold=_number_or_default(number(None, "FUND_AGENT_DRAWDOWN_THRESHOLD", "FUND_AGENT_RISK_DRAWDOWN_THRESHOLD"), -0.20),
            volatility_threshold=_number_or_default(number(None, "FUND_AGENT_VOLATILITY_THRESHOLD", "FUND_AGENT_RISK_VOLATILITY_THRESHOLD"), 0.30),
            stale_hours=_number_or_default(number(None, "FUND_AGENT_STALE_HOURS", "FUND_AGENT_RISK_STALE_HOURS"), 24.0),
            official_confidence=_number_or_default(number(None, "FUND_AGENT_OFFICIAL_CONFIDENCE", "FUND_AGENT_RISK_OFFICIAL_CONFIDENCE"), 0.80),
            screening_focus_threshold=_number_or_default(number(None, "FUND_AGENT_SCREENING_FOCUS_THRESHOLD"), 0.75),
            screening_observe_threshold=_number_or_default(number(None, "FUND_AGENT_SCREENING_OBSERVE_THRESHOLD"), 0.55),
            screening_neutral_threshold=_number_or_default(number(None, "FUND_AGENT_SCREENING_NEUTRAL_THRESHOLD"), 0.35),
            screening_weights=_parse_weights(first("FUND_AGENT_SCREENING_WEIGHTS")),
        )


def _parse_time(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError("analysis time must use HH:MM or HH:MM:SS")
    try:
        parsed = time(*(int(part) for part in parts))
    except (TypeError, ValueError) as exc:
        raise ValueError("analysis time must use HH:MM or HH:MM:SS") from exc
    return parsed


def _parse_weekdays(value: str) -> tuple[int, ...]:
    result: list[int] = []
    for token in value.replace(";", ",").split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token in _WEEKDAY_NAMES:
            result.append(_WEEKDAY_NAMES[token])
            continue
        try:
            day = int(token)
        except ValueError as exc:
            raise ValueError(f"unknown weekday: {token}") from exc
        if day < 0 or day > 6:
            raise ValueError("weekday values must be between 0 and 6")
        result.append(day)
    if not result:
        raise ValueError("analysis weekdays cannot be empty")
    return tuple(sorted(set(result)))


def _number_or_default(value: float | None, default: float) -> float:
    return default if value is None else value


def _interval_hours(value: str | None, default: float) -> float:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized.endswith("h"):
        return float(normalized[:-1])
    if normalized.endswith("m"):
        return float(normalized[:-1]) / 60
    if normalized.endswith("s"):
        return float(normalized[:-1]) / 3600
    return float(normalized)


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean configuration must be true/false")


def _parse_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(dict.fromkeys(item.strip() for item in value.replace(";", ",").split(",") if item.strip()))


def _parse_weights(value: str | None) -> dict[str, float]:
    if not value:
        return dict(Settings.model_fields["screening_weights"].default)
    parsed: dict[str, float] = {}
    for part in value.split(","):
        name, separator, raw = part.partition("=")
        if not separator or not name.strip():
            raise ValueError("screening weights must use name=value pairs")
        parsed[name.strip()] = float(raw)
    return parsed
