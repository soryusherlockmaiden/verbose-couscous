"""Causal schedule estimates and strict settlement continuity checks."""

from dataclasses import dataclass
from typing import Protocol

from acgs.pit import Evidence, Funding, Instrument, Market, Provenance, Schedule
from acgs.sim_config import Policy


@dataclass(frozen=True)
class FundingView:
    interval_ms: int | None
    next_time: int | None
    evidence: Evidence
    next_evidence: Evidence | None = None
    publication: Schedule | None = None


class FundingInference(Protocol):
    @property
    def funding_intervals_ms(self) -> tuple[int, ...]: ...

    @property
    def funding_jitter_ms(self) -> int: ...


def ownership_boundary(time: int, interval: int, window: int) -> int | None:
    nearest = (time + interval // 2) // interval * interval
    return nearest if abs(time - nearest) <= window else None


def inferred_interval(previous: Funding, current: Funding, policy: FundingInference) -> int | None:
    gap = current.time - previous.time
    if current.interval_ms is not None:
        return current.interval_ms if abs(gap - current.interval_ms) <= policy.funding_jitter_ms else None
    return next(
        (interval for interval in policy.funding_intervals_ms
         if abs(gap - interval) <= policy.funding_jitter_ms),
        None,
    )


def funding_view(market: Market, instrument: Instrument, time: int, policy: Policy) -> FundingView:
    settlements = tuple(f for f in market.settlements.get(instrument, ()) if f.evidence.as_of <= time)
    observations = tuple(
        s for s in market.schedules.get(instrument, ())
        if s.effective <= time and s.evidence.as_of <= time and (s.until is None or time < s.until)
        and time - s.evidence.as_of < policy.metadata_age_ms
    )
    next_observed = next((s for s in reversed(observations) if s.next_time is not None), None)
    if next_observed is not None and next_observed.next_time is not None and next_observed.next_time < time:
        next_observed = None
    publication = next(
        (s for s in reversed(observations)
         if s.interval_withdrawn or (s.interval_ms is not None and s.evidence.provenance == Provenance.VERIFIED)),
        None,
    )
    interval_observed = publication if publication is not None and not publication.interval_withdrawn else None
    interval, predicted = None, None
    evidence = Evidence("funding", "missing", Provenance.UNAVAILABLE, time, "No current interval evidence")
    if interval_observed is not None and interval_observed.evidence.provenance == Provenance.VERIFIED:
        interval = interval_observed.interval_ms
        evidence = interval_observed.evidence
    elif len(settlements) >= 2:
        prev, last = settlements[-2:]
        interval = inferred_interval(prev, last, policy)
        # A longer spacing without an interval field may be a missing record.
        if (
            len(settlements) >= 3 and last.interval_ms is None
            and (prior := inferred_interval(settlements[-3], prev, policy)) is not None
            and interval is not None and interval > prior
        ):
            interval = None
        if interval is not None:
            evidence = Evidence(
                last.evidence.source, "settlement_spacing", Provenance.RECONSTRUCTED,
                max(prev.evidence.as_of, last.evidence.as_of),
                "Spacing is a causal estimate; it does not guarantee the next interval",
            )
    if interval is None:
        scenario = next(
            (s for s in reversed(observations) if s.interval_ms is not None
             and s.evidence.provenance == Provenance.RECONSTRUCTED), None,
        )
        if scenario is not None:
            interval, evidence = scenario.interval_ms, scenario.evidence
    if interval is not None and settlements:
        last = settlements[-1]
        predicted = last.time + interval
        if time - predicted > policy.funding_jitter_ms and next_observed is None:
            return FundingView(
                None, None,
                Evidence(last.evidence.source, "overdue_settlement", Provenance.UNAVAILABLE,
                         time, "Expected settlement has no available record"),
            )
    if next_observed is not None:
        return FundingView(interval, next_observed.next_time, evidence,
                           next_observed.evidence, interval_observed)
    inferred = Evidence(
        evidence.source, "project_next_settlement", Provenance.RECONSTRUCTED,
        evidence.as_of, "Forecast from the latest available settlement and interval",
    ) if predicted is not None else None
    return FundingView(interval, predicted, evidence, inferred, interval_observed)


def funding_complete(
    settlements: tuple[Funding, ...], start: int, end: int, policy: FundingInference
) -> bool:
    """Check continuity on a half-open window using the supplied observation prefix.

    Archive interval fields can corroborate a spacing change. With no such field,
    a longer interval than the prior spacing is indistinguishable from a missing payment.
    """
    before = tuple(f for f in settlements if f.time < start)
    window = tuple(f for f in settlements if start <= f.time < end)
    chain = before[-2:] + window
    if len(chain) < 2:
        return False
    if not before:
        first = chain[0]
        gap = inferred_interval(first, chain[1], policy)
        if gap is None or first.time - start >= gap + policy.funding_jitter_ms:
            return False
    prior: int | None = None
    for previous, current in zip(chain, chain[1:], strict=False):
        interval = inferred_interval(previous, current, policy)
        if current.time >= start and (
            interval is None or (prior is not None and interval > prior and current.interval_ms is None)
        ):
            return False
        prior = interval
    if prior is None:
        return False
    expected = chain[-1].time + prior
    if end <= expected:
        return True
    following = next((f for f in settlements if f.time >= end), None)
    # Only accounting can see a subsequent actual record. A decision passes a
    # prefix filtered by availability and must fail if that record is still absent.
    return following is not None and abs(following.time - expected) <= policy.funding_jitter_ms
