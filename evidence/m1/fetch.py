"""Reproduce the pinned Binance history sample through public GETs."""

import argparse
import hashlib
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from acgs.history_io import read_json
from acgs.market_data import MAX_PAYLOAD, fetch_public


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("assets", type=Path)
    args = parser.parse_args()
    root = args.assets.resolve()
    for entry in read_json(args.manifest)["assets"]:
        url = urlsplit(entry["source"])
        target = (root / entry["path"]).resolve()
        archive = (
            url.netloc == "data.binance.vision" and url.path.startswith("/data/futures/um/monthly/")
            and url.path.endswith(".zip") and not url.query
        )
        query = parse_qs(url.query, strict_parsing=True)
        settlement_marks = (
            entry["kind"] == "fundingMarks" and url.netloc == "fapi.binance.com"
            and url.path == "/fapi/v1/fundingRate"
            and query.keys() == {"symbol", "startTime", "endTime", "limit"}
            and query["symbol"] == [entry["id"].split("@", 1)[0]]
            and all(len(query[key]) == 1 and query[key][0].isdigit()
                    for key in ("startTime", "endTime", "limit"))
        )
        if (
            not target.is_relative_to(root) or url.scheme != "https" or url.fragment
            or not (archive or settlement_marks)
        ):
            raise SystemExit("Expected a pinned Binance USD-M public source")
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
                raise SystemExit(f"Existing asset checksum mismatch: {target}")
            continue
        response = fetch_public(url.geturl())
        if response.status in {403, 451}:
            # Binance geo-restricts its REST API by location. Never route around it.
            raise SystemExit(
                f"Public source refused from this location (HTTP {response.status}): {url.geturl()}. "
                "Fetch it from an eligible location and verify the pinned SHA-256."
            )
        if response.status != 200:
            raise SystemExit(f"Public source unavailable (HTTP {response.status}): {url.geturl()}")
        if (
            len(response.payload) > MAX_PAYLOAD
            or hashlib.sha256(response.payload).hexdigest() != entry["sha256"]
        ):
            raise SystemExit(f"Asset differs from the pinned version: {url.geturl()}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(response.payload)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
