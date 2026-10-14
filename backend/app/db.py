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
    # v2 — per-track force. The review screen's "Force" used to flip the whole
    # job's force flag, which bypassed dedup and overwrote library files for
    # every sibling track.
    """
    ALTER TABLE tracks ADD COLUMN force INTEGER NOT NULL DEFAULT 0;
    """,
    # v3 — "I've looked at this, it's fine" for the skipped list. Kept separate
    # from status so the skip still counts as a skip in job accounting.
    """
    ALTER TABLE tracks ADD COLUMN review_acked INTEGER NOT NULL DEFAULT 0;
    """,
    # v4 — library management. library_index was a dedup lookup table (is this
    # artist+title present?); the library manager needs to compare copies, so
    # it carries the raw tags, the audio properties that decide which copy is
    # better, and the stat() pair that makes rescans incremental. Same table,
    # not a second one: two tables scanning the same 60k files over CIFS would
    # drift, and find_duplicate keeps working untouched.
    """
    ALTER TABLE library_index ADD COLUMN artist TEXT;
    ALTER TABLE library_index ADD COLUMN title TEXT;
    ALTER TABLE library_index ADD COLUMN album TEXT;
    ALTER TABLE library_index ADD COLUMN album_artist TEXT;
    ALTER TABLE library_index ADD COLUMN track_number INTEGER;
    ALTER TABLE library_index ADD COLUMN disc_number INTEGER;
    ALTER TABLE library_index ADD COLUMN year TEXT;
    ALTER TABLE library_index ADD COLUMN bitrate_kbps INTEGER;
    ALTER TABLE library_index ADD COLUMN sample_rate INTEGER;
    ALTER TABLE library_index ADD COLUMN bit_depth INTEGER;
    ALTER TABLE library_index ADD COLUMN channels INTEGER;
    ALTER TABLE library_index ADD COLUMN filesize_bytes INTEGER;
    ALTER TABLE library_index ADD COLUMN mtime REAL;
    ALTER TABLE library_index ADD COLUMN dirpath TEXT;
    ALTER TABLE library_index ADD COLUMN artist_dir TEXT;
    ALTER TABLE library_index ADD COLUMN scanned_at TEXT;
    CREATE INDEX idx_li_dirpath ON library_index(dirpath);
    CREATE INDEX idx_li_artist_dir ON library_index(artist_dir);

    -- Removal from the library is always a move into .mdl-trash, recorded
    -- here so it can be undone. Nothing in this app unlinks a library file.
    CREATE TABLE library_trash (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      batch_id TEXT NOT NULL,
      original_path TEXT NOT NULL,
      trash_path TEXT NOT NULL,
      kind TEXT NOT NULL,
      reason TEXT,
      filesize_bytes INTEGER,
      trashed_at TEXT NOT NULL,
      restored_at TEXT
    );
    CREATE INDEX idx_trash_batch ON library_trash(batch_id);
    CREATE INDEX idx_trash_active ON library_trash(restored_at);
    """,
    # v5 — an explicit queue order. Jobs used to run strictly in creation
    # order, so the only way to prioritise something was to wait. Existing
    # jobs are backfilled in creation order, which is exactly the order they
    # already ran in.
    """
    ALTER TABLE jobs ADD COLUMN queue_position INTEGER;
    CREATE INDEX idx_jobs_queue ON jobs(queue_position, created_at);
    UPDATE jobs SET queue_position = (
      SELECT rn FROM (
        SELECT id, ROW_NUMBER() OVER (ORDER BY created_at, id) AS rn FROM jobs
      ) AS ordered WHERE ordered.id = jobs.id
    );
    """,
    # v6 — decisions the user has already made, and files we cannot read.
    #
    # Duplicate detection is a heuristic, and a library full of compilations
    # produces real false positives: "Mothership Disc 1" shares most of its
    # tracks with "An Introduction to Led Zeppelin" without being the same
    # album. Saying so once has to stick, so judgements are stored as pairs
    # that may coexist. Pairs, not groups, because the next scan may find a
    # different set of copies and a decision about A and B still holds.
    """
    CREATE TABLE library_exclusions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      scope TEXT NOT NULL,
      key_a TEXT NOT NULL,
      key_b TEXT NOT NULL,
      reason TEXT,
      created_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX idx_excl_pair ON library_exclusions(scope, key_a, key_b);

    -- A file mutagen cannot open has no row to mark scanned, so every scan
    -- retried it forever and the UI kept reporting it as "not read yet".
    ALTER TABLE library_index ADD COLUMN unreadable INTEGER NOT NULL DEFAULT 0;
    """,
    # v7 — the artist catalogue. YouTube Music metadata is cached so that
    # reopening an artist, or queueing an album the browser already showed,
    # costs no requests. fetched_at is epoch seconds: it is only ever compared
    # against a TTL, never shown.
    #
    # track_filter lets a job download part of an album — the video ids the
    # user ticked in the browser, as a JSON list. NULL means the whole album.
    """
    CREATE TABLE catalog_cache (
      key TEXT PRIMARY KEY,
      data TEXT NOT NULL,
      fetched_at REAL NOT NULL
    );
    ALTER TABLE jobs ADD COLUMN track_filter TEXT;
    """,
    # v8 — Discover. Each library artist folder is matched to a YouTube Music
    # artist once (discover_names); artists are re-checked on a schedule
    # (discover_artists), and every release they list is remembered with the
    # time it was first seen (discover_releases) — which is how "new since
    # last time" is known, since YouTube Music only gives a release's year.
    # Times are epoch seconds.
    """
    CREATE TABLE discover_names (
      name_key TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      weight INTEGER NOT NULL DEFAULT 0,
      channel_id TEXT,
      resolved_at REAL NOT NULL DEFAULT 0
    );
    CREATE TABLE discover_artists (
      channel_id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      thumbnail TEXT,
      source TEXT NOT NULL,
      weight REAL NOT NULL DEFAULT 0,
      related TEXT,
      first_checked REAL,
      checked_at REAL,
      next_check REAL NOT NULL DEFAULT 0,
      dismissed INTEGER NOT NULL DEFAULT 0,
      error TEXT
    );
    CREATE INDEX idx_discover_artists_due ON discover_artists(next_check);
    CREATE TABLE discover_releases (
      browse_id TEXT PRIMARY KEY,
      channel_id TEXT NOT NULL,
      artist TEXT,
      title TEXT NOT NULL,
      type TEXT,
      year INTEGER,
      thumbnail TEXT,
      explicit INTEGER NOT NULL DEFAULT 0,
      first_seen REAL NOT NULL,
      dismissed INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_discover_releases_channel ON discover_releases(channel_id);
    CREATE INDEX idx_discover_releases_year ON discover_releases(year);
    """,
    # v9 — the metadata manager. library_index learns the tags that say
    # *which* release a file is (MusicBrainz ids, ISRC) and what it is
    # described as (genre, label, release type), plus the junk it carries.
    # tags_version marks rows read by this reader: older rows are re-read by
    # the next incremental scan, so no separate migration pass is needed.
    #
    # plex_items mirrors the Plex music section, joined to our files by path.
    # md_* hold the desired state (md_albums / md_artists), what disagrees
    # with it (md_issues), and every change made to get there (md_changes),
    # each with the old value so it can be undone.
    """
    ALTER TABLE library_index ADD COLUMN genre TEXT;
    ALTER TABLE library_index ADD COLUMN isrc TEXT;
    ALTER TABLE library_index ADD COLUMN mb_albumid TEXT;
    ALTER TABLE library_index ADD COLUMN mb_releasegroupid TEXT;
    ALTER TABLE library_index ADD COLUMN mb_albumartistid TEXT;
    ALTER TABLE library_index ADD COLUMN mb_artistid TEXT;
    ALTER TABLE library_index ADD COLUMN mb_trackid TEXT;
    ALTER TABLE library_index ADD COLUMN mb_releasetrackid TEXT;
    ALTER TABLE library_index ADD COLUMN compilation INTEGER;
    ALTER TABLE library_index ADD COLUMN releasetype TEXT;
    ALTER TABLE library_index ADD COLUMN label TEXT;
    ALTER TABLE library_index ADD COLUMN originaldate TEXT;
    ALTER TABLE library_index ADD COLUMN junk TEXT;
    ALTER TABLE library_index ADD COLUMN tags_version INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX idx_li_mb_album ON library_index(mb_albumid);

    CREATE TABLE plex_items (
      rating_key TEXT PRIMARY KEY,
      type TEXT NOT NULL,
      guid TEXT,
      mbid TEXT,
      title TEXT,
      parent_key TEXT,
      grandparent_key TEXT,
      parent_title TEXT,
      grandparent_title TEXT,
      idx INTEGER,
      parent_idx INTEGER,
      year INTEGER,
      studio TEXT,
      path TEXT,
      folder TEXT,
      genres TEXT,
      styles TEXT,
      moods TEXT,
      countries TEXT,
      locked TEXT,
      thumb TEXT,
      summary TEXT,
      added_at INTEGER,
      updated_at INTEGER,
      detail_at INTEGER,
      seen_at REAL NOT NULL
    );
    CREATE INDEX idx_plex_type ON plex_items(type);
    CREATE INDEX idx_plex_path ON plex_items(path);
    CREATE INDEX idx_plex_parent ON plex_items(parent_key);
    CREATE INDEX idx_plex_folder ON plex_items(folder);

    CREATE TABLE md_albums (
      key TEXT PRIMARY KEY,
      dirpath TEXT NOT NULL,
      artist_dir TEXT,
      album_tag TEXT,
      release_mbid TEXT,
      releasegroup_mbid TEXT,
      artist_mbid TEXT,
      confidence REAL,
      source TEXT,
      status TEXT NOT NULL DEFAULT 'pending',
      manual INTEGER NOT NULL DEFAULT 0,
      ignored INTEGER NOT NULL DEFAULT 0,
      desired TEXT,
      candidates TEXT,
      plex_key TEXT,
      file_sig TEXT,
      plex_sig TEXT,
      checked_at REAL,
      next_check REAL NOT NULL DEFAULT 0,
      priority INTEGER NOT NULL DEFAULT 0,
      error TEXT
    );
    CREATE INDEX idx_md_albums_dir ON md_albums(dirpath);
    CREATE INDEX idx_md_albums_artist ON md_albums(artist_dir);
    CREATE INDEX idx_md_albums_due ON md_albums(next_check);

    CREATE TABLE md_artists (
      artist_dir TEXT PRIMARY KEY,
      name TEXT,
      artist_mbid TEXT,
      status TEXT NOT NULL DEFAULT 'pending',
      desired TEXT,
      plex_key TEXT,
      checked_at REAL,
      next_check REAL NOT NULL DEFAULT 0,
      error TEXT
    );

    CREATE TABLE md_issues (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      key TEXT NOT NULL UNIQUE,
      kind TEXT NOT NULL,
      entity TEXT NOT NULL,
      ref TEXT NOT NULL,
      artist_dir TEXT,
      album_key TEXT,
      severity TEXT NOT NULL,
      summary TEXT NOT NULL,
      detail TEXT,
      auto INTEGER NOT NULL DEFAULT 0,
      state TEXT NOT NULL DEFAULT 'open',
      error TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE INDEX idx_issues_state ON md_issues(state, kind);
    CREATE INDEX idx_issues_artist ON md_issues(artist_dir, state);
    CREATE INDEX idx_issues_album ON md_issues(album_key, state);
    -- Issues that concern several albums or artists at once (a duplicate
    -- group, an artist split across folders) are listed under each of them.
    CREATE TABLE md_issue_refs (
      issue_id INTEGER NOT NULL REFERENCES md_issues(id) ON DELETE CASCADE,
      artist_dir TEXT,
      album_key TEXT
    );
    CREATE INDEX idx_issue_refs_issue ON md_issue_refs(issue_id);
    CREATE INDEX idx_issue_refs_album ON md_issue_refs(album_key);
    CREATE INDEX idx_issue_refs_artist ON md_issue_refs(artist_dir);

    CREATE TABLE md_changes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      batch_id TEXT NOT NULL,
      target TEXT NOT NULL,
      ref TEXT NOT NULL,
      artist_dir TEXT,
      album_key TEXT,
      field TEXT,
      old_value TEXT,
      new_value TEXT,
      summary TEXT,
      applied_at TEXT NOT NULL,
      reverted_at TEXT
    );
    CREATE INDEX idx_changes_batch ON md_changes(batch_id);
    CREATE INDEX idx_changes_album ON md_changes(album_key);
    CREATE INDEX idx_changes_artist ON md_changes(artist_dir);

    CREATE TABLE md_runs (
      id TEXT PRIMARY KEY,
      kind TEXT NOT NULL,
      report_only INTEGER NOT NULL DEFAULT 1,
      status TEXT NOT NULL,
      total INTEGER NOT NULL DEFAULT 0,
      done INTEGER NOT NULL DEFAULT 0,
      counts TEXT,
      started_at TEXT NOT NULL,
      finished_at TEXT,
      error TEXT
    );

    CREATE TABLE md_overrides (
      entity TEXT NOT NULL,
      ref TEXT NOT NULL,
      field TEXT NOT NULL,
      value TEXT NOT NULL,
      created_at TEXT NOT NULL,
      PRIMARY KEY (entity, ref, field)
    );

    CREATE TABLE md_state (
      key TEXT PRIMARY KEY,
      value TEXT
    );

    -- MusicBrainz / Last.fm responses. Separate from catalog_cache: different
    -- sources, different lifetimes, and pruned on their own schedule.
    CREATE TABLE meta_cache (
      key TEXT PRIMARY KEY,
      data TEXT NOT NULL,
      fetched_at REAL NOT NULL
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
