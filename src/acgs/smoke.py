import sqlite3

from acgs.clock import Clock
from acgs.config import Config
from acgs.ledger import record_smoke


def run_smoke(connection: sqlite3.Connection, config: Config, clock: Clock) -> bool:
    return record_smoke(connection, config, clock.now())
