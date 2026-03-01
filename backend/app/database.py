# backend/app/database.py
import asyncio
import logging
import random
from functools import wraps
from pathlib import Path

from sqlalchemy.exc import OperationalError
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
# Added timeout and WAL mode for better concurrency
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    poolclass=NullPool,
    connect_args={
        "check_same_thread": False,
        "timeout": 30.0,  # 30 second timeout for locks
    },
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
            # Enable WAL mode for better concurrency
            from sqlalchemy import text
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA busy_timeout=30000"))  # 30 seconds
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


def retry_on_db_lock(max_retries: int = 5, base_delay: float = 0.1):
    """
    Decorator to retry database operations when database is locked.
    Uses exponential backoff with jitter.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except OperationalError as e:
                    last_error = e
                    if "database is locked" in str(e).lower():
                        if attempt < max_retries - 1:
                            # Exponential backoff with jitter
                            delay = base_delay * (2 ** attempt) * (0.5 + random.random() * 0.5)
                            logger.warning(
                                "Database locked, retrying %s in %.2fs (attempt %d/%d)",
                                func.__name__, delay, attempt + 1, max_retries
                            )
                            await asyncio.sleep(delay)
                        else:
                            logger.error(
                                "Database locked after %d retries in %s",
                                max_retries, func.__name__
                            )
                            raise
                    else:
                        # Not a lock error, re-raise immediately
                        raise
            raise last_error
        return wrapper
    return decorator