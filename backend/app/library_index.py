"""library_index is the ground truth for duplicate detection — not yt-dlp's
download archive (whose misuse in v1 claimed downloads whose files never
existed). Bootstrapped by a full mutagen scan, maintained by the importer."""
import asyncio
import logging
from pathlib import Path

from .config import settings
from .db import db, utcnow
from .engine.matcher import normalize
from .engine.tagger import read_tags

log = logging.getLogger("mdl.index")

AUDIO_SUFFIXES = {".mp3", ".flac", ".opus", ".m4a", ".ogg", ".wav", ".aac", ".wma"}


async def find_duplicate(provider_track_id: str | None, artist: str | None,
                         title: str | None) -> str | None:
    """Returns the existing library path if this track already exists."""
    if provider_track_id:
        row = await db.fetchone(
            "SELECT path FROM library_index WHERE provider_track_id = ?",
            (provider_track_id,))
        if row:
            return row["path"]
    if artist and title:
        row = await db.fetchone(
            "SELECT path FROM library_index WHERE norm_artist = ? AND norm_title = ?",
            (normalize(artist), normalize(title)))
        if row:
            return row["path"]
    return None


async def in_legacy_archive(provider: str, provider_id: str) -> bool:
    row = await db.fetchone(
        "SELECT 1 FROM legacy_archive WHERE provider = ? AND provider_id = ?",
        (provider, provider_id))
    return row is not None


async def add_entry(path: str, artist: str, title: str, album: str | None,
                    fmt: str | None, duration: float | None,
                    provider_track_id: str | None) -> None:
    await db.execute(
        """INSERT INTO library_index
           (norm_artist, norm_title, norm_album, path, format, duration_sec,
            provider_track_id, added_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET
             norm_artist=excluded.norm_artist, norm_title=excluded.norm_title,
             norm_album=excluded.norm_album, format=excluded.format,
             duration_sec=excluded.duration_sec,
             provider_track_id=excluded.provider_track_id""",
        (normalize(artist), normalize(title), normalize(album or ""),
         path, fmt, duration, provider_track_id, utcnow()))


def _scan_file(path: Path) -> tuple | None:
    try:
        tags = read_tags(path)
    except Exception:
        return None
    artist = tags.get("artist") or ""
    title = tags.get("title") or path.stem
    if not artist:
        # fall back to the artist folder name (library layout is Artist/Album/file)
        try:
            artist = path.relative_to(settings.library_dir).parts[0]
        except Exception:
            return None
    return (normalize(artist), normalize(title), normalize(tags.get("album") or ""),
            str(path.relative_to(settings.library_dir)),
            path.suffix.lstrip(".").lower(), tags.get("duration_sec"), None, utcnow())


async def rebuild(progress_cb=None) -> dict:
    """Full rescan of the library into library_index. Runs file IO in a
    thread; commits in batches. Takes a while over CIFS — CLI/maintenance."""
    root = settings.library_dir
    loop = asyncio.get_running_loop()

    def _walk() -> list[Path]:
        return [p for p in root.rglob("*")
                if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
                and not p.name.startswith(".")]

    files = await loop.run_in_executor(None, _walk)
    log.info("library rebuild: %d audio files found", len(files))
    # preserve provider-id associations of previously imported tracks
    known_ids = {r["path"]: r["provider_track_id"] for r in await db.fetchall(
        "SELECT path, provider_track_id FROM library_index "
        "WHERE provider_track_id IS NOT NULL")}
    await db.execute("DELETE FROM library_index")

    rows, scanned, skipped = [], 0, 0
    for i in range(0, len(files), 200):
        batch = files[i:i + 200]
        results = await loop.run_in_executor(
            None, lambda b=batch: [_scan_file(p) for p in b])
        for r in results:
            if r:
                if r[3] in known_ids:  # restore provider id for this path
                    r = r[:6] + (known_ids[r[3]],) + r[7:]
                rows.append(r)
            else:
                skipped += 1
        scanned += len(batch)
        if rows:
            await db.executemany(
                """INSERT INTO library_index
                   (norm_artist, norm_title, norm_album, path, format,
                    duration_sec, provider_track_id, added_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(path) DO NOTHING""",
                rows)
            rows = []
        if progress_cb:
            progress_cb(scanned, len(files))
    count = (await db.fetchone("SELECT COUNT(*) AS c FROM library_index"))["c"]
    log.info("library rebuild complete: %d indexed, %d unreadable", count, skipped)
    return {"files": len(files), "indexed": count, "unreadable": skipped}


async def import_legacy_archive(archive_path: Path) -> int:
    """Load v1 download_archive.txt IDs as ADVISORY data (UI badge only)."""
    if not archive_path.exists():
        return 0
    rows = []
    for line in archive_path.read_text(errors="ignore").splitlines():
        parts = line.strip().split()
        if len(parts) == 2:
            rows.append((parts[0], parts[1]))
    if rows:
        await db.executemany(
            "INSERT OR IGNORE INTO legacy_archive (provider, provider_id) VALUES (?,?)",
            rows)
    return len(rows)
