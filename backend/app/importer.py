"""Staging -> library import. This module is the ONLY code that writes to
/library. Copy to a dot-prefixed temp name (invisible to Plex), fsync,
checksum-verify, then same-directory rename — with EIO retries because the
CIFS mount is 'soft' and surfaces transient server timeouts as IO errors."""
import asyncio
import hashlib
import json
import logging
import os
import shutil
import uuid
from pathlib import Path

from . import locks, relocate
from .config import settings
from .db import db, utcnow
from .engine import lyrics as lyrics_mod
from .engine import tagger
from .models import ErrorClass
from .paths import PathBuildError, build_track_relpath, sanitize_component
from .plex import plex

log = logging.getLogger("mdl.importer")

EIO_RETRY_DELAYS = [1, 5, 15, 60, 120]


class ImportError_(Exception):
    def __init__(self, code: str, message: str, error_class=ErrorClass.IMPORT_FAILED,
                 path: str | None = None):
        super().__init__(message)
        self.code = code
        self.error_class = error_class
        self.path = path   # the library file in the way, for dest_exists


def library_online() -> bool:
    try:
        return settings.library_sentinel.exists()
    except OSError:
        return False


def ensure_sentinel() -> None:
    """Create the sentinel once, when the mount is known-good (called at
    startup only if the library root has real content)."""
    try:
        root = settings.library_dir
        if root.is_dir() and any(root.iterdir()) and not settings.library_sentinel.exists():
            settings.library_sentinel.touch()
            log.info("library sentinel created")
    except OSError as exc:
        log.warning("cannot create library sentinel: %s", exc)


