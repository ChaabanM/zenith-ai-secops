"""SQLAlchemy engine, session factory and request-scoped session dependency."""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models import Base


def _create_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    connect_args: dict = {}
    is_sqlite = url.get_backend_name() == "sqlite"
    if is_sqlite:
        # FastAPI may use a session across threads within one request.
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    new_engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)

    if is_sqlite:

        @event.listens_for(new_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record) -> None:  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")  # readers are not blocked by ingestion
            cursor.execute("PRAGMA synchronous=NORMAL")  # durable in WAL mode, much faster writes
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

    return new_engine


engine: Engine = _create_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session that is always closed after the request."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create tables and indexes for all registered models (idempotent; no migrations yet).

    ``create_all`` skips existing tables, so indexes added in a later phase are
    created explicitly for databases that already exist.
    """
    Base.metadata.create_all(bind=engine)
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(bind=engine, checkfirst=True)


def check_database(db: Session) -> bool:
    """Return True if a trivial query succeeds against the database."""
    try:
        return db.execute(text("SELECT 1")).scalar_one() == 1
    except Exception:
        return False
