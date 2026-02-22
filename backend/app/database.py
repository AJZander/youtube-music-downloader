# backend/app/database.py
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models import Base

logger = logging.getLogger(__name__)

# Derive filesystem path from the connection string so we can mkdir it
_db_path_str = settings.database_url.replace("sqlite+aiosqlite://", "").lstrip("/")
_db_file = Path("/" + _db_path_str)
_db_file.parent.mkdir(parents=True, exist_ok=True)

# NullPool — each request gets its own connection, safe for single-process async
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    poolclass=NullPool,
    connect_args={"check_same_thread": False},
)

# Reusable session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Create all tables on startup. Safe to call multiple times."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database ready at %s", _db_file)
    except Exception:
        logger.exception("Failed to initialise database")
        raise


async def get_session():
    """FastAPI dependency that yields an async session and closes it cleanly."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()