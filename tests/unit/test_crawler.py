from __future__ import annotations

import httpx
import pytest

from fund_agent.sources.crawler import CrawlerFailureReason, InternalCrawler


def test_internal_crawler_sends_headers_extracts_html_and_records_request_metadata():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["user-agent"] = request.headers["user-agent"]
        seen["accept"] = request.headers["accept"]
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=(
                "<html><head><title>基金公告</title></head>"
                "<body><h1>公告标题</h1><p>净值信息已更新</p>"
                "<a href='/notice/1'>原文</a></body></html>"
            ).encode(),
            request=request,
        )

    crawler = InternalCrawler(
        allowed_domains=["example.test"],
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0,
        respect_robots=False,
    )

    result = crawler.fetch("https://example.test/page")

    assert result.success is True
    assert result.title == "基金公告"
    assert "净值信息已更新" in (result.text or "")
    assert result.links == ["https://example.test/notice/1"]
    assert seen == {"user-agent": "TouzhiAgent/0.1", "accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.8,*/*;q=0.1"}
    assert result.metadata["crawler"] == "internal"
    assert result.metadata["attempts"] == 1
    assert result.metadata["bytes"] > 0
    assert result.metadata["final_url"] == "https://example.test/page"


def test_internal_crawler_decodes_declared_gb18030_content():
    body = "基金经理变更公告".encode("gb18030")

    crawler = InternalCrawler(
        allowed_domains=["example.test"],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html; charset=gb18030"},
                content=body,
                request=request,
            )
        ),
        min_interval_seconds=0,
        respect_robots=False,
    )

    result = crawler.fetch("https://example.test/notice")

    assert result.success is True
    assert result.text == "基金经理变更公告"


def test_internal_crawler_retries_timeout_and_records_attempts():
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="ok", request=request)

    crawler = InternalCrawler(
        allowed_domains=["example.test"],
        max_retries=1,
        sleeper=sleeps.append,
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0,
        respect_robots=False,
    )

    result = crawler.fetch("https://example.test/retry")

    assert result.success is True
    assert result.attempts == 2
    assert sleeps == [0.25]


def test_internal_crawler_rejects_disallowed_domain_before_request():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text="unexpected", request=request)

    crawler = InternalCrawler(
        allowed_domains=["allowed.test"],
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0,
        respect_robots=False,
    )

    result = crawler.fetch("https://blocked.test/page")

    assert result.success is False
    assert result.failure_reason == CrawlerFailureReason.DOMAIN_NOT_ALLOWED
    assert called is False
    assert result.metadata["failure_reason"] == "domain_not_allowed"


def test_internal_crawler_follows_allowlisted_redirect():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "/final"}, request=request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="final", request=request)

    crawler = InternalCrawler(
        allowed_domains=["example.test"],
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0,
        respect_robots=False,
    )

    result = crawler.fetch("https://example.test/start")

    assert result.success is True
    assert seen == ["https://example.test/start", "https://example.test/final"]
    assert result.metadata["redirects"] == 1


def test_internal_crawler_rejects_redirect_outside_allowlist():
    crawler = InternalCrawler(
        allowed_domains=["example.test"],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"location": "https://other.test/page"}, request=request)
        ),
        min_interval_seconds=0,
        respect_robots=False,
    )

    result = crawler.fetch("https://example.test/start")

    assert result.success is False
    assert result.failure_reason == CrawlerFailureReason.REDIRECT_NOT_ALLOWED


def test_internal_crawler_rejects_response_larger_than_limit():
    crawler = InternalCrawler(
        allowed_domains=["example.test"],
        max_response_bytes=4,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/plain", "content-length": "5"},
                content=b"12345",
                request=request,
            )
        ),
        min_interval_seconds=0,
        respect_robots=False,
    )

    result = crawler.fetch("https://example.test/large")

    assert result.success is False
    assert result.failure_reason == CrawlerFailureReason.RESPONSE_TOO_LARGE
    assert result.metadata["bytes"] == 5


def test_internal_crawler_reports_javascript_login_and_access_restrictions():
    cases = [
        ("<html><body><div id='root'></div><script src='/app.js'></script></body></html>", CrawlerFailureReason.JAVASCRIPT_REQUIRED),
        ("<html><title>登录</title><body>请先登录后查看</body></html>", CrawlerFailureReason.LOGIN_REQUIRED),
        ("<html><body>验证码 CAPTCHA access denied</body></html>", CrawlerFailureReason.ACCESS_RESTRICTED),
    ]

    for html, expected in cases:
        crawler = InternalCrawler(
            allowed_domains=["example.test"],
            transport=httpx.MockTransport(
                lambda request, html=html: httpx.Response(
                    200,
                    headers={"content-type": "text/html; charset=utf-8"},
                    text=html,
                    request=request,
                )
            ),
            min_interval_seconds=0,
            respect_robots=False,
        )

        result = crawler.fetch("https://example.test/restricted")

        assert result.success is False
        assert result.failure_reason == expected


def test_internal_crawler_waits_between_requests_to_same_host():
    now = 0.0
    sleeps: list[float] = []

    def clock() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    crawler = InternalCrawler(
        allowed_domains=["example.test"],
        min_interval_seconds=2.0,
        clock=clock,
        sleeper=sleep,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "text/plain"}, text="ok", request=request)
        ),
        respect_robots=False,
    )

    assert crawler.fetch("https://example.test/one").success is True
    assert crawler.fetch("https://example.test/two").success is True

    assert sleeps == [2.0]


def test_internal_crawler_respects_robots_disallow():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private", request=request)
        return httpx.Response(200, text="should not be fetched", request=request)

    crawler = InternalCrawler(
        allowed_domains=["example.test"],
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0,
    )

    result = crawler.fetch("https://example.test/private/page")

    assert result.success is False
    assert result.failure_reason == CrawlerFailureReason.ROBOTS_NOT_ALLOWED
    assert seen == ["https://example.test/robots.txt"]


def test_internal_crawler_allows_site_without_robots_file():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="public", request=request)

    crawler = InternalCrawler(
        allowed_domains=["example.test"],
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0,
    )

    result = crawler.fetch("https://example.test/public")

    assert result.success is True
    assert seen == ["https://example.test/robots.txt", "https://example.test/public"]


def test_internal_crawler_does_not_log_raw_exception_details(caplog: pytest.LogCaptureFixture):
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("secret-token=do-not-log")

    crawler = InternalCrawler(
        allowed_domains=["example.test"],
        max_retries=0,
        transport=httpx.MockTransport(handler),
        min_interval_seconds=0,
        respect_robots=False,
    )

    with caplog.at_level("WARNING"):
        result = crawler.fetch("https://example.test/fetch?token=secret-token")

    assert result.success is False
    assert "secret-token" not in caplog.text
    assert "RuntimeError" in str(result.metadata["failure_detail"])


def test_internal_crawler_classifies_login_page_from_title():
    crawler = InternalCrawler(
        allowed_domains=["example.test"],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html><head><title>登录</title></head><body><form></form></body></html>",
                request=request,
            )
        ),
        min_interval_seconds=0,
        respect_robots=False,
    )

    result = crawler.fetch("https://example.test/account")

    assert result.success is False
    assert result.failure_reason.value == "login_required"
