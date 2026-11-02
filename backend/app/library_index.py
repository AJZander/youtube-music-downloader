"""library_index is the ground truth for duplicate detection — not yt-dlp's
download archive (whose misuse in v1 claimed downloads whose files never
existed). Filled by library.scan, maintained by the importer."""
from pathlib import Path

from . import ownership
from .db import db, utcnow
from .engine.matcher import normalize


async def find_duplicate_match(provider_track_id: str | None, artist: str | None,
                               title: str | None, album: str | None = None,
                               duration: float | None = None,
                               kind: str | None = None) -> dict | None:
    """The library's copy of this track on this album, if it is already
    there: the whole ownership lookup, so a skip can say what it matched and
    how (`library_via`, `library_mismatch`).

    Same album only (ownership.LibraryIndex.lookup): the song on a
    compilation or a single is not a reason to skip it on the album being
    downloaded. With no album known — a bare video link — only a file
    downloaded from the very same video counts. `kind`: the release's type
    when the job knows it (a YouTube Music release), else None."""
    primary = (artist or "").split(",")[0].strip()
    index = await ownership.load_index({artist or ""}, [primary], [provider_track_id])
    found = index.lookup(title=title, credit=artist, album=album,
                         video_id=provider_track_id, length=duration, kind=kind)
    return found if found["library"] == "have" else None


async def find_duplicate(provider_track_id: str | None, artist: str | None,
                         title: str | None, album: str | None = None,
                         duration: float | None = None) -> str | None:
    """The library path of this track on this album, if it is already there
    (see find_duplicate_match)."""
    found = await find_duplicate_match(provider_track_id, artist, title, album, duration)
    return found["library_path"] if found else None


def held_message(found: dict) -> str:
    """Why the dedup gate skipped a track, for the Skipped list."""
    note = "; version or length differs" if found.get("library_mismatch") else ""
    return (f"Already in the library: {found['library_path']} "
            f"(matched by {found.get('library_via') or 'title'}{note})")


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


async def rebuild(progress_cb=None) -> dict:
    """Re-read every file in the library: library.scan(full=True). It used
    to empty library_index and write back 8 columns with no dirpath, which
    the next registry sync took for every album gone, Accept match and all.
    The scan upserts in place and keeps each file's provider id."""
    from . import library
    return await library.scan(full=True, progress_cb=progress_cb)


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
