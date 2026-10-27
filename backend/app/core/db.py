"""SQLAlchemy engine/session plumbing. SQLite by default; any SQLAlchemy URL works."""
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None


def init_engine(url: str | None = None):
    """(Re)initialise the engine. Tests call this with an in-memory/temp URL."""
    global _engine, _Session
    url = url or get_settings().db_url
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _fk(dbapi_conn, _):  # enforce FK cascades
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    _Session = sessionmaker(_engine, expire_on_commit=False)
    from app.models import tables  # noqa: F401  (register models)
    Base.metadata.create_all(_engine)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    if _Session is None:
        init_engine()
    s = _Session()
    try:
        yield s
        s.commit()
        for path in s.info.pop("unlink_after_commit", []):  # files are removed only once the DB delete is durable
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    with session_scope() as s:
        yield s


def unlink_after_commit(db: Session, path) -> None:
    """Schedule a file removal for after the surrounding transaction commits (dropped on rollback)."""
    db.info.setdefault("unlink_after_commit", []).append(path)
