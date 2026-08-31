"""Scheduled evidence collection and risk-review workflow."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from fund_agent.agent.reviewer import EvidenceReviewer, ReviewResult
from fund_agent.alerts.service import AlertService
from fund_agent.analytics.metrics import compute_nav_metrics
from fund_agent.domain.models import Evidence, EvidenceStatus, FundAnalysis, FundShare, NavMetrics, RiskAlert, SourceType
from fund_agent.persistence.repository import Repository
from fund_agent.tracking.rules import RuleEngine, RuleHit


class SourceStatus(BaseModel):
    """Outcome of one source adapter during a tracking run."""

    name: str
    status: EvidenceStatus
    evidence_count: int = 0
    subjects: list[str] = Field(default_factory=list)
    error: str | None = None
    collected_at: datetime | None = None


class TrackingRunResult(BaseModel):
    """Traceable result of a complete tracking pass."""

    subjects: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    source_statuses: dict[str, SourceStatus] = Field(default_factory=dict)
    rule_hits: dict[str, list[RuleHit]] = Field(default_factory=dict)
    reviews: dict[str, ReviewResult] = Field(default_factory=dict)
    alerts: list[RiskAlert] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime

    @property
    def source_results(self) -> dict[str, SourceStatus]:
        """Compatibility alias for callers that use the source-results wording."""

        return self.source_statuses


class TrackingService:
    """Collect evidence, run deterministic rules, review, and persist in-system alerts."""

    def __init__(
        self,
        *,
        repository: Repository,
        source_adapters: Sequence[Any] | Mapping[str, Any],
        rule_engine: RuleEngine,
        reviewer: EvidenceReviewer,
        alert_service: AlertService,
        clock=None,
    ) -> None:
        self.repository = repository
        self.source_adapters = source_adapters
        self.rule_engine = rule_engine
        self.reviewer = reviewer
        self.alert_service = alert_service
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._source_unavailable_subjects: set[str] = set()

    def run(
        self,
        funds: Sequence[FundAnalysis] | None = None,
        *,
        since: datetime | None = None,
    ) -> TrackingRunResult:
        started_at = self.clock()
        self._source_unavailable_subjects: set[str] = set()
        analyses = self._resolve_funds(funds)
        code_counts: dict[str, int] = {}
        for analysis in analyses:
            code_counts[analysis.fund.code] = code_counts.get(analysis.fund.code, 0) + 1
        result_keys = {
            analysis.fund.identity_key if code_counts[analysis.fund.code] > 1 else analysis.fund.code
            for analysis in analyses
        }
        subjects = sorted(result_keys)
        source_subjects = list(dict.fromkeys(analysis.fund.code for analysis in analyses))
        all_evidence: list[Evidence] = []
        evidence_by_subject: dict[str, list[Evidence]] = {subject: [] for subject in source_subjects}
        for subject in source_subjects:
            for existing in self.repository.list_evidence(subject):
                if since is not None and existing.collected_at < since:
                    continue
                if existing.id not in {item.id for item in evidence_by_subject[subject]}:
                    evidence_by_subject[subject].append(existing)
                    all_evidence.append(existing)
        source_statuses: dict[str, SourceStatus] = {}

        for source_name, adapter in self._iter_adapters():
            source_statuses[source_name] = self._collect_source(
                source_name,
                adapter,
                source_subjects,
                since,
                evidence_by_subject,
                all_evidence,
            )

        rule_hits: dict[str, list[RuleHit]] = {}
        reviews: dict[str, ReviewResult] = {}
        alerts: list[RiskAlert] = []
        for analysis in analyses:
            subject = analysis.fund.code
            result_key = analysis.fund.identity_key if code_counts[subject] > 1 else subject
            subject_evidence = self._latest_evidence(evidence_by_subject.get(subject, []))
            analysis = self._enrich_analysis(analysis, subject_evidence)
            hits = self.rule_engine.evaluate(analysis, subject_evidence)
            review = self.reviewer.review(subject, hits, subject_evidence)
            rule_hits[result_key] = hits
            reviews[result_key] = review

            current_reasons = {hit.reason_code for hit in hits}
            source_failed = subject in self._source_unavailable_subjects
            usable_evidence = any(item.status in {EvidenceStatus.AVAILABLE, EvidenceStatus.ESTIMATED} for item in subject_evidence)
            if not source_failed and usable_evidence:
                for active in self.alert_service.list_alerts(subject, active_only=True):
                    if active.reason_code in current_reasons:
                        continue
                    resolved = self.alert_service.resolve(subject, active.reason_code, started_at)
                    if resolved is not None:
                        alerts.append(self.repository.save_alert(resolved))

            alert = self.alert_service.upsert(review, at=started_at)
            if alert is not None:
                alerts.append(self.repository.save_alert(alert))
            elif not hits and not source_failed and usable_evidence:
                for active in self.alert_service.list_alerts(subject, active_only=True):
                    resolved = self.alert_service.resolve(subject, active.reason_code, started_at)
                    if resolved is not None:
                        alerts.append(self.repository.save_alert(resolved))

        return TrackingRunResult(
            subjects=subjects,
            evidence=all_evidence,
            source_statuses=source_statuses,
            rule_hits=rule_hits,
            reviews=reviews,
            alerts=alerts,
            started_at=started_at,
            completed_at=self.clock(),
        )

    @staticmethod
    def _latest_evidence(evidence: Sequence[Evidence]) -> list[Evidence]:
        latest: dict[tuple[SourceType, str], Evidence] = {}
        for item in evidence:
            if item.status not in {EvidenceStatus.AVAILABLE, EvidenceStatus.ESTIMATED}:
                continue
            provider = str(item.metadata.get("provider") or item.url or "default")
            key = (item.source_type, provider)
            current = latest.get(key)
            item_time = item.effective_at or item.collected_at
            current_time = (current.effective_at or current.collected_at) if current else None
            if current is None or item_time > current_time:
                latest[key] = item
        return list(latest.values())

    @staticmethod
    def _enrich_analysis(analysis: FundAnalysis, evidence: Sequence[Evidence]) -> FundAnalysis:
        """Fill missing deterministic metrics from the latest market NAV evidence."""

        current = analysis.metrics.model_dump(exclude_none=True)
        if len(current) == len(NavMetrics.model_fields):
            return analysis
        for item in sorted(evidence, key=lambda value: value.effective_at or value.collected_at, reverse=True):
            if item.source_type is not SourceType.MARKET or item.status not in {EvidenceStatus.AVAILABLE, EvidenceStatus.ESTIMATED}:
                continue
            nav = item.metadata.get("nav") if item.metadata else None
            if not isinstance(nav, list) or len(nav) < 2:
                continue
            try:
                derived = compute_nav_metrics([float(value) for value in nav])
            except (TypeError, ValueError):
                continue
            merged = derived.model_dump(exclude_none=True)
            merged.update(current)
            return analysis.model_copy(update={"metrics": NavMetrics(**merged)})
        return analysis

    def _resolve_funds(self, funds: Sequence[FundAnalysis] | None) -> list[FundAnalysis]:
        by_identity: dict[str, FundAnalysis] = {}
        explicit_codes: set[str] = set()
        if funds is not None:
            for analysis in funds:
                by_identity[analysis.fund.identity_key] = analysis
                explicit_codes.add(analysis.fund.code)

        for fund in getattr(self.repository, "list_funds", lambda: [])():
            if fund.code in explicit_codes:
                continue
            by_identity.setdefault(fund.identity_key, FundAnalysis(fund=fund, metrics=NavMetrics()))
        for snapshot in getattr(self.repository, "latest_snapshots", lambda: [])():
            fund = snapshot.fund
            if fund.code in explicit_codes:
                continue
            by_identity.setdefault(fund.identity_key, FundAnalysis(fund=fund, metrics=NavMetrics()))
        return list(by_identity.values())

    def _iter_adapters(self) -> Iterable[tuple[str, Any]]:
        if isinstance(self.source_adapters, Mapping):
            for name, adapter in self.source_adapters.items():
                normalized = getattr(name, "value", name)
                yield str(normalized), adapter
            return
        used: set[str] = set()
        for adapter in self.source_adapters:
            source_type = getattr(adapter, "source_type", None)
            base_name = str(getattr(source_type, "value", source_type or adapter.__class__.__name__.lower()))
            name = base_name
            suffix = 2
            while name in used:
                name = f"{base_name}#{suffix}"
                suffix += 1
            used.add(name)
            yield name, adapter

    def _collect_source(
        self,
        source_name: str,
        adapter: Any,
        subjects: Sequence[str],
        since: datetime | None,
        evidence_by_subject: dict[str, list[Evidence]],
        all_evidence: list[Evidence],
    ) -> SourceStatus:
        source_type = getattr(adapter, "source_type", SourceType.NEWS)
        if not isinstance(source_type, SourceType):
            try:
                source_type = SourceType(str(source_type))
            except ValueError:
                source_type = SourceType.NEWS
        source_records: list[Evidence] = []
        errors: list[str] = []
        touched_subjects: list[str] = []
        for subject in subjects:
            try:
                records = adapter.fetch(subject, since=since)
                records = list(records or [])
                touched_subjects.append(subject)
                if not records:
                    self._source_unavailable_subjects.add(subject)
                    errors.append(f"{subject}: no data returned")
                valid_record_count = 0
                for record in records:
                    if not isinstance(record, Evidence):
                        continue
                    valid_record_count += 1
                    normalized = record.model_copy(update={"subject": subject})
                    if normalized.status is EvidenceStatus.FAILED:
                        self._source_unavailable_subjects.add(subject)
                    if normalized.id and any(item.id == normalized.id for item in evidence_by_subject[subject]):
                        continue
                    saved = self.repository.save_evidence(normalized)
                    source_records.append(saved)
                    evidence_by_subject.setdefault(subject, []).append(saved)
                    all_evidence.append(saved)
                if records and not valid_record_count:
                    self._source_unavailable_subjects.add(subject)
                    errors.append(f"{subject}: no usable data returned")
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                errors.append(f"{subject}: {message}")
                failed = Evidence(
                    source_type=source_type,
                    subject=subject,
                    collected_at=self.clock(),
                    url=getattr(adapter, "endpoint", None),
                    content=f"source request failed: {message}",
                    confidence=0,
                    status=EvidenceStatus.FAILED,
                )
                saved = self.repository.save_evidence(failed)
                source_records.append(saved)
                evidence_by_subject.setdefault(subject, []).append(saved)
                all_evidence.append(saved)

        statuses = {record.status for record in source_records}
        if errors or EvidenceStatus.FAILED in statuses:
            # A partial source outage is still a failed source run.  Conflicting is
            # reserved for sources that explicitly report contradictory evidence.
            status = EvidenceStatus.FAILED
        elif EvidenceStatus.CONFLICTING in statuses:
            status = EvidenceStatus.CONFLICTING
        elif EvidenceStatus.STALE in statuses:
            status = EvidenceStatus.STALE
        elif EvidenceStatus.ESTIMATED in statuses and statuses <= {EvidenceStatus.ESTIMATED}:
            status = EvidenceStatus.ESTIMATED
        else:
            status = EvidenceStatus.AVAILABLE
        return SourceStatus(
            name=source_name,
            status=status,
            evidence_count=len(source_records),
            subjects=touched_subjects,
            error="; ".join(errors) if errors else None,
            collected_at=max((record.collected_at for record in source_records), default=None),
        )
