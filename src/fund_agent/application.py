from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

from fund_agent.agent.reviewer import EvidenceReviewer
from fund_agent.alerts.service import AlertService
from fund_agent.config.settings import Settings
from fund_agent.domain.models import Evidence, EvidenceStatus, FundAnalysis, FundShare, HoldingSnapshot, NavMetrics
from fund_agent.persistence.repository import InMemoryRepository
from fund_agent.persistence.mysql import MySqlRepository
from fund_agent.portfolio.service import PortfolioService
from fund_agent.screening.service import PreferenceProfile, ScreeningService
from fund_agent.tracking.rules import RuleEngine
from fund_agent.tracking.service import TrackingRunResult, TrackingService
from fund_agent.tracking.scheduler import TrackingScheduler
from fund_agent.sources.http import CrawlerApiSource
from fund_agent.sources.http import PublicHttpSource
from fund_agent.domain.models import SourceType


class FundAgentApplication:
    def __init__(self, repository=None, *, settings: Settings | None = None, source_adapters=None, clock=None):
        self.settings = settings or Settings.from_env()
        self.repository = repository or self.repository_from_settings(self.settings)
        if isinstance(self.repository, MySqlRepository):
            self.repository.initialize_schema()
        self.screening = ScreeningService(weights=self.settings.screening_score_weights, thresholds=self.settings.screening_thresholds)
        self.portfolio = PortfolioService()
        self.rules = RuleEngine(settings=self.settings.risk_thresholds, clock=clock)
        self.reviewer = EvidenceReviewer()
        self.alerts = AlertService(clock=clock)
        if hasattr(self.repository, "list_alerts"):
            self.alerts.hydrate(self.repository.list_alerts())
        self.source_adapters = list(source_adapters) if source_adapters is not None else self._default_sources()
        self.tracking = TrackingService(
            repository=self.repository,
            source_adapters=self.source_adapters,
            rule_engine=self.rules,
            reviewer=self.reviewer,
            alert_service=self.alerts,
            clock=clock,
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.scheduler = TrackingScheduler(
            self.run_evidence_tracking,
            full_analysis_callback=self.run_full_analysis,
            settings=self.settings,
            clock=self._clock,
            interval_seconds=min(self.settings.evidence_interval.total_seconds(), 60.0),
        )

    @staticmethod
    def repository_from_settings(settings: Settings, *, connect=None):
        if settings.database_url:
            parsed = urlparse(settings.database_url.replace("mysql+pymysql://", "mysql://", 1))
            return MySqlRepository(
                connect=connect,
                host=parsed.hostname,
                port=parsed.port or 3306,
                database=parsed.path.lstrip("/"),
                user=unquote(parsed.username or ""),
                password=unquote(parsed.password or ""),
                charset="utf8mb4",
            )
        if settings.mysql_host and settings.mysql_database:
            return MySqlRepository(
                connect=connect,
                host=settings.mysql_host,
                port=settings.mysql_port,
                database=settings.mysql_database,
                user=settings.mysql_user,
                password=settings.mysql_password,
                charset="utf8mb4",
            )
        return InMemoryRepository()

    def _default_sources(self) -> list[Any]:
        if self.settings.crawler_endpoint:
            return [
                CrawlerApiSource(self.settings.crawler_endpoint, self.settings.crawler_api_key, source_type)
                for source_type in SourceType
            ]
        return [
            PublicHttpSource(endpoint, SourceType(source_name))
            for source_name, endpoint in self.settings.source_endpoints.items()
        ]

    def add_fund(self, fund: FundShare) -> FundShare:
        return self.repository.save_fund(fund)

    def list_funds(self) -> list[FundShare]:
        return self.repository.list_funds()

    def add_holding(self, snapshot: HoldingSnapshot) -> HoldingSnapshot:
        self.repository.save_fund(snapshot.fund)
        return self.repository.save_snapshot(snapshot)

    def analyze_portfolio(self, latest_values: dict[str, float] | None = None) -> Any:
        snapshots = self.repository.latest_snapshots()
        values = dict(latest_values or {})
        history: dict[str, list[float]] = {}
        for snapshot in snapshots:
            identity = snapshot.fund.identity_key
            candidates = sorted(
                self.repository.list_evidence(snapshot.fund.code),
                key=lambda item: item.effective_at or item.collected_at,
                reverse=True,
            )
            for evidence in candidates:
                if evidence.status not in {EvidenceStatus.AVAILABLE, EvidenceStatus.ESTIMATED}:
                    continue
                nav = evidence.metadata.get("nav") if evidence.metadata else None
                if isinstance(nav, list) and len(nav) >= 2:
                    try:
                        history[identity] = [float(value) for value in nav]
                    except (TypeError, ValueError):
                        continue
                    if identity not in values and snapshot.fund.code not in values:
                        latest = evidence.metadata.get("latest_value") if evidence.metadata else None
                        if latest is not None:
                            values[identity] = float(latest)
                        else:
                            values[identity] = history[identity][-1]
                    break
        return self.portfolio.analyze(snapshots, values, history)

    def screen(self, funds: list[FundAnalysis], preference: PreferenceProfile | None = None):
        enriched = []
        for analysis in funds:
            evidence = self.tracking._latest_evidence(self.repository.list_evidence(analysis.fund.code))
            enriched.append(self.tracking._enrich_analysis(analysis, evidence))
        return self.screening.rank(enriched, preference)

    def run_tracking(
        self,
        funds: list[FundAnalysis] | None = None,
        evidence: list[Evidence] | None = None,
        *,
        since: datetime | None = None,
    ) -> TrackingRunResult:
        for item in evidence or []:
            self.repository.save_evidence(item)
        return self.tracking.run(funds, since=since)

    def start(self) -> None:
        if self.settings.scheduler_enabled:
            self.scheduler.start()

    def stop(self) -> None:
        self.scheduler.stop()

    def update_settings(self, values: dict[str, Any]) -> Settings:
        """Apply non-secret runtime settings in place.

        Threshold and screening changes take effect immediately; the scheduler reads the
        live settings object each tick.  Secrets and repository settings stay env-driven.
        """

        for key, value in values.items():
            if value is not None:
                setattr(self.settings, key, value)
        self.screening = ScreeningService(
            weights=self.settings.screening_score_weights, thresholds=self.settings.screening_thresholds
        )
        self.rules = RuleEngine(settings=self.settings.risk_thresholds, clock=self._clock)
        self.tracking.rule_engine = self.rules
        if not self.settings.crawler_endpoint:
            self.source_adapters[:] = [
                PublicHttpSource(endpoint, SourceType(source_name))
                for source_name, endpoint in self.settings.source_endpoints.items()
            ]
        return self.settings

    def run_evidence_tracking(self) -> TrackingRunResult:
        return self.run_tracking(since=self._clock() - self.settings.evidence_interval)

    def run_full_analysis(self) -> TrackingRunResult:
        return self.run_tracking()

    def track(self, funds: list[FundAnalysis], evidence: list[Evidence]):
        return self.run_tracking(funds, evidence).alerts


def create_application():
    from fund_agent.web import build_app

    return build_app(FundAgentApplication(settings=Settings.from_env()))


def parse_json_object(value: str | None) -> dict[str, float]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("latest_values must be a JSON object")
    return {str(key): float(item) for key, item in parsed.items()}
