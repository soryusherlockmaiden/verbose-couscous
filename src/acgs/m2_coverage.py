"""Deterministic coverage observations, through the same seal as research loads."""

from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from acgs.funding import funding_complete, ownership_boundary
from acgs.m2_data import (
    INTERVALS,
    SIGNAL_INTERVALS,
    AccessRefused,
    Window,
    _approve,
    _load,
)
from acgs.m2_semantics import ACCEPTED_M1_SEMANTICS, M1Semantics
from acgs.m2_semantics import VERSION as SEMANTICS_VERSION
from acgs.pit import Evidence, Market
from acgs.sim_config import Policy

COVERAGE_SCHEMA = "m2-coverage-v2"


def _evidence(observation: Evidence) -> dict[str, Any]:
    return asdict(observation)


def _bar_coverage(
    rows: list[tuple[int, int, int, Evidence]], window: Window, size: int,
) -> dict[str, Any]:
    spans: list[list[int]] = []
    missing = []
    cursor = window.start
    visible = []
    for start, end, available, observation in rows:
        if start < window.start or end > window.end:
            continue
        if cursor < start:
            missing.append([cursor, start])
        if spans and spans[-1][1] == start:
            spans[-1][1] = end
        else:
            spans.append([start, end])
        cursor = end
        visible.append((available, observation))
    if cursor < window.end:
        missing.append([cursor, window.end])
    return {
        "observation_count": len(visible),
        "longest_contiguous_bars": max(((end - start) // size for start, end in spans), default=0),
        "effective_spans": spans,
        "missing_ranges": missing,
        "missing_provenance": "UNAVAILABLE" if missing else None,
        "by_source": _time_summary(visible),
    }


def _time_summary(rows: list[tuple[int, Evidence]]) -> dict[str, Any]:
    by_source: dict[str, list[tuple[int, Evidence]]] = defaultdict(list)
    for available, observation in rows:
        by_source[observation.source].append((available, observation))
    return {
        source: {
            "observations": len(group),
            "method": group[0][1].method,
            "provenance": group[0][1].provenance,
            "as_of_range": [min(x.as_of for _, x in group), max(x.as_of for _, x in group)],
            "available_range": [min(t for t, _ in group), max(t for t, _ in group)],
        }
        for source, group in sorted(by_source.items())
    }


def _epochs(
    market: Market, dependencies: dict[str, Window], request: Window, semantics: M1Semantics,
) -> list[dict[str, Any]]:
    result = []
    for inst, listings in sorted(market.listings.items()):
        groups: dict[tuple[bool, int], list[tuple[int, int, int, Evidence]]] = defaultdict(list)
        for row in market.bars.get(inst, ()):
            groups[row.is_mark, row.end - row.start].append(
                (row.start, row.end, row.available, row.evidence),
            )
        streams = {}
        for name, size in INTERVALS.items():
            for mark, kind in ((False, "klines"), (True, "markPriceKlines")):
                key = f"{kind}:{name}"
                streams[key] = _bar_coverage(groups[mark, size], dependencies[key], size)
        settlements = market.settlements.get(inst, ())
        funding_window = dependencies["fundingRate"]
        funding = tuple(row for row in settlements if funding_window.start <= row.time < funding_window.end)
        marks_required = {}
        for name in SIGNAL_INTERVALS:
            size = INTERVALS[name]
            marks_required[name] = [
                {"settled_at": row.time, "affected_bar": [start, start + size]}
                for row in funding
                if row.mark is None
                and ownership_boundary(row.time, size, semantics.funding_ownership_window_ms) is None
                and (start := row.time // size * size) < request.end
                and request.start < start + size
            ]
        metadata = {
            "listings": [
                {"effective": row.effective, "status": row.status, "evidence": _evidence(row.evidence)}
                for row in listings
            ],
            "filters": [
                {"effective": row.effective, "evidence": _evidence(row.evidence)}
                for row in market.filters.get(inst, ())
            ],
            "maintenance": [
                {"effective": row.effective, "evidence": _evidence(row.evidence),
                 "highest_observed_notional": row.tiers[-1].ceiling}
                for row in market.maintenance.get(inst, ())
            ],
            "spreads": [
                {"effective": row.evidence.as_of, "evidence": _evidence(row.evidence)}
                for row in market.spreads.get(inst, ())
            ],
            "schedules": [
                {"effective": row.effective, "until": row.until, "interval_ms": row.interval_ms,
                 "next_time": row.next_time, "cap": row.cap, "floor": row.floor,
                 "evidence": _evidence(row.evidence)}
                for row in market.schedules.get(inst, ())
            ],
        }
        result.append({
            "epoch": inst.id,
            "onboard": inst.onboard,
            "metadata": metadata,
            "metadata_without_observations": [
                kind for kind, observations in metadata.items() if not observations
            ],
            "candle_streams": streams,
            "funding": {
                "settlement_times": [row.time for row in funding],
                "settlement_marks": [
                    {"effective": row.time, "evidence": _evidence(row.mark_evidence)}
                    for row in funding if row.mark_evidence is not None
                ],
                "by_source": _time_summary([(row.evidence.as_of, row.evidence) for row in funding]),
                "settlement_continuity_on_decision_window": funding_complete(
                    funding, request.start, request.end, semantics,
                ),
                "archive_interval_fields_ms": sorted({
                    row.interval_ms for row in funding if row.interval_ms is not None
                }),
                "interval_inferred_from_spacing": "RECONSTRUCTED",
                "missing_intrabar_settlement_marks": marks_required,
            },
        })
    return result


def coverage(
    config: Path, manifest: Path, root: Path, purpose: str, request: Window,
    policy: Policy | None = None,
) -> dict[str, Any]:
    approved = _approve(config, manifest, purpose, request)
    semantics = ACCEPTED_M1_SEMANTICS if policy is None else M1Semantics.from_policy(policy)
    if semantics.digest != ACCEPTED_M1_SEMANTICS.digest:
        raise AccessRefused("Coverage requires the pinned M1 semantics")
    loaded = _load(root, approved)
    return {
        "schema": COVERAGE_SCHEMA,
        "config_sha256": approved.config_digest,
        "manifest_sha256": loaded.digest,
        "m1_semantics": {"version": SEMANTICS_VERSION, "sha256": semantics.digest},
        "purpose": approved.purpose,
        "dependencies": {name: asdict(window) for name, window in approved.dependencies.items()},
        "asset_count": len(loaded.manifest["assets"]),
        "total_bytes": sum(row["size_bytes"] for row in loaded.manifest["assets"]),
        "ingested_at": loaded.manifest["ingested_at"],
        "modeled_publication_delays_ms": {
            "candle": loaded.manifest["candle_delay_ms"],
            "funding": loaded.manifest["funding_delay_ms"],
        },
        "assets": [
            {
                "epoch": row["id"],
                "kind": row["kind"],
                "source": row["source"],
                "declared_effective_window": asdict(Window.parse(row)),
                "size_bytes": row["size_bytes"],
                "sha256": row["sha256"],
            }
            for row in loaded.manifest["assets"]
        ],
        "epochs": _epochs(loaded.market, approved.dependencies, approved.request, semantics),
    }
