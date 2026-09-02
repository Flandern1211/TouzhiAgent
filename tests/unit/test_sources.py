from datetime import datetime, timezone

import httpx

from fund_agent.domain.models import Evidence, EvidenceStatus, SourceType
from fund_agent.sources.crawler import InternalCrawler
from fund_agent.sources.http import CrawlerApiSource, InternalCrawlerSource, PublicHttpSource
from fund_agent.sources.http import SourceRouter


class _StaticAdapter:
    def __init__(self, source_type: SourceType, records: list, error: Exception | None = None):
        self.source_type = source_type
        self.records = records
        self.error = error
        self.calls = 0

    def fetch(self, subject: str, since=None) -> list:
        self.calls += 1
        if self.error:
            raise self.error
        return [item.model_copy(update={"subject": subject}) if hasattr(item, "model_copy") else item for item in self.records]


def _transport(status_code=200, payload=None, text=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if payload is not None:
            return httpx.Response(status_code, json=payload, request=request)
        return httpx.Response(status_code, text=text or "", request=request)

    return httpx.MockTransport(handler)


def test_public_source_normalizes_json_records_and_request_metadata():
    collected = datetime(2026, 8, 28, tzinfo=timezone.utc)
    source = PublicHttpSource(
        "https://example.test/facts",
        SourceType.OFFICIAL,
        transport=_transport(
            payload={
                "items": [
                    {"subject": "000001", "content": "公告", "url": "https://origin.test/a", "confidence": 0.9,
                     "effective_at": "2026-08-27T00:00:00+00:00"}
                ]
            }
        ),
        clock=lambda: collected,
    )

    evidence = source.fetch("000001")

    assert len(evidence) == 1
    assert evidence[0].subject == "000001"
    assert evidence[0].source_type is SourceType.OFFICIAL
    assert evidence[0].content == "公告"
    assert evidence[0].collected_at == collected
    assert evidence[0].confidence == 0.9
    assert evidence[0].effective_at == datetime(2026, 8, 27, tzinfo=timezone.utc)


def test_public_source_records_http_failure_without_raising():
    source = PublicHttpSource("https://example.test", SourceType.MARKET,
                             transport=_transport(status_code=503, text="unavailable"),
                             respect_robots=False)

    result = source.fetch("000001")

    assert len(result) == 1
    assert result[0].status is EvidenceStatus.FAILED
    assert result[0].subject == "000001"
    assert result[0].confidence == 0
    assert result[0].content == "source request failed: HTTPStatusError"


def test_crawler_source_sends_api_key_and_normalizes_single_record():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["subject"] = request.url.params["subject"]
        seen["source_type"] = request.url.params["source_type"]
        return httpx.Response(200, json={"subject": "000001", "content": "行情", "status": "estimated"}, request=request)

    source = CrawlerApiSource("https://crawler.test/fetch", "secret-key", SourceType.MARKET,
                              transport=httpx.MockTransport(handler))

    result = source.fetch("000001")

    assert seen == {"authorization": "Bearer secret-key", "subject": "000001", "source_type": "market"}
    assert result[0].status is EvidenceStatus.ESTIMATED
    assert result[0].source_type is SourceType.MARKET


def test_source_accepts_plain_text_as_available_evidence():
    source = PublicHttpSource("https://example.test", SourceType.NEWS,
                             transport=_transport(text="news body"))

    result = source.fetch("000001")

    assert result[0].content == "news body"
    assert result[0].status is EvidenceStatus.AVAILABLE


def test_public_source_expands_subject_in_endpoint_template():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text="page", request=request)

    source = PublicHttpSource(
        "https://example.test/funds/{subject}",
        SourceType.MARKET,
        transport=httpx.MockTransport(handler),
    )

    source.fetch("000001")

    assert "/funds/000001" in seen["url"]


def test_source_failure_redacts_endpoint_query_and_exception_details():
    source = PublicHttpSource(
        "https://example.test/fetch?api_key=secret",
        SourceType.NEWS,
        transport=_transport(status_code=503, text="secret"),
    )

    result = source.fetch("000001")

    assert result[0].url == "https://example.test/fetch"
    assert "secret" not in (result[0].content or "")


def test_public_source_extracts_eastmoney_nav_series_from_data_payload():
    source = PublicHttpSource(
        "https://example.test/nav",
        SourceType.MARKET,
        transport=_transport(
            payload={
                "Data": [
                    {"FSRQ": "2026-08-27", "DWJZ": "1.1000"},
                    {"FSRQ": "2026-08-28", "DWJZ": "1.1200"},
                ]
            }
        ),
    )

    result = source.fetch("000001")

    assert result[0].metadata["nav"] == [1.1, 1.12]
    assert result[0].metadata["latest_value"] == 1.12
    assert result[0].effective_at == datetime(2026, 8, 28, tzinfo=timezone.utc)


