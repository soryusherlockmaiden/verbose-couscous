import io
import urllib.request
from email.message import Message
from email.utils import formatdate
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.response import addinfourl

import pytest

from acgs import market_data
from acgs.market_data import PublicResponse, fetch_public, retry_delay, valid_payload

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


FIXTURES = Path(__file__).parent / "fixtures"
PREMIUM = '{"symbol":"BTCUSDT","markPrice":"64626.2","nextFundingTime":1794931200000}'


@pytest.mark.parametrize("endpoint", ["exchangeInfo", "fundingInfo", "premiumIndex", "depth"])
def test_recorded_public_responses_are_valid(endpoint: str) -> None:
    assert valid_payload(endpoint, (FIXTURES / f"{endpoint}.json").read_bytes())


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("fundingInfo", "[]"),
        ("premiumIndex", '[{"symbol":"BTCUSDQ","markPrice":"0","nextFundingTime":0}]'),
        ("depth", '{"lastUpdateId":1,"bids":[],"asks":[]}'),
    ],
)
def test_legitimate_edge_responses_are_valid(endpoint: str, payload: str) -> None:
    assert valid_payload(endpoint, payload.encode())


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("exchangeInfo", '{"symbols":[null]}'),
        ("exchangeInfo", '{"symbols":[]}'),
        ("exchangeInfo", '{"symbols":[{"symbol":"BTCUSDT","status":"TRADING"}]}'),
        (
            "exchangeInfo",
            '{"symbols":[{"symbol":"BTCUSDT","status":"TRADING","contractType":"PERPETUAL",'
            '"filters":[{"minQty":"0.001"}]}]}',
        ),
        ("fundingInfo", '[{"symbol":null,"fundingIntervalHours":8}]'),
        ("fundingInfo", '[{"symbol":"BTCUSDT","fundingIntervalHours":"8"}]'),
        ("fundingInfo", '[{"symbol":"BTCUSDT","fundingIntervalHours":0}]'),
        ("fundingInfo", '[{"symbol":"BTCUSDT","fundingIntervalHours":true}]'),
        ("premiumIndex", "[]"),
        (
            "premiumIndex",
            '[{"symbol":null,"markPrice":"not-a-price","nextFundingTime":null}]',
        ),
        ("premiumIndex", PREMIUM.replace('"64626.2"', '"NaN"').join("[]")),
        ("premiumIndex", PREMIUM.replace('"64626.2"', '"-1"').join("[]")),
        ("premiumIndex", PREMIUM.replace("1794931200000", "null").join("[]")),
        ("depth", '{"lastUpdateId":1,"bids":[null],"asks":[]}'),
        ("depth", '{"lastUpdateId":1,"bids":[["64628.6"]],"asks":[]}'),
        ("depth", '{"lastUpdateId":1,"bids":[["0","1"]],"asks":[]}'),
        ("depth", '{"lastUpdateId":1,"bids":[],"asks":[["1","x"]]}'),
        ("depth", '{"lastUpdateId":true,"bids":[],"asks":[]}'),
    ],
)
def test_malformed_records_are_rejected(endpoint: str, payload: str) -> None:
    assert not valid_payload(endpoint, payload.encode())
