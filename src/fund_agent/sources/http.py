from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

try:
    import httpx
except ImportError:  # pragma: no cover - exercised only in minimal installs
    httpx = None  # type: ignore[assignment]

from fund_agent.domain.models import Evidence, EvidenceStatus, SourceType
from fund_agent.sources.crawler import CrawlerResult, InternalCrawler, redact_url


def _datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def _safe_url(value: str) -> str:
    return redact_url(value)


def _failure_content(reason: str) -> str:
    # Keep the original adapter wording for callers while metadata carries the precise reason.
    legacy_name = "HTTPStatusError" if reason == "http_error" else reason
    return f"source request failed: {legacy_name}"


class _HttpEvidenceSource:
    def __init__(self, endpoint: str, source_type: SourceType, timeout_seconds: float,
                 transport: Any, clock: Callable[[], datetime] | None,
                 crawler_name: str | None = None):
        self.endpoint = endpoint
        self.source_type = source_type
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.crawler_name = crawler_name

    def _request(self, subject: str, since: datetime | None) -> httpx.Response:
        if httpx is None:
            raise RuntimeError("httpx is required for HTTP source adapters")
        endpoint = self.endpoint.replace("{subject}", quote(subject, safe=""))
        params: dict[str, str] = {"subject": subject, "source_type": self.source_type.value}
        if since is not None:
            params["since"] = since.isoformat()
        with httpx.Client(timeout=self.timeout_seconds, transport=self._transport) as client:
            return client.get(endpoint, params=params)

    def _normalize(
        self,
        payload: Any,
        request_url: str,
        subject: str,
        *,
        base_metadata: dict[str, object] | None = None,
    ) -> list[Evidence]:
        inherited = dict(base_metadata or {})
        if self.crawler_name:
            inherited.setdefault("crawler", self.crawler_name)
        if isinstance(payload, dict):
            data = payload.get("Data") or payload.get("data")
            if isinstance(data, list) and data:
                nav_rows: list[tuple[Any, float]] = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    raw_nav = item.get("DWJZ") or item.get("NAV") or item.get("nav")
                    try:
                        nav_rows.append((item.get("FSRQ") or item.get("date"), float(raw_nav)))
                    except (TypeError, ValueError):
                        continue
                if nav_rows:
                    nav_rows.sort(key=lambda pair: str(pair[0] or ""))
                    latest_date = _datetime(nav_rows[-1][0])
                    metadata = dict(inherited)
                    metadata.update({"nav": [value for _, value in nav_rows], "latest_value": nav_rows[-1][1]})
                    return [Evidence(
                        source_type=self.source_type,
                        subject=subject,
                        collected_at=self._clock(),
                        effective_at=latest_date,
                        url=request_url,
                        content=json.dumps(payload, ensure_ascii=False),
                        confidence=1.0,
                        metadata=metadata,
                    )]
        records = payload if isinstance(payload, list) else payload.get("items", payload.get("results", [payload])) if isinstance(payload, dict) else [payload]
        if not isinstance(records, list):
            records = [records]
        normalized: list[Evidence] = []
        for record in records:
            if isinstance(record, str):
                record = {"content": record}
            if not isinstance(record, dict):
                continue
            status = record.get("status", EvidenceStatus.AVAILABLE)
            try:
                status = EvidenceStatus(status)
            except (TypeError, ValueError):
                status = EvidenceStatus.FAILED
            metadata = dict(inherited)
            raw_metadata = record.get("metadata")
            if isinstance(raw_metadata, dict):
                metadata.update(raw_metadata)
            content = record.get("content")
            if content is not None and not isinstance(content, str):
                content = str(content)
            if status in {EvidenceStatus.AVAILABLE, EvidenceStatus.ESTIMATED} and not str(content or "").strip() and not metadata.get("nav"):
                status = EvidenceStatus.FAILED
                metadata.setdefault("failure_reason", "empty_content")
            if record.get("status") is not None and not isinstance(record.get("status"), EvidenceStatus):
                try:
                    EvidenceStatus(record.get("status"))
                except (TypeError, ValueError):
                    metadata.setdefault("failure_reason", "invalid_status")
            confidence = float(record.get("confidence", 1.0 if status is not EvidenceStatus.FAILED else 0))
            normalized.append(Evidence(
                id=record.get("id"), source_type=self.source_type,
                subject=str(record.get("subject", subject)), collected_at=self._clock(),
                effective_at=_datetime(record.get("effective_at")), url=_safe_url(record.get("url", request_url)),
                content=content,
                confidence=confidence, status=status, metadata=metadata,
            ))
        return normalized

    def fetch(self, subject: str, since: datetime | None = None) -> list[Evidence]:
        try:
            response = self._request(subject, since)
            response.raise_for_status()
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                if self.crawler_name == "external":
                    return [self._failed_evidence(subject, "invalid_response", str(response.url))]
                payload = response.text
            request_url = _safe_url(str(response.url))
            records = self._normalize(payload, request_url, subject)
            if records:
                return records
            return [self._failed_evidence(subject, "empty_content", request_url)]
        except Exception as exc:
            reason = "http_error" if httpx is not None and isinstance(exc, httpx.HTTPStatusError) else exc.__class__.__name__
            return [self._failed_evidence(subject, reason)]

    def _failed_evidence(self, subject: str, reason: str, url: str | None = None) -> Evidence:
        metadata: dict[str, object] = {"failure_reason": reason}
        if self.crawler_name:
            metadata["crawler"] = self.crawler_name
        return Evidence(
            source_type=self.source_type,
            subject=subject,
            collected_at=self._clock(),
            url=_safe_url(url or self.endpoint),
            content=_failure_content(reason),
            confidence=0,
            status=EvidenceStatus.FAILED,
            metadata=metadata,
        )


