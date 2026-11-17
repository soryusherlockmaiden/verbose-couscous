"""Causal daily liquidity and signed-momentum research candidates."""

from dataclasses import dataclass
from decimal import Decimal, localcontext

from acgs.clock import Clock
from acgs.funding import funding_complete, funding_view
from acgs.pit import DAY, ZERO, Evidence, Instrument, Market, Provenance, as_of
from acgs.sim_config import Policy, instant_ms


@dataclass(frozen=True)
class Rank:
    instrument: Instrument
    reason: str
    quote_volume: Decimal | None = None
    momentum: Decimal | None = None
    liquidity_rank: int | None = None
    evidence: tuple[Evidence, ...] = ()
    history_end: int | None = None


@dataclass(frozen=True)
class UniverseSnapshot:
    time: int
    policy_digest: str
    ranks: tuple[Rank, ...]


def snapshot(market: Market, clock: Clock, policy: Policy) -> UniverseSnapshot:
    time = instant_ms(clock.now())
    good: list[Rank] = []
    rejected: list[Rank] = []
    with localcontext(policy.context()):
        for instrument, history in sorted(market.listings.items()):
            listing = as_of(history, time)
            if listing is None:
                continue
            if not listing.tradable:
                rejected.append(Rank(instrument, "NOT_TRADING", evidence=(listing.evidence,)))
                continue
            bars = market.history(instrument, time, DAY, policy.history_days)
            if not bars:
                rejected.append(Rank(instrument, "INSUFFICIENT_HISTORY", evidence=(listing.evidence,)))
                continue
            view = funding_view(market, instrument, time, policy)
            settlements = tuple(f for f in market.settlements.get(instrument, ())
                                if f.evidence.as_of <= time)
            evidence = (listing.evidence, *dict.fromkeys(b.evidence for b in bars), view.evidence)
            if view.interval_ms is None or not funding_complete(settlements, bars[0].start, time, policy):
                rejected.append(Rank(instrument, "FUNDING_UNAVAILABLE", evidence=evidence))
                continue
            if not policy.allow_reconstructed and any(
                e.provenance != Provenance.VERIFIED for e in evidence
            ):
                rejected.append(Rank(instrument, "UNVERIFIED_CRITICAL_FILTER", evidence=evidence))
                continue
            volume = sum((b.quote_volume for b in bars[-policy.volume_days:]), ZERO) / policy.volume_days
            momentum = bars[-1].prices.close / bars[-1 - policy.momentum_days].prices.close - 1
            good.append(Rank(instrument, "CANDIDATE", volume, momentum,
                             evidence=evidence, history_end=bars[-1].end))
        good.sort(key=lambda x: (-(x.quote_volume or ZERO), x.instrument))
        ranked = tuple(
            Rank(
                row.instrument,
                "OUTSIDE_LIQUIDITY_RANK" if index > policy.top_liquid
                else "NO_SIGNED_MOMENTUM" if not row.momentum else "CANDIDATE",
                row.quote_volume, row.momentum, index, row.evidence, row.history_end,
            )
            for index, row in enumerate(good, 1)
        )
        return UniverseSnapshot(time, policy.digest, ranked + tuple(rejected))
