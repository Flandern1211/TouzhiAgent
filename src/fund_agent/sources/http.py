from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote, urlsplit, urlunsplit

try:
    import httpx
except ImportError:  # pragma: no cover - exercised only in minimal installs
    httpx = None  # type: ignore[assignment]

from fund_agent.domain.models import Evidence, EvidenceStatus, SourceType


def _datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


class _HttpEvidenceSource:
    def __init__(self, endpoint: str, source_type: SourceType, timeout_seconds: float,
                 transport: Any, clock: Callable[[], datetime] | None):
        self.endpoint = endpoint
        self.source_type = source_type
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _request(self, subject: str, since: datetime | None) -> httpx.Response:
        if httpx is None:
            raise RuntimeError("httpx is required for HTTP source adapters")
        endpoint = self.endpoint.replace("{subject}", quote(subject, safe=""))
        params: dict[str, str] = {"subject": subject, "source_type": self.source_type.value}
        if since is not None:
            params["since"] = since.isoformat()
        with httpx.Client(timeout=self.timeout_seconds, transport=self._transport) as client:
            return client.get(endpoint, params=params)

    def _normalize(self, payload: Any, request_url: str, subject: str) -> list[Evidence]:
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
                    return [Evidence(
                        source_type=self.source_type,
                        subject=subject,
                        collected_at=self._clock(),
                        effective_at=latest_date,
                        url=request_url,
                        content=json.dumps(payload, ensure_ascii=False),
                        confidence=1.0,
                        metadata={"nav": [value for _, value in nav_rows], "latest_value": nav_rows[-1][1]},
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
            except ValueError:
                status = EvidenceStatus.AVAILABLE
            normalized.append(Evidence(
                id=record.get("id"), source_type=self.source_type,
                subject=str(record.get("subject", subject)), collected_at=self._clock(),
                effective_at=_datetime(record.get("effective_at")),
                url=record.get("url", request_url), content=record.get("content"),
                confidence=float(record.get("confidence", 1.0)), status=status,
                metadata=record.get("metadata", {}),
            ))
        return normalized

    def fetch(self, subject: str, since: datetime | None = None) -> list[Evidence]:
        try:
            response = self._request(subject, since)
            response.raise_for_status()
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                payload = response.text
            return self._normalize(payload, str(response.url), subject)
        except Exception as exc:
            return [Evidence(source_type=self.source_type, subject=subject, collected_at=self._clock(),
                             url=_safe_url(self.endpoint), content=f"source request failed: {exc.__class__.__name__}", confidence=0,
                             status=EvidenceStatus.FAILED)]


class PublicHttpSource(_HttpEvidenceSource):
    def __init__(self, base_url: str, source_type: SourceType, timeout_seconds: float = 10.0,
                 *, transport: Any = None, clock: Callable[[], datetime] | None = None):
        super().__init__(base_url, source_type, timeout_seconds, transport, clock)


class CrawlerApiSource(_HttpEvidenceSource):
    def __init__(self, endpoint: str, api_key: str | None, source_type: SourceType,
                              timeout_seconds: float = 20.0, *, transport: Any = None,
                 clock: Callable[[], datetime] | None = None):
        super().__init__(endpoint, source_type, timeout_seconds, transport, clock)
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
