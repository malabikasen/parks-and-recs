from datetime import datetime, timezone

import pytest

from app.db import SCHEMA_PATH, connect, write_tx
from app.seed import seed

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)

# Seed ids (see app/seed.py)
PATEL, KIM, GARCIA, NGUYEN, OKAFOR = 1, 2, 3, 4, 5
AVA, BEN, MAYA, LEO, JUNE, SOFIA, MATEO, LILY, NOAH, ZARA = range(1, 11)
SWIM1, SWIM2_TUE, SWIM2_THU, SWIM3, BASKETBALL, WATERCOLOR, CHAIR_YOGA = range(1, 8)


def make_db(path: str = ":memory:"):
    conn = connect(path)
    conn.executescript(SCHEMA_PATH.read_text())
    with write_tx(conn):
        seed(conn, NOW)
    return conn


@pytest.fixture
def conn():
    c = make_db()
    yield c
    c.close()
