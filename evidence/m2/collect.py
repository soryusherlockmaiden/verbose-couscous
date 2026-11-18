import argparse
import hashlib
import http.client
import json
import re
import socket
import time
from calendar import monthrange
from collections import Counter
from collections.abc import Collection
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from acgs.history_seal import registered_seal, utc_ms

ROOT = Path("data/o2-inventory")
DISCOVERY = Path(__file__).with_name("o2-discovery.json")
START = "2023-01-01T00:00:00Z"
CUTOFF = "2025-01-01T00:00:00Z"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "DGBUSDT")
SERIES: list[tuple[str, str | None]] = [
    (kind, interval) for kind in ("klines", "markPriceKlines") for interval in ("15m", "1h", "1d")
]
SERIES.append(("fundingRate", None))
ARCHIVE = "data.binance.vision"
NOTICES = "www.binance.com"
ACCESS_STOP = {401, 403, 418, 429, 451}
DGB_NOTICE = "https://www.binance.com/en/support/announcement/detail/ce74360a3fd444b2b35da7186f8dd0ad"
M1_REF = "evidence/m1/binance-2024.json"
MAX_NOTICE_BYTES = 8 * 1024 * 1024
ARCHIVE_KEY = re.compile(
    r"/data/futures/um/(monthly|daily)/(klines|markPriceKlines|fundingRate)/"
    r"(BTCUSDT|ETHUSDT|DGBUSDT)/(?:([0-9]+[mhd])/)?[^/]+\.zip"
)


def utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def write(name: str, value: Any) -> None:
    (ROOT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def archive_url(symbol: str, label: str, kind: str, interval: str | None) -> str:
    period = "daily" if len(label) == 10 else "monthly"
    subdir = f"/{interval}" if interval else ""
    return f"https://{ARCHIVE}/data/futures/um/{period}/{kind}/{symbol}{subdir}/{symbol}-{interval or kind}-{label}.zip"


def checksum_path(root: Path, zip_url: str) -> Path:
    return root / "checksums" / (urlsplit(zip_url).path.lstrip("/") + ".CHECKSUM")


def request_path(host: str, method: str, url: str, limit: int, notice_urls: Collection[str] = ()) -> str:
    """Allow only direct keys from the approved inventory and the selected notices."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != host or parsed.query or parsed.fragment:
        raise ValueError("Expected a direct HTTPS URL for the selected host")
    if host == ARCHIVE:
        if method == "HEAD" and limit == 0:
            zip_url = url
        elif method == "GET" and 0 < limit <= 2048 and url.endswith(".zip.CHECKSUM"):
            zip_url = url.removesuffix(".CHECKSUM")
        else:
            raise ValueError("Archive requests permit only HEAD ZIP and GET CHECKSUM")
        path = urlsplit(zip_url).path
        match = ARCHIVE_KEY.fullmatch(path)
        if match is None:
            raise ValueError("Expected an exact archive key")
        period, kind, symbol, interval = match.groups()
        prefix = f"{symbol}-{interval or kind}-"
        filename = path.rsplit("/", 1)[-1]
        label = filename.removeprefix(prefix).removesuffix(".zip")
        calendar_date = label + "-01" if period == "monthly" else label
        if (
            (kind, interval) not in SERIES or
            len(label) != (7 if period == "monthly" else 10) or
            date.fromisoformat(calendar_date).isoformat() != calendar_date or
            not START[:10] <= calendar_date < CUTOFF[:10] or
            zip_url != archive_url(symbol, label, kind, interval)
        ):
            raise ValueError("Archive key is outside the approved scope")
        if symbol == "DGBUSDT" and label >= "2024-05":
            raise ValueError("DGBUSDT archive is outside the active period")
        if symbol == "DGBUSDT" and period == "daily" and label > "2024-04-01":
            raise ValueError("DGBUSDT daily archive is outside the active period")
        start = utc_ms(calendar_date + "T00:00:00Z")
        registered_seal().check_asset({
            "source": zip_url, "id": symbol, "kind": kind, "start": start, "end": start + 1,
        })
    elif (
        host != NOTICES or method != "GET" or not 0 < limit <= MAX_NOTICE_BYTES or url not in notice_urls or
        not re.fullmatch(r"/en/(?:support/announcement/detail|blog/all)/[A-Za-z0-9]+", parsed.path)
    ):
        raise ValueError("Expected an allowlisted Binance notice URL")
    return parsed.path


def discovery() -> dict[str, Any]:
    record: dict[str, Any] = json.loads(DISCOVERY.read_text(encoding="utf-8"))
    if record["cutoff_exclusive"] != CUTOFF or any(
        not re.fullmatch(r"[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?", item["indexed_publication_period"]) or
        not "2019" <= item["indexed_publication_period"] < CUTOFF[:10]
        for item in record["selected_candidates"]
    ):
        raise ValueError("Discovery candidates must have pre-cutoff indexed publication periods")
    return record


class Client:
    def __init__(self, host: str, log_name: str, notice_urls: Collection[str] = ()) -> None:
        self.host = host
        self.blocked: dict[str, Any] | None = None
        self.log = ROOT / log_name
        self.notice_urls = notice_urls
        self.log.touch(exist_ok=False)
        self.conn = http.client.HTTPSConnection(host, timeout=12)

    def check(self, method: str, url: str, limit: int = 0) -> tuple[dict[str, Any], bytes]:
        path = request_path(self.host, method, url, limit, self.notice_urls)
        if self.blocked:
            return {"reason": "HOST_BLOCKED", "blocking_http_status": self.blocked.get("http_status")}, b""
        record: dict[str, Any] = {"method": method, "url": url, "requested_at": utc()}
        body = b""
        try:
            self.conn.request(method, path, headers={"Accept-Encoding": "identity"})
            response = self.conn.getresponse()
            record["http_status"] = response.status
            value = response.getheader("Content-Length")
            record["content_length"] = int(value) if value and value.isascii() and value.isdecimal() else None
            if response.status in ACCESS_STOP:
                record["reason"] = "HOST_BLOCKED"
                self.blocked = record
            elif self.host == NOTICES and response.status == 202 and record["content_length"] == 0:
                record["reason"] = "EMPTY_HTTP_202"
                self.blocked = record
            if method == "HEAD":
                response.read()
            elif response.status == 200 and (record["content_length"] is None or record["content_length"] <= limit):
                body = response.read(limit + 1)
                if len(body) > limit:
                    record["reason"] = "RESPONSE_LIMIT_EXCEEDED"
                    self.conn.close()
                    body = b""
            else:
                self.conn.close()
                if response.status == 200:
                    record["reason"] = "RESPONSE_LIMIT_EXCEEDED"
        except (OSError, http.client.HTTPException) as error:
            self.conn.close()
            record["reason"] = type(error).__name__
            if isinstance(error, socket.gaierror):
                self.blocked = record
        record["observed_at"] = utc()
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
        time.sleep(0.08)
        return record, body


def archive(
    client: Client, symbol: str, date: str, kind: str, interval: str | None, m1: dict[str, str],
) -> dict[str, Any]:
    period = "daily" if len(date) == 10 else "monthly"
    url = archive_url(symbol, date, kind, interval)
    name = url.rsplit("/", 1)[-1]
    row: dict[str, Any] = {
        "symbol": symbol, "month": date[:7], "series": kind, "interval": interval, "url": url,
        "status": "UNVERIFIED", "size_bytes": None, "sha256": None, "observed_at": None,
    }
    if period == "daily":
        row["date"] = date
    elif symbol == "DGBUSDT" and date >= "2024-05":
        row.update(status="OUTSIDE_ACTIVE_PERIOD", reason="AFTER_DGBUSDT_DELISTING", evidence=DGB_NOTICE)
        return row
    record, _ = client.check("HEAD", url)
    http_status = record.get("http_status")
    row.update(observed_at=record.get("observed_at"), head_http_status=http_status)
    if http_status == 200:
        row.update(status="FOUND", size_bytes=record["content_length"])
        outcome, body = client.check("GET", url + ".CHECKSUM", 2048)
        checksum = re.fullmatch(rb"([0-9A-Fa-f]{64})[ \t]+\*?([!-~]+)[ \t]*", body.strip())
        row["checksum_http_status"] = outcome.get("http_status")
        if outcome.get("http_status") == 200 and checksum and checksum[2] == name.encode("ascii"):
            row["sha256"] = checksum[1].decode("ascii").lower()
            snapshot = checksum_path(ROOT, url)
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(body)
        else:
            row["checksum_issue"] = outcome.get("reason") or (
                "CHECKSUM_CONTENT_INVALID" if outcome.get("http_status") == 200 else "CHECKSUM_UNAVAILABLE"
            )
    elif http_status in (404, 410):
        row.update(status="NOT_FOUND", reason=f"HTTP_{http_status}")
    else:
        row["reason"] = record.get("reason", f"HTTP_{http_status}")
    if url in m1:
        row["m1_sha256"] = m1[url]
        row["m1_checksum_status"] = "UNVERIFIED" if row["sha256"] is None else (
            "MATCH" if row["sha256"] == m1[url] else "MISMATCH"
        )
    return row


def collect_archives() -> None:
    client = Client(ARCHIVE, "archive-requests.jsonl")
    pinned = json.loads(Path(M1_REF).read_text(encoding="utf-8"))
    m1 = {asset["source"]: asset["sha256"] for asset in pinned["assets"]}
    months = [f"{year}-{month:02}" for year in (2023, 2024) for month in range(1, 13)]
    # Visit exact pinned and unpinned monthly keys before any daily probe.
    monthly = [archive(client, symbol, month, kind, interval, m1)
               for symbol in SYMBOLS for month in months for kind, interval in SERIES]
    write("monthly.json", monthly)
    daily = []
    first_day = ("DGBUSDT", "2024-04")
    for row in monthly:
        symbol, month, kind, interval = (row[field] for field in ("symbol", "month", "series", "interval"))
        if (symbol, month) == first_day:
            # The contract was active for part of April 1, even if a monthly ZIP exists.
            daily.append(archive(client, symbol, month + "-01", kind, interval, m1))
        elif row["status"] == "NOT_FOUND":
            year, month_number = map(int, month.split("-"))
            for day in range(1, monthrange(year, month_number)[1] + 1):
                daily.append(archive(client, symbol, f"{month}-{day:02}", kind, interval, m1))
    write("daily.json", daily)
    write("archive-block.json", client.blocked)
    print("monthly", dict(Counter(row["status"] for row in monthly)), "daily",
          dict(Counter(row["status"] for row in daily)), "blocked", client.blocked, flush=True)


def collect_notices() -> None:
    candidates = tuple(item["url"] for item in discovery()["selected_candidates"])
    client = Client(NOTICES, "notice-requests.jsonl", candidates)
    results = []
    for url in candidates:
        outcome, body = client.check("GET", url, MAX_NOTICE_BYTES)
        row: dict[str, Any] = {
            "url": url, "http_status": outcome.get("http_status"), "retrieved_at": outcome.get("observed_at"),
        }
        if outcome.get("http_status") == 200 and body:
            sha = hashlib.sha256(body).hexdigest()
            path = ROOT / "notices" / f"{sha}.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            row.update(raw_path=path.as_posix(), sha256=sha, size_bytes=len(body))
        else:
            row["reason"] = outcome.get("reason", f"HTTP_{outcome.get('http_status')}")
        results.append(row)
    write("notice-http.json", results)
    write("notice-block.json", client.blocked)
    print("notices", len(results), "snapshots", sum("sha256" in row for row in results),
          "blocked", client.blocked, flush=True)


def main() -> None:
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__ or "Collect approved O2 metadata only")
    parser.add_argument("source", choices=("archives", "notices"))
    parser.add_argument("--work-dir", type=Path, required=True, help="New run directory under data/")
    args = parser.parse_args()
    ROOT = args.work_dir.resolve()
    if not ROOT.is_relative_to(Path("data").resolve()) or ROOT == Path("data").resolve():
        parser.error("--work-dir must be below data/")
    ROOT.mkdir(parents=True, exist_ok=True)
    (collect_archives if args.source == "archives" else collect_notices)()


if __name__ == "__main__":
    main()
