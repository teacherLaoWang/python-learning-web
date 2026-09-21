"""数据库装配：engine / session / FastAPI 依赖（对应教程第 7 章）。

SQLAlchemy 2.0 风格：DeclarativeBase + Mapped[] + mapped_column。
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_settings.data_dir.mkdir(parents=True, exist_ok=True)

engine: Engine = create_engine(
    _settings.db_url,
    echo=False,
    future=True,
    # SQLite 默认不允许多线程共用连接；FastAPI 会在线程池里跑同步依赖，
    # 所以这里显式关掉检查（我们每个请求一个 session，不跨线程共用）。
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _record) -> None:  # pragma: no cover - 钩子
    """开 WAL + 外键，让 delete-orphan 级联真正生效。"""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """建表。启动时调用，幂等。"""

    Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    """标准写法：yield 出 session，请求结束后自动 close。

    用法：def endpoint(db: Annotated[Session, Depends(get_session)])
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
