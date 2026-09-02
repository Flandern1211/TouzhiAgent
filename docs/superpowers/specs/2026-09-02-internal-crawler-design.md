# Internal Crawler Design

## Goal

Provide a small, controllable internal HTTP crawler that remains usable when no external crawler is configured or when the configured external crawler is unavailable. External crawling is an optional first choice, never the only source of webpage content.

## Scope

The crawler handles public HTTP and HTTPS pages only. It does not log in, reuse unauthorized sessions, bypass CAPTCHA, evade access controls, execute JavaScript, or fabricate content when a page cannot be processed.

## Components

### `InternalCrawler`

`InternalCrawler.fetch(url)` validates the URL and destination host, applies a per-host minimum interval, sends a bounded HTTP request with a stable user agent, follows redirects only when every destination remains allowlisted, and reads at most the configured response limit. It retries transient transport failures and retryable HTTP responses with bounded backoff.

The result contains the final URL, HTTP status, response size, decoded content, extracted HTML fields, attempt count, and a structured failure reason when unsuccessful. HTML extraction returns the title, visible text, links, and selected metadata without executing page scripts. Content type and charset are respected, with BOM, HTML meta charset, and UTF-8 fallback handling.

The crawler classifies unsupported or restricted pages explicitly, including JavaScript-required shells, login-required pages, CAPTCHA/access-denied pages, disallowed domains, redirects outside the allowlist, oversized responses, and unsupported content types. It records safe request URL, timestamps, status, bytes, retry count, failure reason, and whether a fallback was attempted. Query credentials and response bodies are excluded from failure logs.

### `SourceRouter`

`SourceRouter` composes an optional `CrawlerApiSource` and an `InternalCrawler`-backed public source for one `SourceType`. If the external adapter is configured, it runs first. A valid external evidence result is returned as-is. A missing, failed, empty, or malformed external result triggers the internal source, and the final evidence metadata records the external failure and fallback path. If no external adapter is configured, the internal source runs directly.

The router never treats a failed external response as usable evidence and never suppresses an internal failure. The existing business services continue to consume `SourceAdapter` evidence and do not know which crawler was selected.

## Configuration

Existing `FUND_AGENT_CRAWLER_ENDPOINT` and `FUND_AGENT_CRAWLER_API_KEY` configure the optional external adapter. Internal behavior is controlled by:

- `FUND_AGENT_CRAWLER_ALLOWED_DOMAINS`: comma-separated host allowlist; configured public source hosts are included when this is omitted.
- `FUND_AGENT_CRAWLER_TIMEOUT_SECONDS`: request timeout.
- `FUND_AGENT_CRAWLER_MAX_RETRIES`: maximum retries after the first attempt.
- `FUND_AGENT_CRAWLER_MAX_RESPONSE_BYTES`: response size limit.
- `FUND_AGENT_CRAWLER_MIN_INTERVAL_SECONDS`: minimum interval between requests to the same host.
- `FUND_AGENT_CRAWLER_USER_AGENT`: request user agent.
- `FUND_AGENT_CRAWLER_FOLLOW_REDIRECTS`: whether redirects are followed within the allowlist.

Defaults are conservative and suitable for local development. External API keys remain excluded from settings serialization and logs.

## Data flow

```text
configured external crawler?
        yes ──> external request
                    ├─ usable evidence ──> normalized evidence
                    └─ failed/empty/invalid ──> internal crawler
        no  ────────────────────────────────> internal crawler
                                                   ├─ supported page ──> extracted evidence
                                                   └─ unsupported/failed ──> visible failed evidence
```

## Verification

Tests use deterministic `httpx.MockTransport` and an injectable clock/sleep function. They cover request headers, redirects, charset decoding, HTML extraction, retries, allowlist rejection, rate limiting, response limits, explicit unsupported-page reasons, external-first success, external-to-internal fallback, and no-external direct internal fetching. Existing non-crawler baseline failures remain separately reported.
