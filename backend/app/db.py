"""SQLite access layer. One shared connection, writes serialized by a lock —
throughput here is a handful of rows per track, contention is not a concern."""
import asyncio
import shutil
from datetime import datetime, timezone

import aiosqlite

from .config import settings

MIGRATIONS: list[str] = [
    # v1 — initial schema
    """
    CREATE TABLE jobs (
      id TEXT PRIMARY KEY,
      source_url TEXT NOT NULL,
      provider TEXT NOT NULL,
      source_type TEXT NOT NULL,
      title TEXT, artist TEXT,
      force INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL,
      error_code TEXT, error_message TEXT,
      total_tracks INTEGER NOT NULL DEFAULT 0,
      completed_tracks INTEGER NOT NULL DEFAULT 0,
      failed_tracks INTEGER NOT NULL DEFAULT 0,
      skipped_tracks INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      started_at TEXT, finished_at TEXT
    );
    CREATE TABLE tracks (
      id TEXT PRIMARY KEY,
      job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
      position INTEGER,
      provider TEXT,
      provider_track_id TEXT,
      source_url TEXT,
      artist TEXT, album TEXT, album_artist TEXT, title TEXT,
      track_number INTEGER, disc_number INTEGER, release_date TEXT,
      isrc TEXT, cover_url TEXT,
      expected_duration_sec REAL,
      status TEXT NOT NULL,
      stage TEXT,
      attempts INTEGER NOT NULL DEFAULT 0,
      progress_pct REAL,
      error_code TEXT, error_message TEXT,
      match_candidates TEXT,
      staging_path TEXT, library_path TEXT,
      format TEXT, bitrate_kbps INTEGER, sample_rate INTEGER,
      below_target INTEGER NOT NULL DEFAULT 0,
      duration_sec REAL, filesize_bytes INTEGER, checksum_blake2b TEXT,
      ytdlp_version TEXT,
      plex_verified INTEGER NOT NULL DEFAULT 0, plex_rating_key TEXT,
      created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
    );
    CREATE INDEX idx_tracks_job ON tracks(job_id);
    CREATE INDEX idx_tracks_status ON tracks(status);
    CREATE TABLE library_index (
      id INTEGER PRIMARY KEY,
      norm_artist TEXT NOT NULL, norm_title TEXT NOT NULL, norm_album TEXT,
      path TEXT NOT NULL UNIQUE,
      format TEXT, duration_sec REAL,
      provider_track_id TEXT,
      added_at TEXT NOT NULL
    );
    CREATE INDEX idx_li_artist_title ON library_index(norm_artist, norm_title);
    CREATE INDEX idx_li_provider ON library_index(provider_track_id);
    CREATE TABLE legacy_archive (
      provider TEXT NOT NULL, provider_id TEXT NOT NULL,
      PRIMARY KEY (provider, provider_id)
    );
    CREATE TABLE events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL, job_id TEXT, track_id TEXT,
      level TEXT NOT NULL, code TEXT, message TEXT
    );
    """,
]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self) -> None:
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(settings.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._migrate()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _migrate(self) -> None:
        await self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        cur = await self._conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
        current = (await cur.fetchone())[0]
        for version, sql in enumerate(MIGRATIONS, start=1):
            if version > current:
                await self._conn.executescript(sql)
                await self._conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, utcnow()),
                )
        await self._conn.commit()

    async def execute(self, sql: str, params: tuple = ()) -> None:
        async with self._lock:
            await self._conn.execute(sql, params)
            await self._conn.commit()

    async def executemany(self, sql: str, rows: list[tuple]) -> None:
        async with self._lock:
            await self._conn.executemany(sql, rows)
            await self._conn.commit()

    async def fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self._lock:
            cur = await self._conn.execute(sql, params)
            return await cur.fetchall()

    async def backup(self) -> str:
        """SQLite online backup into data/backups, keep newest 14."""
        settings.backups_dir.mkdir(parents=True, exist_ok=True)
        name = f"mdl-{datetime.now().strftime('%Y%m%d')}.db"
        dest = settings.backups_dir / name
        async with self._lock:
            target = await aiosqlite.connect(dest)
            try:
                await self._conn.backup(target)
            finally:
                await target.close()
        backups = sorted(settings.backups_dir.glob("mdl-*.db"))
        for old in backups[:-14]:
            old.unlink(missing_ok=True)
        return str(dest)


db = Database()
