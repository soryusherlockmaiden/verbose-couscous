"""Reproduce the pinned Binance archive sample through public GETs."""

import argparse
import hashlib
import time
from pathlib import Path
from urllib.parse import urlsplit

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
        if not (
            target.is_relative_to(root) and url.scheme == "https" and url.netloc == "data.binance.vision"
            and url.path.startswith("/data/futures/um/monthly/") and url.path.endswith(".zip")
            and not url.query and not url.fragment
        ):
            raise SystemExit("Expected a pinned Binance USD-M public archive")
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
                raise SystemExit(f"Existing asset checksum mismatch: {target}")
            continue
        response = fetch_public(url.geturl())
        if response.status != 200:
            raise SystemExit(f"Public archive unavailable (HTTP {response.status}): {url.geturl()}")
        if (
            len(response.payload) > MAX_PAYLOAD
            or hashlib.sha256(response.payload).hexdigest() != entry["sha256"]
        ):
            raise SystemExit(f"Archive differs from the pinned version: {url.geturl()}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(response.payload)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
