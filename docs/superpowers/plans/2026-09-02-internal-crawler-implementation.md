# Internal Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a controllable internal public-page crawler and an external-first source router with transparent fallback and evidence status.

**Architecture:** Keep the existing `SourceAdapter` contract at the business boundary. Add an internal crawler that owns HTTP policy, content extraction, safety limits, and request outcomes; add a router that selects the configured external adapter first and delegates to the internal source when the external result is unusable.

**Tech Stack:** Python 3.11+, `httpx`, standard-library HTML parsing and URL utilities, Pydantic domain models, pytest with deterministic mock transports.

**Spec:** `docs/superpowers/specs/2026-09-02-internal-crawler-design.md`; `docs/requirements/fund-agent-v1-requirements.md`

## Global Constraints

- The internal crawler must remain available without an external crawler service.
- External crawler calls occur only when `FUND_AGENT_CRAWLER_ENDPOINT` is explicitly configured.
- Failed, empty, malformed, restricted, and unsupported pages remain visible as failed evidence; they are never replaced with invented content.
- The crawler must not bypass login, CAPTCHA, authorization, robots, or website security controls.
- Important evidence retains source type, collection time, effective time when available, URL, and status.
- Credentials and API keys must not appear in code, logs, evidence content, settings output, or frontend output.

### Task 1: Model crawler outcomes and configuration

**Files:**
- Modify: `src/fund_agent/config/settings.py`
- Modify: `src/fund_agent/domain/models.py`
- Test: `tests/unit/test_settings.py`
- Test: `tests/unit/test_domain_models.py`

**Interfaces:**
- `CrawlerSettings` or equivalent settings fields exposed through `Settings`.
- `Settings.from_env()` parses the seven internal crawler environment variables listed in the design.
- Crawler outcome metadata uses stable string keys such as `crawler`, `attempts`, `bytes`, `failure_reason`, and `fallback`.

- [ ] Write failing tests for defaults, environment parsing, domain-list parsing, and safe serialization.
- [ ] Run `py -3 -m pytest tests/unit/test_settings.py tests/unit/test_domain_models.py -q` and confirm the new assertions fail.
- [ ] Implement the smallest validated settings fields and metadata-compatible status vocabulary.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Implement the internal HTTP crawler

**Files:**
- Create: `src/fund_agent/sources/crawler.py`
- Modify: `src/fund_agent/sources/__init__.py`
- Test: `tests/unit/test_crawler.py`

**Interfaces:**
- `CrawlerFailureReason` enum or constants for `invalid_url`, `domain_not_allowed`, `timeout`, `transport_error`, `http_error`, `redirect_not_allowed`, `response_too_large`, `unsupported_content_type`, `javascript_required`, `login_required`, `access_restricted`, and `empty_content`.
- `CrawlerResult(success: bool, url: str, final_url: str | None, status_code: int | None, content: str | None, title: str | None, text: str | None, links: list[str], metadata: dict[str, object], attempts: int, failure_reason: str | None)`.
- `InternalCrawler.fetch(url: str) -> CrawlerResult`.

- [ ] Write failing tests for headers, timeout/retry, redirect handling, charset decoding, HTML title/text/link extraction, allowlist, rate limiting, maximum response size, and explicit restricted/JS/login classification.
- [ ] Run `py -3 -m pytest tests/unit/test_crawler.py -q` and confirm the new tests fail.
- [ ] Implement bounded request streaming, retry handling, redirect allowlist checks, decoding, and standard-library HTML extraction.
- [ ] Implement safe structured request records exposed through `CrawlerResult.metadata`.
- [ ] Re-run the focused crawler tests and confirm they pass.

### Task 3: Integrate source adaptation and external-first fallback

**Files:**
- Modify: `src/fund_agent/sources/http.py`
- Modify: `src/fund_agent/sources/protocol.py`
- Modify: `src/fund_agent/sources/__init__.py`
- Modify: `src/fund_agent/application.py`
- Test: `tests/unit/test_sources.py`
- Test: `tests/integration/test_application_flow.py`

**Interfaces:**
- `InternalCrawlerSource(endpoint: str, source_type: SourceType, crawler: InternalCrawler, ...) -> SourceAdapter`.
- `SourceRouter(external: SourceAdapter | None, internal: SourceAdapter) -> SourceAdapter`.
- Existing normalized `Evidence` remains the only contract consumed by tracking, rules, screening, and portfolio services.

- [ ] Write failing tests for external success, external failure followed by internal success, malformed/empty external output, and no external configuration.
- [ ] Run the focused source/integration tests and confirm the new assertions fail.
- [ ] Implement internal result-to-evidence normalization and fallback metadata without changing business-rule call sites.
- [ ] Update application composition so every configured public endpoint has an internal source and uses the external adapter first when configured.
- [ ] Re-run focused source/integration tests and confirm they pass.

### Task 4: Document and expose operational status

**Files:**
- Modify: `docs/coding/API.md`
- Modify: `docs/coding/TSD/2026-08-27-fund-agent-v1-minimal-design.md`
- Modify: `docs/project-structure.md`
- Modify: `docs/project-conventions.md`
- Modify: `README.md`
- Modify: `src/fund_agent/static/index.html`
- Test: `tests/integration/test_api.py`

**Interfaces:**
- Existing tracking response exposes source status and evidence metadata including crawler path and failure/fallback details.
- `/api/settings` exposes non-secret crawler settings only.

- [ ] Write failing API assertions for source status, fallback metadata, and the non-secret crawler configuration view.
- [ ] Implement the smallest UI/API changes needed to display source path, failure reason, and fallback state.
- [ ] Update setup examples with internal crawler defaults and optional external variables, without real credentials.
- [ ] Run focused API tests and confirm they pass.

### Task 5: Full verification and acceptance audit

**Files:**
- Modify: `docs/requirements/fund-agent-v1-requirements.md` only if status wording needs evidence-backed correction.

- [ ] Run `py -3 -m pytest` and record all failures, distinguishing pre-existing failures from crawler regressions.
- [ ] Run `D:\skills\bootstrap-project-governance\scripts\validate_project_docs.py C:\Users\31800\Documents\ChatGPT\TouzhiAgent\.worktrees\internal-crawler`.
- [ ] Run `py -3 -m compileall src tests`.
- [ ] Search the diff for credentials, direct provider calls, and silent fallback behavior.
- [ ] Review the final diff and report the worktree path, tests, known baseline failures, required runtime configuration, and AC-04/AC-06 evidence.
