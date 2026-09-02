"""Small, policy-bound crawler for public HTTP and HTTPS pages."""

from __future__ import annotations

import codecs
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

try:
    import httpx
except ImportError:  # pragma: no cover - exercised only in minimal installs
    httpx = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)
DEFAULT_ACCEPT = "text/html,application/xhtml+xml,text/plain,application/json;q=0.8,*/*;q=0.1"
SUPPORTED_CONTENT_TYPES = {
    "application/json",
    "application/xhtml+xml",
    "text/html",
    "text/plain",
}
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
MAX_REDIRECTS = 5
SENSITIVE_QUERY_KEYS = {"api", "apikey", "api_key", "auth", "authorization", "key", "password", "secret", "token"}


class CrawlerFailureReason(StrEnum):
    INVALID_URL = "invalid_url"
    DOMAIN_NOT_ALLOWED = "domain_not_allowed"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    HTTP_ERROR = "http_error"
    REDIRECT_NOT_ALLOWED = "redirect_not_allowed"
    RESPONSE_TOO_LARGE = "response_too_large"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    JAVASCRIPT_REQUIRED = "javascript_required"
    LOGIN_REQUIRED = "login_required"
    ACCESS_RESTRICTED = "access_restricted"
    EMPTY_CONTENT = "empty_content"
    ROBOTS_NOT_ALLOWED = "robots_not_allowed"
    ROBOTS_UNAVAILABLE = "robots_unavailable"


@dataclass(slots=True)
class CrawlerResult:
    success: bool
    url: str
    final_url: str | None = None
    status_code: int | None = None
    content: str | None = None
    title: str | None = None
    text: str | None = None
    links: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    attempts: int = 0
    failure_reason: CrawlerFailureReason | None = None


class _HtmlExtractor(HTMLParser):
    _SKIPPED_TAGS = {"script", "style", "noscript", "template", "svg"}

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self.meta: dict[str, str] = {}
        self._title_depth = 0
        self._skip_depth = 0
        self.script_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        attributes = {key.lower(): value for key, value in attrs}
        if normalized == "title":
            self._title_depth += 1
        if normalized in self._SKIPPED_TAGS:
            self._skip_depth += 1
            if normalized == "script":
                self.script_count += 1
        if normalized == "a":
            href = attributes.get("href")
            if href:
                resolved = urljoin(self.base_url, href.strip())
                if urlsplit(resolved).scheme in {"http", "https"} and resolved not in self.links:
                    self.links.append(resolved)
        if normalized == "meta":
            key = attributes.get("name") or attributes.get("property")
            content = attributes.get("content")
            if key and content:
                self.meta[key.lower()] = content.strip()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized == "title" and self._title_depth:
            self._title_depth -= 1
        if normalized in self._SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self.title_parts.append(data)
        if not self._skip_depth and not self._title_depth:
            self.text_parts.append(data)


class _RobotsParser:
    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self.parser = RobotFileParser()

    def parse(self, content: str) -> None:
        self.parser.parse(content.splitlines())

    def can_fetch(self, url: str) -> bool:
        return self.parser.can_fetch(self.user_agent, url)


def redact_url(value: str) -> str:
    """Keep traceable URL structure while removing credentials from query strings."""

    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        port = ""
    safe_netloc = f"{hostname}{port}"
    safe_query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in SENSITIVE_QUERY_KEYS
    ]
    return urlunsplit((parsed.scheme, safe_netloc, parsed.path, urlencode(safe_query), ""))


def _normalize_domain(value: str) -> str:
    value = value.strip().lower()
    if "://" in value:
        value = urlsplit(value).hostname or ""
    if value.startswith("*."):
        value = value[2:]
    return value.rstrip(".")


