import json
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from http.client import HTTPException, HTTPMessage
from typing import IO
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE_URL = "https://fapi.binance.com"
MAX_PAYLOAD = 16 * 1024 * 1024


@dataclass(frozen=True)
class PublicResponse:
    status: int
    payload: bytes = b""
    retry_after: str | None = None


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        return None


def fetch_public(url: str) -> PublicResponse:
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with build_opener(_NoRedirects()).open(request, timeout=15) as response:
            payload = response.read(MAX_PAYLOAD + 1)
            return PublicResponse(response.status, payload)
    except HTTPError as error:
        try:
            return PublicResponse(
                error.code, retry_after=error.headers.get("Retry-After")
            )
        finally:
            error.close()
    except (URLError, OSError, HTTPException):
        return PublicResponse(0)


def retry_delay(header: str | None, now_seconds: float, minimum: int) -> float:
    if header is None:
        return minimum
    try:
        return max(minimum, int(header))
    except ValueError:
        try:
            date = parsedate_to_datetime(header)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            return max(minimum, date.timestamp() - now_seconds)
        except (ValueError, TypeError, OverflowError):
            return minimum


def valid_payload(endpoint: str, payload: bytes) -> bool:
    if len(payload) > MAX_PAYLOAD:
        return False
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        return False
    match endpoint:
        case "exchangeInfo":
            return (
                isinstance(value, dict)
                and isinstance(value.get("symbols"), list)
                and bool(value["symbols"])
            )
        case "fundingInfo":
            fields = {"symbol", "fundingIntervalHours"}
        case "premiumIndex":
            if not value:
                return False
            fields = {"symbol", "markPrice", "nextFundingTime"}
        case "depth":
            return (
                isinstance(value, dict)
                and type(value.get("lastUpdateId")) is int
                and all(isinstance(value.get(side), list) for side in ("bids", "asks"))
            )
        case _:
            return False
    return isinstance(value, list) and all(
        isinstance(item, dict) and fields <= item.keys() for item in value
    )
