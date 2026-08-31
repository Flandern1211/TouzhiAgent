# Fund Agent v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a runnable local Python service that supports manual multi-fund screening, current portfolio snapshot analysis, evidence-backed monitoring, and in-system risk alerts for Chinese public funds.

**Architecture:** Use a small Python application with domain models and deterministic analytics at the center. Source adapters expose one normalized evidence contract and can use either the default public fetcher or a configured HTTP crawler API. A repository protocol isolates MySQL persistence from an in-memory test/demo implementation; the local HTTP application composes screening, portfolio, tracking, review, and alert workflows without broker or external notification integrations.

**Tech Stack:** Python 3.11+, Pydantic v2, pandas/numpy for deterministic calculations, FastAPI/Uvicorn for the local service, PyMySQL for optional remote MySQL persistence, pytest for tests, and a dependency-free HTML/JavaScript browser view served by the Python app.

**Spec:** `docs/requirements/fund-agent-v1-requirements.md`; `docs/coding/TSD/2026-08-27-fund-agent-v1-minimal-design.md`

## Global Constraints

- v1 uses Python.
- The service runs locally and may connect to MySQL and other infrastructure on a server.
- Funds and holdings are manually maintained; no transaction ledger, account import, broker connection, order execution, or automatic rebalancing.
- Sources include public market/nav data, official notices, financial/industry news, and public sentiment pages; the default fetcher is replaceable by a configured crawler API.
- Fixed rules detect anomalies; an evidence-aware reviewer explains and grades them.
- Alerts are displayed in-system only.
- Important facts retain source, collection time, effective time when available, and original URL/reference.
- Formal NAV, estimates, disclosed facts, model inferences, and unverified sentiment leads remain distinct.
- Missing, stale, failed, or conflicting data is visible and never silently treated as current truth.

## File Map

- `pyproject.toml`: package metadata, runtime dependencies, and test configuration.
- `src/fund_agent/config/settings.py`: environment/config parsing with safe defaults.
- `src/fund_agent/domain/models.py`: shared Pydantic value objects and enums.
- `src/fund_agent/funds/identity.py`: code/name/share-class normalization and candidate records.
- `src/fund_agent/analytics/metrics.py`: pure NAV/return/risk metric functions.
- `src/fund_agent/screening/service.py`: objective and personalized ranking.
- `src/fund_agent/portfolio/service.py`: current snapshot and portfolio risk calculations.
- `src/fund_agent/sources/protocol.py`: normalized source/evidence interfaces.
- `src/fund_agent/sources/http.py`: default public fetcher and configurable crawler API client.
- `src/fund_agent/tracking/rules.py`: deterministic anomaly rules.
- `src/fund_agent/agent/reviewer.py`: evidence-based deterministic/LLM-ready review contract.
- `src/fund_agent/alerts/service.py`: deduplication, escalation, recovery, and alert history.
- `src/fund_agent/persistence/repository.py`: persistence protocol and in-memory implementation.
- `src/fund_agent/persistence/mysql.py`: optional PyMySQL repository and schema bootstrap.
- `src/fund_agent/application.py`: workflow composition and service container.
- `src/fund_agent/web.py`: FastAPI routes and static local browser view.
- `src/fund_agent/__main__.py`: local server entry point.
- `tests/conftest.py`: stable source-tree import path for tests.
- `tests/unit/`: pure domain, analytics, rules, and alert tests.
- `tests/integration/`: API and repository boundary tests that do not require live credentials.
- `docs/coding/API.md`: frozen v1 local API contract after implementation.

## Task 1: Runtime and Domain Foundation

**Files:**
- Create: `pyproject.toml`
- Create: `src/fund_agent/config/settings.py`
- Create: `src/fund_agent/domain/__init__.py`
- Create: `src/fund_agent/domain/models.py`
- Create: `tests/unit/test_domain_models.py`

