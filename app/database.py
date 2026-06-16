from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db_base import Base

_engine = None
_async_session_local = None


def _ensure_engine():
    global _engine, _async_session_local
    if _engine is not None:
        return

    from app.config import get_settings

    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Set DATABASE_URL (postgresql+asyncpg://...) "
            "or standard PG* / POSTGRES_* variables."
        )

    _engine = create_async_engine(
        settings.database_url,
        echo=settings.environment == "development",
        pool_pre_ping=True,
    )
    _async_session_local = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def get_engine():
    _ensure_engine()
    return _engine


class _EngineProxy:
    def __getattr__(self, name):
        return getattr(get_engine(), name)


class _SessionMakerProxy:
    def __call__(self, *args, **kwargs):
        _ensure_engine()
        return _async_session_local(*args, **kwargs)

    def __getattr__(self, name):
        _ensure_engine()
        return getattr(_async_session_local, name)


engine = _EngineProxy()
AsyncSessionLocal = _SessionMakerProxy()


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