def _decode(raw: bytes, content_type: str) -> str:
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig", errors="replace")
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode("utf-16", errors="replace")
    if raw.startswith(codecs.BOM_UTF32_LE) or raw.startswith(codecs.BOM_UTF32_BE):
        return raw.decode("utf-32", errors="replace")
    declared = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", content_type, flags=re.IGNORECASE)
    if declared is None:
        prefix = raw[:4096].decode("ascii", errors="ignore")
        declared = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", prefix, flags=re.IGNORECASE)
    encoding = declared.group(1) if declared else "utf-8"
    try:
        codecs.lookup(encoding)
    except LookupError:
        encoding = "utf-8"
    return raw.decode(encoding, errors="replace")


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class InternalCrawler:
    """Fetch public pages under an explicit host, size, and rate policy."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        max_response_bytes: int = 2_000_000,
        min_interval_seconds: float = 0.25,
        allowed_domains: Sequence[str] = (),
        user_agent: str = "TouzhiAgent/0.1",
        follow_redirects: bool = True,
        respect_robots: bool = True,
        transport: object | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_response_bytes = max_response_bytes
        self.min_interval_seconds = min_interval_seconds
        self.allowed_domains = tuple(
            domain for domain in (_normalize_domain(item) for item in allowed_domains) if domain
        )
        self.user_agent = user_agent
        self.follow_redirects = follow_redirects
        self.respect_robots = respect_robots
        self.transport = transport
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep
        self._last_request_at: dict[str, float] = {}
        self._robots_cache: dict[str, tuple[bool, str | None]] = {}

    def fetch(self, url: str) -> CrawlerResult:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return self._failure(url, CrawlerFailureReason.INVALID_URL)
        if not self._allowed(parsed.hostname):
            return self._failure(url, CrawlerFailureReason.DOMAIN_NOT_ALLOWED)
        if httpx is None:
            return self._failure(url, CrawlerFailureReason.TRANSPORT_ERROR, detail="httpx_missing")

        started_at = self.clock()
        current_url = url
        attempts = 0
        redirects = 0
        last_status: int | None = None
        last_error: CrawlerFailureReason | None = None
        client_headers = {
            "User-Agent": self.user_agent,
            "Accept": DEFAULT_ACCEPT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        }

        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                headers=client_headers,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                while True:
                    if self.respect_robots:
                        robots_result = self._check_robots(client, current_url)
                        if robots_result is not None:
                            reason, detail = robots_result
                            return self._failure(
                                url,
                                reason,
                                attempts=attempts,
                                started_at=started_at,
                                final_url=current_url,
                                detail=detail,
                            )
                    response_data = None
                    for retry_index in range(self.max_retries + 1):
                        attempts += 1
                        host = urlsplit(current_url).hostname or ""
                        self._wait_for_host(host)
                        try:
                            response_data = self._request_once(client, current_url)
                        except httpx.TimeoutException:
                            last_error = CrawlerFailureReason.TIMEOUT
                            if retry_index < self.max_retries:
                                self.sleeper(0.25 * (2**retry_index))
                                continue
                            return self._failure(
                                url,
                                last_error,
                                attempts=attempts,
                                started_at=started_at,
                                final_url=current_url,
                            )
                        except httpx.TransportError:
                            last_error = CrawlerFailureReason.TRANSPORT_ERROR
                            if retry_index < self.max_retries:
                                self.sleeper(0.25 * (2**retry_index))
                                continue
                            return self._failure(
                                url,
                                last_error,
                                attempts=attempts,
                                started_at=started_at,
                                final_url=current_url,
                            )

                        last_status = response_data.status_code
                        if last_status in RETRYABLE_STATUS_CODES and retry_index < self.max_retries:
                            self.sleeper(0.25 * (2**retry_index))
                            continue
                        break

                    if response_data is None:
                        return self._failure(
                            url,
                            last_error or CrawlerFailureReason.TRANSPORT_ERROR,
                            attempts=attempts,
                            started_at=started_at,
                            final_url=current_url,
                        )

                    status_code = response_data.status_code
                    if 300 <= status_code < 400:
                        location = response_data.headers.get("location")
                        next_url = urljoin(current_url, location or "")
                        next_host = urlsplit(next_url).hostname
                        if not self.follow_redirects or not location or not next_host or not self._allowed(next_host):
                            return self._failure(
                                url,
                                CrawlerFailureReason.REDIRECT_NOT_ALLOWED,
                                attempts=attempts,
                                started_at=started_at,
                                final_url=current_url,
                                status_code=status_code,
                                redirects=redirects,
                            )
                        redirects += 1
                        if redirects > MAX_REDIRECTS:
                            return self._failure(
                                url,
                                CrawlerFailureReason.REDIRECT_NOT_ALLOWED,
                                attempts=attempts,
                                started_at=started_at,
                                final_url=current_url,
                                status_code=status_code,
                                redirects=redirects,
                            )
                        current_url = next_url
                        continue

                    if status_code in {401, 403, 429}:
                        return self._failure(
                            url,
                            CrawlerFailureReason.ACCESS_RESTRICTED,
                            attempts=attempts,
                            started_at=started_at,
                            final_url=current_url,
                            status_code=status_code,
                            redirects=redirects,
                        )
                    if status_code >= 400:
                        return self._failure(
                            url,
                            CrawlerFailureReason.HTTP_ERROR,
                            attempts=attempts,
                            started_at=started_at,
                            final_url=current_url,
                            status_code=status_code,
                            redirects=redirects,
                        )

                    raw = response_data.body or b""
                    if response_data.bytes_read > self.max_response_bytes:
                        return self._failure(
                            url,
                            CrawlerFailureReason.RESPONSE_TOO_LARGE,
                            attempts=attempts,
                            started_at=started_at,
                            final_url=current_url,
                            status_code=status_code,
                            redirects=redirects,
                            bytes_read=response_data.bytes_read,
                        )
                    content_type = response_data.headers.get("content-type", "").lower()
                    media_type = content_type.split(";", 1)[0].strip()
                    if not media_type and raw.lstrip().startswith((b"<", b"{" , b"[")):
                        media_type = "text/html" if raw.lstrip().startswith(b"<") else "application/json"
                    if media_type not in SUPPORTED_CONTENT_TYPES:
                        return self._failure(
                            url,
                            CrawlerFailureReason.UNSUPPORTED_CONTENT_TYPE,
                            attempts=attempts,
                            started_at=started_at,
                            final_url=current_url,
                            status_code=status_code,
                            redirects=redirects,
                            bytes_read=len(raw),
                            content_type=media_type,
                        )

                    decoded = _decode(raw, content_type)
                    title = None
                    text = decoded
                    links: list[str] = []
                    extracted_meta: dict[str, str] = {}
                    looks_like_html = raw.lstrip().lower().startswith((b"<!doctype html", b"<html", b"<head", b"<body"))
                    if media_type in {"text/html", "application/xhtml+xml"} or (media_type in {"", "text/plain"} and looks_like_html):
                        if looks_like_html and media_type == "text/plain":
                            media_type = "text/html"
                        parser = _HtmlExtractor(current_url)
                        try:
                            parser.feed(decoded)
                            parser.close()
                        except ValueError:
                            pass
                        title = _compact_text(" ".join(parser.title_parts)) or None
                        text = _compact_text(" ".join(parser.text_parts))
                        links = parser.links
                        extracted_meta = parser.meta
                        lowered = f"{title or ''} {text}".lower()
                        raw_lower = decoded.lower()
                        if self._contains_access_marker(lowered):
                            return self._failure(
                                url,
                                CrawlerFailureReason.ACCESS_RESTRICTED,
                                attempts=attempts,
                                started_at=started_at,
                                final_url=current_url,
                                status_code=status_code,
                                redirects=redirects,
                                bytes_read=len(raw),
                                content_type=media_type,
                            )
                        if self._contains_login_marker(lowered, current_url, title):
                            return self._failure(
                                url,
                                CrawlerFailureReason.LOGIN_REQUIRED,
                                attempts=attempts,
                                started_at=started_at,
                                final_url=current_url,
                                status_code=status_code,
                                redirects=redirects,
                                bytes_read=len(raw),
                                content_type=media_type,
                            )
                        if self._requires_javascript(lowered, raw_lower, parser.script_count):
                            return self._failure(
                                url,
                                CrawlerFailureReason.JAVASCRIPT_REQUIRED,
                                attempts=attempts,
                                started_at=started_at,
                                final_url=current_url,
                                status_code=status_code,
                                redirects=redirects,
                                bytes_read=len(raw),
                                content_type=media_type,
                            )
                        if not text:
                            return self._failure(
                                url,
                                CrawlerFailureReason.EMPTY_CONTENT,
                                attempts=attempts,
                                started_at=started_at,
                                final_url=current_url,
                                status_code=status_code,
                                redirects=redirects,
                                bytes_read=len(raw),
                                content_type=media_type,
                            )
                    elif not decoded.strip():
                        return self._failure(
                            url,
                            CrawlerFailureReason.EMPTY_CONTENT,
                            attempts=attempts,
                            started_at=started_at,
                            final_url=current_url,
                            status_code=status_code,
                            redirects=redirects,
                            bytes_read=len(raw),
                            content_type=media_type,
                        )

                    metadata = self._metadata(
                        url,
                        current_url,
                        attempts=attempts,
                        started_at=started_at,
                        status_code=status_code,
                        redirects=redirects,
                        bytes_read=len(raw),
                        content_type=media_type,
                    )
                    if extracted_meta:
                        metadata["html_meta"] = extracted_meta
                    result = CrawlerResult(
                        success=True,
                        url=redact_url(url),
                        final_url=redact_url(current_url),
                        status_code=status_code,
                        content=decoded,
                        title=title,
                        text=text,
                        links=links,
                        metadata=metadata,
                        attempts=attempts,
                    )
                    LOGGER.info("crawler request completed", extra={"crawler_event": metadata})
                    return result
        except Exception as exc:
            LOGGER.error(
                "crawler request crashed",
                extra={"crawler_event": {"crawler": "internal", "request_url": redact_url(url), "failure_detail": exc.__class__.__name__}},
            )
            return self._failure(
                url,
                CrawlerFailureReason.TRANSPORT_ERROR,
                attempts=attempts,
                started_at=started_at,
                final_url=current_url,
                detail=exc.__class__.__name__,
            )

    def _request_once(self, client: object, url: str) -> object:
        with client.stream("GET", url) as response:  # type: ignore[union-attr]
            status_code = response.status_code
            headers = response.headers
            content_length = headers.get("content-length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = 0
                if declared_size > self.max_response_bytes:
                    return _ResponseData(status_code, headers, b"", declared_size)
            if 300 <= status_code < 400 or status_code >= 400:
                return _ResponseData(status_code, headers, None, 0)
            chunks: list[bytes] = []
            bytes_read = 0
            for chunk in response.iter_bytes():
                bytes_read += len(chunk)
                if bytes_read > self.max_response_bytes:
                    return _ResponseData(status_code, headers, b"", bytes_read)
                chunks.append(chunk)
            return _ResponseData(status_code, headers, b"".join(chunks), bytes_read)

    def _wait_for_host(self, host: str) -> None:
        if self.min_interval_seconds <= 0:
            self._last_request_at[host] = self.clock()
            return
        now = self.clock()
        last = self._last_request_at.get(host)
        if last is not None:
            wait_for = self.min_interval_seconds - (now - last)
            if wait_for > 0:
                self.sleeper(wait_for)
                now = self.clock()
        self._last_request_at[host] = now

    def _check_robots(self, client: object, url: str) -> tuple[CrawlerFailureReason, str] | None:
        parsed = urlsplit(url)
        host_key = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        cached = self._robots_cache.get(host_key)
        if cached is not None:
            allowed, detail = cached
            return None if allowed else (CrawlerFailureReason.ROBOTS_NOT_ALLOWED, detail or "robots_disallow")

        robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
        self._wait_for_host(parsed.hostname or "")
        try:
            response = self._request_once(client, robots_url)
        except httpx.TimeoutException:
            self._robots_cache[host_key] = (False, "robots_timeout")
            return CrawlerFailureReason.ROBOTS_UNAVAILABLE, "robots_timeout"
        except httpx.TransportError:
            self._robots_cache[host_key] = (False, "robots_transport_error")
            return CrawlerFailureReason.ROBOTS_UNAVAILABLE, "robots_transport_error"

        if response.status_code == 404:
            self._robots_cache[host_key] = (True, None)
            return None
        if 300 <= response.status_code < 400:
            self._robots_cache[host_key] = (False, "robots_redirect_not_followed")
            return CrawlerFailureReason.ROBOTS_UNAVAILABLE, "robots_redirect_not_followed"
        if response.status_code in {401, 403}:
            self._robots_cache[host_key] = (False, f"robots_http_{response.status_code}")
            return CrawlerFailureReason.ROBOTS_UNAVAILABLE, f"robots_http_{response.status_code}"
        if response.status_code >= 400:
            self._robots_cache[host_key] = (False, f"robots_http_{response.status_code}")
            return CrawlerFailureReason.ROBOTS_UNAVAILABLE, f"robots_http_{response.status_code}"

        if response.bytes_read > self.max_response_bytes:
            self._robots_cache[host_key] = (False, "robots_response_too_large")
            return CrawlerFailureReason.ROBOTS_UNAVAILABLE, "robots_response_too_large"
        parser = _RobotsParser(self.user_agent)
        parser.parse(_decode(response.body or b"", response.headers.get("content-type", "")))
        allowed = parser.can_fetch(url)
        detail = None if allowed else "robots_disallow"
        self._robots_cache[host_key] = (allowed, detail)
        return None if allowed else (CrawlerFailureReason.ROBOTS_NOT_ALLOWED, detail or "robots_disallow")

    def _allowed(self, host: str) -> bool:
        normalized = host.lower().rstrip(".")
        return any(normalized == domain or normalized.endswith(f".{domain}") for domain in self.allowed_domains)

    def _metadata(
        self,
        url: str,
        final_url: str,
        *,
        attempts: int,
        started_at: float,
        status_code: int | None,
        redirects: int,
        bytes_read: int,
        content_type: str | None,
    ) -> dict[str, object]:
        return {
            "crawler": "internal",
            "request_url": redact_url(url),
            "final_url": redact_url(final_url),
            "status_code": status_code,
            "attempts": attempts,
            "redirects": redirects,
            "bytes": bytes_read,
            "content_type": content_type,
            "elapsed_ms": round(max(0.0, self.clock() - started_at) * 1000, 2),
        }

    def _failure(
        self,
        url: str,
        reason: CrawlerFailureReason,
        *,
        attempts: int = 0,
        started_at: float | None = None,
        final_url: str | None = None,
        status_code: int | None = None,
        redirects: int = 0,
        bytes_read: int = 0,
        content_type: str | None = None,
        detail: str | None = None,
    ) -> CrawlerResult:
        metadata: dict[str, object] = {
            "crawler": "internal",
            "request_url": redact_url(url),
            "final_url": redact_url(final_url or url),
            "status_code": status_code,
            "attempts": attempts,
            "redirects": redirects,
            "bytes": bytes_read,
            "content_type": content_type,
            "failure_reason": reason.value,
        }
        if started_at is not None:
            metadata["elapsed_ms"] = round(max(0.0, self.clock() - started_at) * 1000, 2)
        if detail:
            metadata["failure_detail"] = detail
        LOGGER.warning("crawler request failed", extra={"crawler_event": metadata})
        return CrawlerResult(
            success=False,
            url=redact_url(url),
            final_url=redact_url(final_url or url),
            status_code=status_code,
            metadata=metadata,
            attempts=attempts,
            failure_reason=reason,
        )

    @staticmethod
    def _contains_access_marker(value: str) -> bool:
        return any(marker in value for marker in ("captcha", "验证码", "access denied", "访问频繁", "robot check", "blocked"))

    @staticmethod
    def _contains_login_marker(value: str, url: str, title: str | None = None) -> bool:
        path = urlsplit(url).path.lower()
        title_value = (title or "").strip().lower()
        return (
            title_value in {"登录", "login", "sign in", "log in"}
            or any(marker in value for marker in ("请先登录", "用户登录", "登录后查看", "sign in", "log in"))
            or path.rstrip("/").endswith("/login")
        )

    @staticmethod
    def _requires_javascript(value: str, raw: str, script_count: int) -> bool:
        explicit = ("enable javascript", "javascript required", "请启用javascript", "请开启javascript")
        shell = any(marker in raw for marker in ("id='root'", 'id="root"', "id='app'", 'id="app"', "data-reactroot", "__next_data__"))
        return any(marker in value for marker in explicit) or (not value.strip() and script_count > 0 and shell)


@dataclass(slots=True)
class _ResponseData:
    status_code: int
    headers: object
    body: bytes | None
    bytes_read: int