def _blake2b(path: Path) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def _retry_io(func, description: str):
    last_exc = None
    for attempt, delay in enumerate([0] + EIO_RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await asyncio.get_running_loop().run_in_executor(None, func)
        except OSError as exc:
            last_exc = exc
            log.warning("%s failed (attempt %d): %s", description, attempt + 1, exc)
            if not library_online():
                raise ImportError_("library_offline",
                                   "library mount is offline") from exc
    raise ImportError_("io_exhausted", f"{description}: {last_exc}") from last_exc


async def import_track(staging_file: Path, validated: dict, track_meta: dict,
                       force: bool = False, target_dir: str | None = None,
                       job_id: str | None = None, landed=None) -> dict:
    """Move a validated staging file into the library.
    track_meta carries authoritative metadata (Spotify) or {} (YouTube —
    validated tags are used). `target_dir` is an existing album folder the
    track belongs in (a missing track of that album): the file goes there,
    under its usual name, and the metadata manager tags and numbers it as
    that album's. Returns import result fields for the DB.

    The album folder is held (locks.hold) from the mkdir until the file is
    in it and `landed(result)` has run (the worker indexes it there), so no
    move, merge or Trash of the album runs meanwhile. `job_id`: the job the
    track is of. A move that ran while the track waited for the folder took
    the album elsewhere, and the job's target_dir with it (relocate): once
    the folder is held, it is read again, and the track follows."""
    if not library_online():
        raise ImportError_("library_offline", "library sentinel missing — mount down?")

    tags = validated["tags"]
    meta = {
        "title": track_meta.get("title") or tags["title"],
        "artist": track_meta.get("artist") or tags["artist"],
        "album": track_meta.get("album") or tags["album"],
        "album_artist": track_meta.get("album_artist") or tags.get("album_artist") or tags["artist"],
        "track_number": track_meta.get("track_number") or tags.get("track_number"),
        "disc_number": track_meta.get("disc_number") or tags.get("disc_number"),
        "release_date": track_meta.get("release_date") or tags.get("release_date"),
        "isrc": track_meta.get("isrc"),
    }
    checksum = await asyncio.get_running_loop().run_in_executor(None, _blake2b, staging_file)
    while True:
        relpath = await _destination(meta, staging_file.suffix, target_dir)
        async with locks.hold(str(relpath.parent)):
            if job_id is not None:
                row = await db.fetchone("SELECT target_dir FROM jobs WHERE id = ?", (job_id,))
                if row is not None and row["target_dir"] != target_dir:
                    target_dir = row["target_dir"]      # the album moved meanwhile
                    continue
            result = await _land(staging_file, relpath, meta, track_meta, validated,
                                 checksum, force)
            if landed is not None:
                await landed(result)
            return result


async def _destination(meta: dict, suffix: str, target_dir: str | None) -> Path:
    try:
        relpath = build_track_relpath(
            meta["album_artist"], meta["album"], meta["title"],
            suffix, meta.get("track_number"), meta.get("disc_number"),
            year=(meta.get("release_date") or "")[:4])
        if target_dir:
            relpath = _in_folder(target_dir, relpath.name)
    except PathBuildError as exc:
        raise ImportError_("path_build", str(exc), ErrorClass.VALIDATION_FAILED) from exc
    # The share ignores case: a "Sam Smith/..." file lands in the "SAM SMITH"
    # folder the library already has, so it is named as it lands, and its
    # index row isn't dropped by the next scan for a spelling nothing has.
    return Path(await relocate.on_disk(str(relpath.parent))) / relpath.name


async def _land(staging_file: Path, relpath: Path, meta: dict, track_meta: dict,
                validated: dict, checksum: str, force: bool) -> dict:
    """The copy, its check and the rename, then the sidecars."""
    dest = settings.library_dir / relpath
    dest_dir = dest.parent

    overwrote = dest.exists()
    if overwrote and not force:
        raise ImportError_("dest_exists", f"file already exists: {relpath}",
                           path=str(relpath))
    if overwrote:
        log.warning("force: overwriting existing library file %s", relpath)

    await _retry_io(lambda: dest_dir.mkdir(parents=True, exist_ok=True), "mkdir album dir")

    tmp_name = dest_dir / f".incoming-{uuid.uuid4().hex[:12]}{staging_file.suffix}"

    def _copy_and_verify():
        shutil.copyfile(staging_file, tmp_name)
        with open(tmp_name, "rb+") as f:
            f.flush()
            os.fsync(f.fileno())
        if tmp_name.stat().st_size != staging_file.stat().st_size:
            raise OSError("size mismatch after copy")
        if _blake2b(tmp_name) != checksum:
            raise OSError("checksum mismatch after copy")

    try:
        await _retry_io(_copy_and_verify, f"copy {relpath}")
        await _retry_io(lambda: os.replace(tmp_name, dest), f"rename {relpath}")
    except ImportError_:
        try:
            tmp_name.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    # Sidecar lyrics (best effort)
    try:
        synced = await lyrics_mod.fetch_synced(
            meta["artist"], meta["title"], meta["album"], validated.get("duration_sec"))
        if synced:
            lrc_tmp = dest.with_suffix(".lrc")
            await _retry_io(lambda: lrc_tmp.write_text(synced, encoding="utf-8"),
                            "write lrc")
    except Exception as exc:
        log.debug("lyrics sidecar skipped: %s", exc)

    # folder.jpg once per album (never overwrite, never leave webp)
    await _place_folder_art(dest_dir, staging_file, track_meta)

    return {
        "library_path": str(relpath),
        "checksum_blake2b": checksum,
        "meta": meta,
        "overwrote": overwrote,
    }


def _in_folder(folder: str, filename: str) -> Path:
    """`folder/filename`, refusing a folder that would leave the library
    (it comes from the database, but it is still a path to write to)."""
    parts = Path(folder).parts
    if not parts or Path(folder).is_absolute() or any(p in ("..", ".") or p.startswith(".")
                                                      for p in parts):
        raise PathBuildError(f"not a library folder: {folder!r}")
    return Path(folder) / filename


async def _place_folder_art(dest_dir: Path, staging_file: Path, track_meta: dict) -> None:
    try:
        art_dest = dest_dir / "folder.jpg"
        if await _retry_io(art_dest.exists, "check folder.jpg"):
            return
        image: bytes | None = None
        if track_meta.get("cover_url"):
            image = await tagger.fetch_cover(track_meta["cover_url"])
        if image is None:
            image = tagger.extract_embedded_cover(staging_file)
        if image is None:
            jpgs = [p for p in staging_file.parent.iterdir() if p.suffix.lower() == ".jpg"]
            if jpgs:
                image = jpgs[0].read_bytes()
        if not image:
            return
        if image[:3] != b"\xff\xd8\xff":  # not JPEG — convert
            from io import BytesIO

            from PIL import Image
            buf = BytesIO()
            Image.open(BytesIO(image)).convert("RGB").save(buf, "JPEG", quality=92)
            image = buf.getvalue()
        tmp = dest_dir / f".incoming-art-{uuid.uuid4().hex[:8]}.jpg"
        await _retry_io(lambda: tmp.write_bytes(image), "write folder art")
        await _retry_io(lambda: os.replace(tmp, art_dest), "rename folder art")
    except Exception as exc:
        log.debug("folder art skipped: %s", exc)


def quarantine(track_id: str, track_dir: Path, error: dict) -> str | None:
    """Move a failed track dir into quarantine with an error manifest."""
    try:
        settings.quarantine_dir.mkdir(parents=True, exist_ok=True)
        dest = settings.quarantine_dir / track_id
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        if track_dir.exists():
            shutil.move(str(track_dir), str(dest))
        else:
            dest.mkdir(parents=True)
        (dest / "import_error.json").write_text(
            json.dumps({**error, "ts": utcnow()}, indent=2, default=str))
        return str(dest)
    except OSError as exc:
        log.warning("quarantine failed for %s: %s", track_id, exc)
        return None


class AlbumScanDebouncer:
    """One Plex partial scan per album, ~20s after its last track lands."""

    def __init__(self, delay: float = 20.0):
        self._delay = delay
        self._pending: dict[str, asyncio.Task] = {}

    def schedule(self, album_relpath: str) -> None:
        existing = self._pending.pop(album_relpath, None)
        if existing:
            existing.cancel()
        self._pending[album_relpath] = asyncio.create_task(self._fire(album_relpath))

    async def _fire(self, album_relpath: str) -> None:
        try:
            await asyncio.sleep(self._delay)
            await plex.refresh_path(album_relpath)
        except asyncio.CancelledError:
            pass
        finally:
            self._pending.pop(album_relpath, None)

    async def flush(self) -> None:
        for task in list(self._pending.values()):
            task.cancel()
        self._pending.clear()


scan_debouncer = AlbumScanDebouncer()