class InternalCrawlerSource(_HttpEvidenceSource):
    """Adapt an InternalCrawler result to the shared evidence contract."""

    def __init__(
        self,
        endpoint: str,
        source_type: SourceType,
        crawler: InternalCrawler,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(endpoint, source_type, crawler.timeout_seconds, None, clock, crawler_name="internal")
        self.crawler = crawler

    def fetch(self, subject: str, since: datetime | None = None) -> list[Evidence]:
        request_url = self._build_url(subject, since)
        result = self.crawler.fetch(request_url)
        if not result.success:
            return [self._result_failure(subject, result)]
        metadata = dict(result.metadata)
        metadata["crawler"] = "internal"
        if result.title:
            metadata["title"] = result.title
        if result.links:
            metadata["links"] = result.links
        content_type = str(metadata.get("content_type") or "")
        content = result.content or ""
        if content_type == "application/json":
            try:
                payload: object = json.loads(content)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {"content": result.text or content}
        else:
            payload = {"content": result.text or content}
        records = self._normalize(payload, _safe_url(result.final_url or request_url), subject, base_metadata=metadata)
        if records:
            return records
        return [self._result_failure(subject, result, reason="empty_content")]

    def _build_url(self, subject: str, since: datetime | None) -> str:
        endpoint = self.endpoint.replace("{subject}", quote(subject, safe=""))
        parsed = urlsplit(endpoint)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params.setdefault("subject", subject)
        params.setdefault("source_type", self.source_type.value)
        if since is not None:
            params.setdefault("since", since.isoformat())
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), parsed.fragment))

    def _result_failure(self, subject: str, result: CrawlerResult, reason: str | None = None) -> Evidence:
        failure_reason = reason or (result.failure_reason.value if result.failure_reason else "transport_error")
        metadata = dict(result.metadata)
        metadata.update({"crawler": "internal", "failure_reason": failure_reason})
        return Evidence(
            source_type=self.source_type,
            subject=subject,
            collected_at=self._clock(),
            url=_safe_url(self.endpoint),
            content=_failure_content(failure_reason),
            confidence=0,
            status=EvidenceStatus.FAILED,
            metadata=metadata,
        )


class PublicHttpSource(InternalCrawlerSource):
    def __init__(
        self,
        base_url: str,
        source_type: SourceType,
        timeout_seconds: float = 10.0,
        *,
        transport: Any = None,
        clock: Callable[[], datetime] | None = None,
        crawler: InternalCrawler | None = None,
        allowed_domains: Sequence[str] | None = None,
        max_retries: int = 2,
        max_response_bytes: int = 2_000_000,
        min_interval_seconds: float = 0.25,
        user_agent: str = "TouzhiAgent/0.1",
        follow_redirects: bool = True,
        respect_robots: bool = True,
    ) -> None:
        if crawler is None:
            host = urlsplit(base_url).hostname
            domains = tuple(allowed_domains or ((host,) if host else ()))
            crawler = InternalCrawler(
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                max_response_bytes=max_response_bytes,
                min_interval_seconds=min_interval_seconds,
                allowed_domains=domains,
                user_agent=user_agent,
                follow_redirects=follow_redirects,
                respect_robots=respect_robots,
                transport=transport,
            )
        super().__init__(base_url, source_type, crawler, clock=clock)


