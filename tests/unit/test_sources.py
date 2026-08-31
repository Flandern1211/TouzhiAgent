from datetime import datetime, timezone

import httpx

from fund_agent.domain.models import EvidenceStatus, SourceType
from fund_agent.sources.http import CrawlerApiSource, PublicHttpSource


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
                             transport=_transport(status_code=503, text="unavailable"))

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
