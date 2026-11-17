import io
import urllib.request
from email.message import Message
from email.utils import formatdate
from typing import Any
from urllib.error import URLError
from urllib.response import addinfourl

import pytest

from acgs import market_data
from acgs.market_data import PublicResponse, fetch_public, retry_delay

URL = "https://fapi.binance.com/fapi/v1/premiumIndex"


class FakeHandler(urllib.request.BaseHandler):
    """Answers HTTPS requests in-process; runs before the real HTTPS handler."""

    handler_order = 100

    def __init__(
        self,
        status: int = 200,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.status, self.body, self.error = status, body, error
        self.headers = headers or {}
        self.requests: list[str] = []

    def https_open(self, request: urllib.request.Request) -> addinfourl:
        self.requests.append(request.full_url)
        if self.error is not None:
            raise self.error
        message = Message()
        for name, value in self.headers.items():
            message[name] = value
        response = addinfourl(io.BytesIO(self.body), message, request.full_url, self.status)
        response.msg = "fake"
        return response


def install(monkeypatch: pytest.MonkeyPatch, handler: FakeHandler) -> None:
    def opener(*handlers: Any) -> urllib.request.OpenerDirector:
        return urllib.request.build_opener(*handlers, handler)

    monkeypatch.setattr(market_data, "build_opener", opener)


def test_success_returns_status_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = FakeHandler(200, b"[]")
    install(monkeypatch, handler)
    assert fetch_public(URL) == PublicResponse(200, b"[]")
    assert handler.requests == [URL]


def test_redirect_is_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = FakeHandler(302, headers={"Location": "https://example.invalid/elsewhere"})
    install(monkeypatch, handler)
    assert fetch_public(URL) == PublicResponse(302)
    assert handler.requests == [URL]


def test_rate_limit_keeps_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeHandler(429, headers={"Retry-After": "7"}))
    assert fetch_public(URL) == PublicResponse(429, retry_after="7")


@pytest.mark.parametrize("error", [URLError("down"), TimeoutError(), ConnectionResetError()])
def test_network_failures_map_to_status_zero(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    install(monkeypatch, FakeHandler(error=error))
    assert fetch_public(URL) == PublicResponse(0)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, 300),
        ("7", 300),
        ("900", 900),
        ("-5", 300),
        ("soon", 300),
        (formatdate(1_000_000 + 1_000, usegmt=True), 1_000),
        (formatdate(1_000_000 - 50, usegmt=True), 300),
    ],
)
def test_retry_delay(header: str | None, expected: float) -> None:
    assert retry_delay(header, 1_000_000, 300) == expected