class CrawlerApiSource(_HttpEvidenceSource):
    def __init__(self, endpoint: str, api_key: str | None, source_type: SourceType,
                 timeout_seconds: float = 20.0, *, transport: Any = None,
                 clock: Callable[[], datetime] | None = None):
        super().__init__(endpoint, source_type, timeout_seconds, transport, clock, crawler_name="external")
        self._api_key = api_key

    def _request(self, subject: str, since: datetime | None) -> httpx.Response:
        if httpx is None:
            raise RuntimeError("httpx is required for HTTP source adapters")
        endpoint = self.endpoint.replace("{subject}", quote(subject, safe=""))
        params: dict[str, str] = {"subject": subject, "source_type": self.source_type.value}
        if since is not None:
            params["since"] = since.isoformat()
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        with httpx.Client(timeout=self.timeout_seconds, transport=self._transport, headers=headers) as client:
            return client.get(endpoint, params=params)
class SourceRouter:
    """Choose a configured external source first and fall back to an internal source."""

    def __init__(self, *, external: Any | None, internal: Any, clock: Callable[[], datetime] | None = None) -> None:
        self.external = external
        self.internal = internal
        self.source_type = getattr(internal, "source_type", getattr(external, "source_type", SourceType.NEWS))
        self.endpoint = getattr(internal, "endpoint", None)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self, subject: str, since: datetime | None = None) -> list[Evidence]:
        if self.external is None:
            return self._mark_internal(self._fetch_internal(subject, since), fallback=False)

        external_reason: str | None = None
        try:
            external_records = list(self.external.fetch(subject, since=since) or [])
            if self._usable(external_records):
                return [self._with_metadata(record, {"crawler": "external", "fallback": False}) for record in external_records]
            external_reason = self._failure_reason(external_records)
        except Exception as exc:
            external_reason = exc.__class__.__name__

        internal_records = self._fetch_internal(subject, since)
        if not internal_records:
            internal_records = [self._router_failure(subject, external_reason or "empty_content", "internal_empty")]
        return [
            self._with_metadata(
                record,
                {
                    "crawler": "internal",
                    "fallback": True,
                    "fallback_from": "external",
                    "external_failure_reason": external_reason or "empty_content",
                },
            )
            for record in internal_records
        ]

    def _fetch_internal(self, subject: str, since: datetime | None) -> list[Evidence]:
        try:
            return list(self.internal.fetch(subject, since=since) or [])
        except Exception as exc:
            return [self._router_failure(subject, exc.__class__.__name__, "internal_exception")]

    @staticmethod
    def _usable(records: list[Any]) -> bool:
        return any(
            isinstance(record, Evidence)
            and record.status in {EvidenceStatus.AVAILABLE, EvidenceStatus.ESTIMATED}
            and (bool(str(record.content or "").strip()) or bool(record.metadata.get("nav")))
            for record in records
        )

    @staticmethod
    def _failure_reason(records: list[Any]) -> str:
        for record in records:
            if isinstance(record, Evidence):
                reason = record.metadata.get("failure_reason")
                if reason:
                    return str(reason)
        return "empty_content" if not records else "invalid_or_failed_external_result"

    @staticmethod
    def _with_metadata(record: Evidence, updates: dict[str, object]) -> Evidence:
        metadata = dict(record.metadata)
        metadata.update(updates)
        return record.model_copy(update={"metadata": metadata})

    @staticmethod
    def _mark_internal(records: list[Evidence], *, fallback: bool) -> list[Evidence]:
        return [
            SourceRouter._with_metadata(record, {"crawler": "internal", "fallback": fallback})
            for record in records
        ]

    def _router_failure(self, subject: str, reason: str, stage: str) -> Evidence:
        return Evidence(
            source_type=self.source_type,
            subject=subject,
            collected_at=self._clock(),
            url=_safe_url(self.endpoint or ""),
            content=_failure_content(reason),
            confidence=0,
            status=EvidenceStatus.FAILED,
            metadata={"crawler": "internal", "failure_reason": reason, "failure_stage": stage},
        )


__all__ = ["CrawlerApiSource", "InternalCrawlerSource", "PublicHttpSource", "SourceRouter"]