def test_source_router_prefers_configured_external_evidence():
    external = _StaticAdapter(
        SourceType.NEWS,
        [
            Evidence(
            source_type=SourceType.NEWS,
            subject="000001",
            collected_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            confidence=1,
            content="external",
            metadata={"crawler": "external"},
            )
        ],
    )
    internal = _StaticAdapter(SourceType.NEWS, [])

    result = SourceRouter(external=external, internal=internal).fetch("000001")

    assert result[0].content == "external"
    assert external.calls == 1
    assert internal.calls == 0
    assert result[0].metadata["crawler"] == "external"


def test_source_router_falls_back_to_internal_when_external_fails():
    external = _StaticAdapter(SourceType.NEWS, [], error=RuntimeError("external unavailable"))
    internal = _StaticAdapter(
        SourceType.NEWS,
        [
            Evidence(
                source_type=SourceType.NEWS,
                subject="000001",
                collected_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
                confidence=1,
                content="internal",
                metadata={"crawler": "internal"},
            )
        ],
    )

    result = SourceRouter(external=external, internal=internal).fetch("000001")

    assert result[0].content == "internal"
    assert result[0].status is EvidenceStatus.AVAILABLE
    assert result[0].metadata["fallback"] is True
    assert result[0].metadata["external_failure_reason"] == "RuntimeError"
    assert external.calls == 1
    assert internal.calls == 1


def test_source_router_falls_back_when_external_returns_empty_or_failed_records():
    external = _StaticAdapter(
        SourceType.NEWS,
        [
            Evidence(
                source_type=SourceType.NEWS,
                subject="000001",
                collected_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
                confidence=0,
                content="external failed",
                status=EvidenceStatus.FAILED,
                metadata={"failure_reason": "http_error"},
            )
        ],
    )
    internal = _StaticAdapter(SourceType.NEWS, [])

    result = SourceRouter(external=external, internal=internal).fetch("000001")

    assert result[0].status is EvidenceStatus.FAILED
    assert result[0].metadata["fallback"] is True
    assert result[0].metadata["external_failure_reason"] == "http_error"
    assert internal.calls == 1


def test_source_router_falls_back_when_external_result_is_malformed():
    external = _StaticAdapter(SourceType.NEWS, [object()])
    internal = _StaticAdapter(
        SourceType.NEWS,
        [Evidence(
            source_type=SourceType.NEWS,
            subject="000001",
            collected_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            confidence=1,
            content="internal valid content",
        )],
    )

    result = SourceRouter(external=external, internal=internal).fetch("000001")

    assert result[0].content == "internal valid content"
    assert result[0].metadata["fallback"] is True
    assert result[0].metadata["external_failure_reason"] == "invalid_or_failed_external_result"


def test_source_router_falls_back_when_external_status_is_unknown():
    external = CrawlerApiSource(
        "https://crawler.test/fetch",
        None,
        SourceType.NEWS,
        transport=_transport(payload={"content": "should not be trusted", "status": "bogus"}),
    )
    internal = _StaticAdapter(
        SourceType.NEWS,
        [Evidence(
            source_type=SourceType.NEWS,
            subject="000001",
            collected_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            confidence=1,
            content="internal valid content",
        )],
    )

    result = SourceRouter(external=external, internal=internal).fetch("000001")

    assert result[0].content == "internal valid content"
    assert result[0].metadata["fallback"] is True


def test_source_router_falls_back_when_external_returns_non_json_payload():
    external = CrawlerApiSource(
        "https://crawler.test/fetch",
        None,
        SourceType.NEWS,
        transport=_transport(text="<html>provider error page</html>"),
    )
    internal = _StaticAdapter(
        SourceType.NEWS,
        [Evidence(
            source_type=SourceType.NEWS,
            subject="000001",
            collected_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            confidence=1,
            content="internal valid content",
        )],
    )

    result = SourceRouter(external=external, internal=internal).fetch("000001")

    assert result[0].content == "internal valid content"
    assert result[0].metadata["fallback"] is True
    assert result[0].metadata["external_failure_reason"] == "invalid_response"


def test_internal_crawler_source_preserves_extraction_metadata_and_failure_reason():
    crawler = InternalCrawler(
        allowed_domains=["example.test"],
        transport=_transport(text="<html><title>公告</title><body>基金经理变更</body></html>"),
        min_interval_seconds=0,
    )
    source = InternalCrawlerSource("https://example.test/notice", SourceType.OFFICIAL, crawler)

    result = source.fetch("000001")

    assert result[0].status is EvidenceStatus.AVAILABLE
    assert result[0].content == "基金经理变更"
    assert result[0].metadata["crawler"] == "internal"
    assert result[0].metadata["title"] == "公告"