**Interfaces:**
- `FundShare(code: str, product_id: str | None, name: str | None, category: str | None, share_class: str | None)`
- `HoldingSnapshot(fund: FundShare, amount: float | None, units: float | None, invested: float, as_of: datetime, manual_value: float | None = None)`
- `Evidence(source_type: SourceType, subject: str, collected_at: datetime, effective_at: datetime | None, url: str | None, content: str | None, confidence: float, status: EvidenceStatus = AVAILABLE)`
- `RiskAlert(subject: str, level: RiskLevel, reason_code: str, triggered_at: datetime, summary: str, evidence_ids: list[str], status: AlertStatus)`
- `Settings.from_env()` reads `FUND_AGENT_*` variables and never logs secret values.

- [x] **Step 1: Write failing model and configuration tests**
- [x] **Step 2: Run `pytest tests/unit/test_domain_models.py -q` and verify expected failures**
- [x] **Step 3: Implement enums, models, validation, and settings**
- [x] **Step 4: Re-run focused tests and then `pytest -q`**
- [x] **Step 5: Review the focused diff; do not commit unless the user requests it**

## Task 2: Fund Identity and Deterministic Analytics

**Files:**
- Create: `src/fund_agent/funds/identity.py`
- Create: `src/fund_agent/analytics/__init__.py`
- Create: `src/fund_agent/analytics/metrics.py`
- Create: `tests/unit/test_identity.py`
- Create: `tests/unit/test_metrics.py`

**Interfaces:**
- `normalize_fund_input(value: str, metadata: dict[str, object] | None = None) -> FundShare`
- `identify_share_class(name: str | None) -> str | None`
- `compute_nav_metrics(nav: Sequence[float], periods_per_year: int = 252) -> NavMetrics`
- `compute_drawdown(nav: Sequence[float]) -> DrawdownMetrics`
- `compute_portfolio_returns(weights: Mapping[str, float], returns: Mapping[str, Sequence[float]]) -> Sequence[float]`

- [x] **Step 1: Write failing tests for codes, A/C/E share parsing, invalid input, empty/constant NAV, returns, drawdown, volatility, Sharpe, and Calmar**
- [x] **Step 2: Run focused tests and verify they fail for missing implementations**
- [x] **Step 3: Implement pure functions with explicit missing-data states**
- [x] **Step 4: Run focused and full unit tests**
- [x] **Step 5: Review the focused diff; do not commit unless the user requests it**

## Task 3: Screening and Portfolio Services

**Files:**
- Create: `src/fund_agent/screening/service.py`
- Create: `src/fund_agent/portfolio/service.py`
- Create: `tests/unit/test_screening.py`
- Create: `tests/unit/test_portfolio.py`

**Interfaces:**
- `PreferenceProfile(risk: str = "balanced", horizon: str = "long_term")`
- `ScreeningService.rank(funds: Sequence[FundAnalysis], preference: PreferenceProfile | None = None) -> list[ScreeningResult]`
- `PortfolioService.analyze(snapshot: Sequence[HoldingSnapshot], latest_values: Mapping[str, float], history: Mapping[str, Sequence[float]]) -> PortfolioAnalysis`

- [x] **Step 1: Write failing tests for objective vs personalized ranking and deterministic four-level labels**
- [x] **Step 2: Write failing tests for snapshot valuation, weights, contribution, concentration, and missing-value warnings**
- [x] **Step 3: Implement configuration-driven weights and pure service methods**
- [x] **Step 4: Run focused and full tests**
- [x] **Step 5: Review the focused diff; do not commit unless the user requests it**

## Task 4: Sources, Evidence, and Persistence Boundaries

**Files:**
- Create: `src/fund_agent/sources/protocol.py`
- Create: `src/fund_agent/sources/http.py`
- Create: `src/fund_agent/persistence/repository.py`
- Create: `src/fund_agent/persistence/mysql.py`
- Create: `tests/unit/test_sources.py`
- Create: `tests/unit/test_repository.py`

**Interfaces:**
- `SourceAdapter.fetch(subject: str, since: datetime | None = None) -> list[Evidence]`
- `PublicHttpSource(base_url: str, source_type: SourceType, timeout_seconds: float = 10.0)`
- `CrawlerApiSource(endpoint: str, api_key: str | None, source_type: SourceType, timeout_seconds: float = 20.0)`
- `Repository` methods: `save_fund`, `list_funds`, `save_snapshot`, `latest_snapshots`, `save_evidence`, `list_evidence`, `save_alert`, `list_alerts`.
- `MySqlRepository.initialize_schema()` creates only v1 tables and uses parameterized SQL.

