import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema.sql"

# Order matters for wiping: referencing tables first (foreign keys are enforced).
TABLES = ["events", "waitlist_entries", "registrations", "sections", "programs", "participants", "households"]

_init_lock = threading.Lock()
_ready: set[str] = set()


def default_db_path() -> str:
    if os.environ.get("DB_PATH"):
        return os.environ["DB_PATH"]
    # Vercel functions can only write to /tmp, which is per-instance and ephemeral.
    if os.environ.get("VERCEL"):
        return "/tmp/app.db"
    return str(ROOT / "data" / "app.db")


def connect(path: str) -> sqlite3.Connection:
    # isolation_level=None: we issue BEGIN/COMMIT ourselves (see write_tx).
    conn = sqlite3.connect(path, isolation_level=None, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def write_tx(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE takes the write lock up front, so check-then-insert
    (e.g. count seats, then enroll) can't interleave with another writer."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def is_initialized(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='households'").fetchone()
    return row is not None


def ensure_db(path: str, seed_fn) -> None:
    """Create + seed the database on first use (e.g. a Vercel cold start)."""
    if path in _ready and os.path.exists(path):
        return
    with _init_lock:
        conn = connect(path)
        try:
            if not is_initialized(conn):
                conn.executescript(SCHEMA_PATH.read_text())
                with write_tx(conn):
                    seed_fn(conn)
        finally:
            conn.close()
        _ready.add(path)


def reset_db(conn: sqlite3.Connection, seed_fn) -> None:
    """Wipe and reseed in one transaction. Rowids restart at 1, so ids stay stable."""
    with write_tx(conn):
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")
        seed_fn(conn)
