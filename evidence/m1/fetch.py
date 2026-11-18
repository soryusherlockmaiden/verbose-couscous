"""Reproduce the pinned Binance history sample through public GETs."""

import argparse
import hashlib
import time
from pathlib import Path

from acgs.history_io import read_json
from acgs.history_seal import ARCHIVE_HOST, registered_seal
from acgs.market_data import MAX_PAYLOAD, fetch_public


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("assets", type=Path)
    args = parser.parse_args()
    root = args.assets.resolve()
    manifest = read_json(args.manifest)
    if manifest.get("version") != "m1-history-v1":
        raise SystemExit("Expected an M1 history manifest")
    seal = registered_seal()
    assets = []
    try:
        seal.check_manifest(manifest)
        for entry in manifest["assets"]:
            url = entry["source"]
            target = (root / entry["path"]).resolve()
            if not target.is_relative_to(root) or not (
                entry["kind"] == "fundingMarks"
                or url.startswith(f"{ARCHIVE_HOST}/data/futures/um/monthly/")
            ):
                raise ValueError("Expected a pinned Binance USD-M public source")
            assets.append((entry, url, target))
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise SystemExit(str(error)) from error
    for entry, url, target in assets:
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
                raise SystemExit(f"Existing asset checksum mismatch: {target}")
            continue
        response = fetch_public(url)
        if response.status in {403, 451}:
            # Binance geo-restricts its REST API by location. Never route around it.
            raise SystemExit(
                f"Public source refused from this location (HTTP {response.status}): {url}. "
                "Fetch it from an eligible location and verify the pinned SHA-256."
            )
        if response.status != 200:
            raise SystemExit(f"Public source unavailable (HTTP {response.status}): {url}")
        if (
            len(response.payload) > MAX_PAYLOAD
            or hashlib.sha256(response.payload).hexdigest() != entry["sha256"]
        ):
            raise SystemExit(f"Asset differs from the pinned version: {url}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(response.payload)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