- [x] **Step 1: Write failing adapter normalization and repository contract tests using deterministic fake transports**
- [x] **Step 2: Run focused tests and verify failures**
- [x] **Step 3: Implement adapters, failure/status handling, in-memory repository, and optional MySQL repository**
- [x] **Step 4: Verify no credentials are emitted and run focused/full tests**
- [x] **Step 5: Review the focused diff; do not commit unless the user requests it**

## Task 5: Tracking, Review, and Alerts

**Files:**
- Create: `src/fund_agent/tracking/rules.py`
- Create: `src/fund_agent/agent/reviewer.py`
- Create: `src/fund_agent/alerts/service.py`
- Create: `tests/unit/test_rules.py`
- Create: `tests/unit/test_alerts.py`

**Interfaces:**
- `RuleEngine.evaluate(fund: FundAnalysis, evidence: Sequence[Evidence]) -> list[RuleHit]`
- `EvidenceReviewer.review(subject: str, hits: Sequence[RuleHit], evidence: Sequence[Evidence]) -> ReviewResult`
- `AlertService.upsert(review: ReviewResult) -> RiskAlert | None`
- `AlertService.resolve(subject: str, reason_code: str, at: datetime) -> RiskAlert | None`

- [x] **Step 1: Write failing tests for NAV drop, drawdown, volatility, stale data, official notice, sentiment-only evidence, dedupe, escalation, and recovery**
- [x] **Step 2: Run focused tests and verify failures**
- [x] **Step 3: Implement fixed thresholds as settings-backed defaults and evidence-aware review**
- [x] **Step 4: Run focused and full tests**
- [x] **Step 5: Review the focused diff; do not commit unless the user requests it**

## Task 6: Application Workflows and Local API/UI

**Files:**
- Create: `src/fund_agent/application.py`
- Create: `src/fund_agent/web.py`
- Create: `src/fund_agent/__main__.py`
- Create: `src/fund_agent/static/index.html`
- Create: `tests/integration/test_api.py`
- Modify: `docs/coding/API.md`
- Modify: `README.md`

**Interfaces:**
- `POST /api/funds` and `GET /api/funds`
- `POST /api/screening`
- `POST /api/holdings` and `GET /api/portfolio`
- `POST /api/tracking/run`
- `GET /api/alerts`
- `GET /api/health`
- `GET /` serves the local browser view.

- [x] **Step 1: Write failing API tests for manual funds, screening, snapshots, portfolio, tracking, alerts, and health**
- [x] **Step 2: Run focused API tests and verify failures**
- [x] **Step 3: Implement service container, routes, validation errors, and a small usable browser view**
- [x] **Step 4: Run API tests, full tests, and a local startup smoke test**
- [x] **Step 5: Review the focused diff; do not commit unless the user requests it**

## Task 7: Final Verification and Documentation

**Files:**
- Modify: `docs/project-structure.md`
- Modify: `docs/project-conventions.md`
- Modify: `docs/coding/TSD/2026-08-27-fund-agent-v1-minimal-design.md`
- Modify: `tests/conftest.py`

- [x] **Step 1: Run governance validator**
- [x] **Step 2: Run the complete test suite with coverage of all v1 acceptance criteria**
- [x] **Step 3: Verify no secrets, generated artifacts, or out-of-scope integrations are tracked**
- [x] **Step 4: Update documentation from “待实现” to implemented status only where evidence supports it**
- [ ] **Step 5: Review the final diff; do not commit unless the user requests it**

## Deferred by Design

- Transaction ledger, recurring investment plans, broker/account import, order execution, automatic rebalancing, external notifications, whole-market autonomous selection, and guaranteed return prediction remain outside this plan.
- Concrete external source URLs and provider-specific parsing are configured incrementally; the normalized adapter contract and safe fallback behavior are implemented first.
