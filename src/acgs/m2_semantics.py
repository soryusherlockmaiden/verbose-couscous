"""The accepted replay and funding rules pinned by M2."""

from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from acgs.broker_sim import REPLAY_VERSION
from acgs.pit import HOUR
from acgs.sim_config import Policy, digest

VERSION = "m1-semantics-v1"


@dataclass(frozen=True)
class M1Semantics:
    funding_intervals_ms: tuple[int, ...]
    funding_jitter_ms: int
    funding_ownership_window_ms: int
    decimal_precision: int
    accounting_tolerance: Decimal

    @classmethod
    def from_policy(cls, policy: Policy) -> "M1Semantics":
        return cls(
            policy.funding_intervals_ms, policy.funding_jitter_ms,
            policy.funding_ownership_window_ms, policy.decimal_precision,
            policy.accounting_tolerance,
        )

    @property
    def digest(self) -> str:
        return digest({
            "version": VERSION,
            # The replay contract identifies event order, funding ownership and snapshot availability.
            "replay": REPLAY_VERSION,
            "funding_inference": "m1-v3",
            "decimal_rounding": ROUND_HALF_EVEN,
            **asdict(self),
        })


ACCEPTED_M1_SEMANTICS = M1Semantics(
    funding_intervals_ms=(HOUR, 2 * HOUR, 4 * HOUR, 8 * HOUR),
    funding_jitter_ms=5_000,
    funding_ownership_window_ms=15_000,
    decimal_precision=40,
    accounting_tolerance=Decimal("0.00000001"),
)
