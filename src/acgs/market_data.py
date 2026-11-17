import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal, InvalidOperation
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
            return isinstance(value, dict) and _records(
                value.get("symbols"), _symbol_info, allow_empty=False
            )
        case "fundingInfo":
            return _records(value, _funding_info, allow_empty=True)
        case "premiumIndex":
            return _records(value, _premium_index, allow_empty=False)
        case "depth":
            return (
                isinstance(value, dict)
                and _count(value.get("lastUpdateId"))
                and all(_book_side(value.get(side)) for side in ("bids", "asks"))
            )
        case _:
            return False


def _records(
    value: object, check: Callable[[object], bool], *, allow_empty: bool
) -> bool:
    return isinstance(value, list) and (allow_empty or bool(value)) and all(
        check(item) for item in value
    )


def _name(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _count(value: object, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _decimal(value: object, *, positive: bool = False) -> bool:
    """A finite decimal string, as Binance sends prices and quantities."""
    if not isinstance(value, str):
        return False
    try:
        number = Decimal(value)
    except InvalidOperation:
        return False
    return number.is_finite() and (number > 0 if positive else number >= 0)


def _symbol_info(item: object) -> bool:
    return (
        isinstance(item, dict)
        and _name(item.get("symbol"))
        and isinstance(item.get("status"), str)
        and isinstance(item.get("contractType"), str)
        and isinstance(item.get("filters"), list)
        and all(
            isinstance(entry, dict) and _name(entry.get("filterType"))
            for entry in item["filters"]
        )
    )


def _funding_info(item: object) -> bool:
    return (
        isinstance(item, dict)
        and _name(item.get("symbol"))
        and _count(item.get("fundingIntervalHours"), 1)
    )


def _premium_index(item: object) -> bool:
    # Delivery contracts report nextFundingTime 0, so only negative values are invalid.
    return (
        isinstance(item, dict)
        and _name(item.get("symbol"))
        and _decimal(item.get("markPrice"))
        and _count(item.get("nextFundingTime"))
    )


def _book_side(levels: object) -> bool:
    return isinstance(levels, list) and all(
        isinstance(level, list)
        and len(level) == 2
        and _decimal(level[0], positive=True)
        and _decimal(level[1], positive=True)
        for level in levels
    )
