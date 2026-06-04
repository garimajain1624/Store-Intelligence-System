from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Generator, Optional

from sqlalchemy import Engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine


def _utc_now_iso() -> str:
    # Stored/returned in UTC ISO-8601; we keep it simple here (no DB write yet).
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class DBUnavailableError(Exception):
    """Raised when SQLite/engine is not usable."""

    message: str = "database unavailable"
    detail: Optional[str] = None
    trace_id: Optional[str] = None
    detected_at: str = _utc_now_iso()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": self.message,
            "detail": self.detail,
            "trace_id": self.trace_id,
            "detected_at": self.detected_at,
        }


def _default_sqlite_url() -> str:
    # Persist under /data inside Docker (mounted volume in compose),
    # else keep local file `./data/app.db`.
    path = os.getenv("SQLITE_PATH")
    if path:
        return f"sqlite+pysqlite:///{path}"
    return "sqlite+pysqlite:///./data/app.db"


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", _default_sqlite_url())


def _apply_sqlite_pragmas(dbapi_connection: Any) -> None:
    # These pragmas are per-connection. WAL improves concurrency for reads.
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA busy_timeout=5000;")
    finally:
        cursor.close()


_ENGINE: Optional[Engine] = None
_CURRENT_URL: Optional[str] = None
_SessionLocal: Optional[sessionmaker[Session]] = None


def get_engine() -> Engine:
    global _ENGINE, _SessionLocal, _CURRENT_URL
    url = get_database_url()
    if _ENGINE is not None and _CURRENT_URL == url:
        return _ENGINE

    connect_args: Dict[str, Any] = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}

    engine = create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _connection_record: Any) -> None:  # noqa: ANN401
        if url.startswith("sqlite"):
            _apply_sqlite_pragmas(dbapi_connection)

    _ENGINE = engine
    _CURRENT_URL = url
    _SessionLocal = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False, future=True)
    return _ENGINE


def get_sessionmaker() -> sessionmaker[Session]:
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db(trace_id: Optional[str] = None) -> Generator[Session, None, None]:
    """FastAPI dependency that yields a SQLAlchemy Session."""
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
    except OperationalError as e:
        raise DBUnavailableError(detail=str(e), trace_id=trace_id) from e
    finally:
        db.close()


def ping_db(conn: Connection) -> None:
    conn.execute(text("SELECT 1"))


def ensure_db_available(trace_id: Optional[str] = None) -> None:
    """Raises DBUnavailableError if engine/DB cannot be reached."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            ping_db(conn)
    except OperationalError as e:
        raise DBUnavailableError(detail=str(e), trace_id=trace_id) from e
    except SQLAlchemyError as e:
        raise DBUnavailableError(detail=str(e), trace_id=trace_id) from e


def new_trace_id() -> str:
    return str(uuid.uuid4())

