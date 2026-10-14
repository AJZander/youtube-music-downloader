"""Library-wide management: duplicate analysis and the safe-removal path.

The download side asks library_index one question — "does this track already
exist?". Managing an existing library asks the opposite one: *how many* copies
exist, and which copy is worth keeping. So everything here works on groups of
files and on the album folders that hold them.

Three rules the rest of the module is built around:

* **Nothing is deleted.** Removal moves a file into `<library>/.mdl-trash`
  (dot-prefixed, so Plex ignores it; same filesystem, so the move is atomic
  and instant) and records a row in library_trash. `purge` is the only code
  that unlinks, and only when explicitly asked.

* **A duplicate is the same track on the same album.** The same song on a
  compilation, a single or another edition is that release's copy, not a
  spare. Album names are compared through every convention this library
  carries — `Yours Truly`, `(2013) - Ariana Grande - Yours Truly`, `[2001]
  Hooray For Boobies`, `Artist - Album [FLAC 24bit]` — by tag and by folder,
  since the tags are missing on exactly the files that most need grouping;
  track-title overlap then confirms two folders really hold the same record.

* **Quality beats tags.** A copy can be re-tagged; it cannot be un-transcoded.
  So lossless outranks any bitrate, and the only thing that outranks lossless
  is evidence the file is truncated.
"""
import asyncio
import collections
import hashlib
import logging
import os
import re
import shutil
import time
import unicodedata
import uuid
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from .config import settings
from .db import db, utcnow
from .engine.matcher import normalize

log = logging.getLogger("mdl.library")

AUDIO_SUFFIXES = {".mp3", ".flac", ".opus", ".m4a", ".ogg", ".wav", ".aac", ".wma",
                  ".alac", ".aiff", ".wv", ".ape"}
# Files that belong to an album folder but are not music — they follow the
# audio into the trash when a whole folder goes, and never hold a folder open.
SIDECAR_SUFFIXES = {".lrc", ".jpg", ".jpeg", ".png", ".webp", ".nfo", ".txt",
                    ".cue", ".log", ".m3u", ".m3u8", ".sfv", ".md5", ".db", ".ini"}
LOSSLESS_FORMATS = {"flac", "wav", "alac", "aiff", "aif", "ape", "wv"}

# Duration difference that still counts as "the same recording". Wider than
# the matcher's ±3s: these are two rips of the same track from different
# sources, not a match against a known reference duration.
DURATION_TOLERANCE = 5.0

# Fraction of the smaller album's tracks that must appear in the larger one
# before the two folders are called the same album.
ALBUM_OVERLAP_THRESHOLD = 0.7

# A bucket this large is a compilation dumping ground ("Various Artists"),
# where pairwise comparison is both quadratic and meaningless.
MAX_BUCKET = 250

# Files per database commit during a scan. Large, so the worker pool is never
# waiting on a chunk boundary; a partial scan still keeps everything committed
# before the last boundary it crossed.
COMMIT_CHUNK = 2000


# ------------------------------------------------------------------ text keys
_YOUTUBE_JUNK = re.compile(
    r"\b(official\s*(music\s*)?(video|audio|visuali[sz]er)|lyric[s]?\s*video"
    r"|full\s*album|audio\s*only|hd|hq|4k|m/v|mv)\b", re.I)

_DIR_NOISE = [
    re.compile(r"^\s*[\(\[\{]?\s*(19|20)\d{2}\s*[\)\]\}]?\s*[-–—.]+\s*"),  # (2013) - / 2013 -
    re.compile(r"[\[\{][^\]\}]*[\]\}]"),                                    # [FLAC 24bit]
    re.compile(r"\((?:19|20)\d{2}\)"),                                      # (2017)
    re.compile(r"\b(flac|mp3|m4a|aac|opus|wav|web|webrip|cd\d?|cdrip|vinyl|"
               r"\d{2,3}\s?kbps|\d{3}k|v0|v2|\d{2}\s?bit|\d{2}(\.\d)?\s?khz|"
               r"hi\s?res|lossless|obey|scene)\b", re.I),
]


def strict_key(s: str) -> str:
    """Punctuation- and space-insensitive identity key. `AC_DC`, `AC/DC` and
    `ACDC` are one artist; `3 Doors Down` and `3doorsdown` are one artist."""
    s = unicodedata.normalize("NFKD", s or "").casefold()
    return re.sub(r"[^a-z0-9]+", "", s)


def title_keys(title: str | None, artist_hint: str | None = None) -> frozenset[str]:
    """Comparison keys for one track title.

    v1 wrote whole YouTube video titles into the title tag — `Ariana Grande -
    Baby I (Official Video)` sits next to a clean `Baby I`. Rather than guess
    which side of a dash is the title, emit both readings and call two tracks
    the same when *any* key matches. Matching on a set intersection instead of
    string equality is what lets a junk-tagged folder group with a clean one.
    """
    if not title:
        return frozenset()
    raw = _YOUTUBE_JUNK.sub(" ", title)
    keys = {normalize(raw)}
    if artist_hint:
        prefix = re.compile(r"^\s*" + re.escape(artist_hint) + r"\s*[-–—]\s*", re.I)
        keys.add(normalize(prefix.sub("", raw)))
    if " - " in raw or " – " in raw:
        keys.add(normalize(re.split(r"\s+[-–—]\s+", raw, maxsplit=1)[1]))
    return frozenset(k for k in keys if k)


def keys_for(row: dict) -> frozenset[str]:
    """title_keys for a scanned row, memoized on the row itself.

    Album grouping compares every pair of folders an artist owns, so an
    un-memoized call recomputes the same regex work thousands of times for a
    prolific artist. The cache lives on the dict, which is per-analysis."""
    cached = row.get("_keys")
    if cached is None:
        cached = row["_keys"] = title_keys(
            row.get("title"), row.get("album_artist") or row.get("artist_dir"))
    return cached


def album_key(folder_or_album: str | None, artist_hint: str | None = None) -> str:
    """Normalized album identity from a folder name or an album tag, with the
    release-year prefixes, scene tags and repeated artist name stripped."""
    if not folder_or_album:
        return ""
    name = folder_or_album
    if artist_hint:
        name = re.sub(r"^\s*" + re.escape(artist_hint) + r"\s*[-–—]\s*", " ", name, flags=re.I)
    for pattern in _DIR_NOISE:
        name = pattern.sub(" ", name)
    if artist_hint:  # the year prefix may have been hiding the artist name
        name = re.sub(r"^\s*" + re.escape(artist_hint) + r"\s*[-–—]\s*", " ", name, flags=re.I)
    return strict_key(name)


# A remaster is the same album; a deluxe or anniversary edition is not.
_ALBUM_REMASTER = re.compile(
    r"\s*[\(\[][^\)\]]*\bremaster(?:ed)?\b[^\)\]]*[\)\]]"
    r"|\s*[-–—:]\s*(?:\d{4}\s+)?(?:digital(?:ly)?\s+)?remaster(?:ed)?\b.*$", re.I)


def album_identity(name: str | None, artist_hints=()) -> frozenset[str]:
    """Keys an album name is known by — two names are the same album when
    any key is shared.

    One key per artist hint, since a folder may or may not repeat the artist
    (`(2013) - Ariana Grande - Yours Truly`). YouTube junk (`Full Album`) and
    remaster tags go first; edition words stay, because `Seventy (Deluxe
    Edition)` is a different album from `Seventy`."""
    if not name:
        return frozenset()
    base = _ALBUM_REMASTER.sub("", _YOUTUBE_JUNK.sub(" ", name))
    keys = {album_key(base)} | {album_key(base, h) for h in artist_hints if h}
    keys.discard("")
    return frozenset(keys)


def row_album_identity(row: dict) -> frozenset[str]:
    """What album a library file is on: its album tag and, unless it sits
    loose in the artist folder, the folder it is in. Memoized on the row."""
    cached = row.get("_albums")
    if cached is None:
        path = row.get("path") or ""
        top = path.split("/", 1)[0]
        hints = {row.get("artist_dir"), row.get("album_artist"), top}
        folder = row.get("dirpath") or path.rsplit("/", 1)[0]
        cached = album_identity(row.get("album"), hints) | album_identity(
            row.get("norm_album"), hints)
        if folder and folder != top:   # a loose file's folder is the artist
            cached |= album_identity(folder.rsplit("/", 1)[-1], hints)
        row["_albums"] = cached
    return cached


def title_looks_like_junk(title: str | None) -> bool:
    """True for a title tag that is really a YouTube video title."""
    if not title:
        return True
    return bool(_YOUTUBE_JUNK.search(title)) or bool(
        re.search(r"\b(ft\.?|feat\.?)\b", title, re.I) and " - " in title)


# ------------------------------------------------------------------ scanning
@dataclass
class ScanState:
    running: bool = False
    phase: str = "idle"          # walking | reading | pruning | idle
    total: int = 0
    done: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    unreadable: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    _t0: float = 0.0

    def snapshot(self) -> dict:
        d = self.__dict__.copy()
        d.pop("_t0", None)
        d["pct"] = round(100 * self.done / self.total, 1) if self.total else None
        # A full first scan is an hour's work on a NAS-backed library; showing
        # a bare percentage with no rate leaves it looking hung.
        elapsed = time.monotonic() - self._t0 if self._t0 else 0
        if self.done and elapsed > 5:
            rate = self.done / elapsed
            d["rate_per_sec"] = round(rate, 1)
            d["eta_sec"] = int((self.total - self.done) / rate) if rate else None
        else:
            d["rate_per_sec"] = None
            d["eta_sec"] = None
        return d


scan_state = ScanState()
_scan_lock = asyncio.Lock()

# Bumped when the reader learns new fields: rows read by an older reader are
# stale, so the next incremental scan re-reads them without a --full.
TAGS_VERSION = 1

_COLUMNS = ("norm_artist", "norm_title", "norm_album", "path", "format",
            "duration_sec", "artist", "title", "album", "album_artist",
            "track_number", "disc_number", "year", "bitrate_kbps", "sample_rate",
            "bit_depth", "channels", "filesize_bytes", "mtime", "dirpath",
            "artist_dir", "scanned_at", "added_at", "unreadable",
            "genre", "isrc", "mb_albumid", "mb_releasegroupid", "mb_albumartistid",
            "mb_artistid", "mb_trackid", "mb_releasetrackid", "compilation",
            "releasetype", "label", "originaldate", "junk", "tags_version")
_EXTENDED = ("genre", "isrc", "mb_albumid", "mb_releasegroupid", "mb_albumartistid",
             "mb_artistid", "mb_trackid", "mb_releasetrackid", "compilation",
             "releasetype", "label", "originaldate", "junk", "tags_version")

_UPSERT = f"""
INSERT INTO library_index ({','.join(_COLUMNS)})
VALUES ({','.join('?' * len(_COLUMNS))})
ON CONFLICT(path) DO UPDATE SET
  norm_artist=excluded.norm_artist, norm_title=excluded.norm_title,
  norm_album=excluded.norm_album, format=excluded.format,
  duration_sec=excluded.duration_sec, artist=excluded.artist,
  title=excluded.title, album=excluded.album, album_artist=excluded.album_artist,
  track_number=excluded.track_number, disc_number=excluded.disc_number,
  year=excluded.year, bitrate_kbps=excluded.bitrate_kbps,
  sample_rate=excluded.sample_rate, bit_depth=excluded.bit_depth,
  channels=excluded.channels, filesize_bytes=excluded.filesize_bytes,
  mtime=excluded.mtime, dirpath=excluded.dirpath, artist_dir=excluded.artist_dir,
  scanned_at=excluded.scanned_at, unreadable=excluded.unreadable,
  genre=excluded.genre, isrc=excluded.isrc, mb_albumid=excluded.mb_albumid,
  mb_releasegroupid=excluded.mb_releasegroupid,
  mb_albumartistid=excluded.mb_albumartistid, mb_artistid=excluded.mb_artistid,
  mb_trackid=excluded.mb_trackid, mb_releasetrackid=excluded.mb_releasetrackid,
  compilation=excluded.compilation, releasetype=excluded.releasetype,
  label=excluded.label, originaldate=excluded.originaldate, junk=excluded.junk,
  tags_version=excluded.tags_version,
  -- a rescan must not forget which download produced the file
  provider_track_id=COALESCE(library_index.provider_track_id,
                             excluded.provider_track_id)
"""


def _make_pool(workers: int):
    """Processes where we can get them, threads where we cannot."""
    try:
        return ProcessPoolExecutor(max_workers=workers), "process"
    except (OSError, ValueError, ImportError) as exc:
        log.warning("process pool unavailable (%s) — using threads", exc)
        return ThreadPoolExecutor(max_workers=workers), "thread"


def _run_batches(pool, root_str: str, chunk: list, on_batch=None) -> list:
    results: list = []
    for batch in pool.map(partial(_read_batch, root_str), _sub_batches(chunk)):
        results.extend(batch)
        if on_batch:
            on_batch(len(batch))
    return results


def _int(value) -> int | None:
    if value is None:
        return None
    text = str(value).split("/")[0].strip()
    try:
        return int(re.sub(r"[^\d-]", "", text) or "0") or None
    except ValueError:
        return None


def _walk(root: Path) -> dict[str, tuple[int, float]]:
    """rel path -> (size, mtime) for every audio file, skipping our own trash."""
    found: dict[str, tuple[int, float]] = {}
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError as exc:
            log.warning("scandir failed on %s: %s", current, exc)
            continue
        for entry in entries:
            name = entry.name
            if name.startswith("."):          # .mdl-trash, .incoming-*, dotfiles
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif Path(name).suffix.lower() in AUDIO_SUFFIXES:
                    st = entry.stat()
                    found[str(Path(entry.path).relative_to(root))] = (st.st_size, st.st_mtime)
            except OSError:
                continue
    return found


def _unreadable_row(root: Path, rel: str, size: int, mtime: float) -> tuple:
    """A placeholder row for a file mutagen will not open.

    Recording it — rather than returning nothing — is what stops every future
    scan from reopening the same broken file, and keeps it out of the "not
    read yet" count that the duplicate report warns about."""
    parts = Path(rel).parts
    artist_dir = parts[0] if len(parts) > 1 else ""
    dirpath = str(Path(rel).parent) if len(parts) > 1 else ""
    stem = Path(rel).stem
    return (normalize(artist_dir), normalize(stem), "", rel,
            Path(rel).suffix.lstrip(".").lower(), None,
            None, stem, None, None, None, None, None, None, None, None, None,
            size, mtime, dirpath, artist_dir, utcnow(), utcnow(), 1,
            None, None, None, None, None, None, None, None, None, None, None, None, None,
            TAGS_VERSION)


def _read_file(root: Path, rel: str, size: int, mtime: float) -> tuple:
    """One row for library_index; a placeholder row if the file will not open."""
    import json

    from .engine import tagio

    path = root / rel
    parts = Path(rel).parts
    artist_dir = parts[0] if len(parts) > 1 else ""
    dirpath = str(Path(rel).parent) if len(parts) > 1 else ""

    data = None
    if path.suffix.lower() in tagio.WRITABLE_SUFFIXES:
        try:
            data = tagio.read(path)
        except tagio.TagError:
            data = None          # a container tagio can't do (raw AAC): the plain reader
        except Exception:
            return _unreadable_row(root, rel, size, mtime)
    if data is not None:
        fields, info = data["fields"], data["info"]

        def tag(key: str) -> str | None:
            values = fields.get(key) or []
            text = str(values[0]).strip() if values else ""
            return text or None

        def joined(key: str) -> str | None:
            values = [v for v in fields.get(key) or [] if str(v).strip()]
            return "; ".join(values) if values else None

        length = info.get("length")
        bitrate_kbps = info.get("bitrate_kbps")
        sample_rate = info.get("sample_rate")
        bits = info.get("bits_per_sample")
        channels = info.get("channels")
        extended = (
            joined("genre"), tag("isrc"), tag("mb_albumid"), tag("mb_releasegroupid"),
            tag("mb_albumartistid"), tag("mb_artistid"), tag("mb_trackid"),
            tag("mb_releasetrackid"), 1 if tag("compilation") not in (None, "0") else None,
            joined("releasetype"), tag("label"), tag("originaldate"),
            json.dumps(data["junk"]) if data["junk"] else None, TAGS_VERSION)
    else:
        import mutagen
        try:
            audio = mutagen.File(path, easy=True)
        except Exception:
            return _unreadable_row(root, rel, size, mtime)
        if audio is None:
            return _unreadable_row(root, rel, size, mtime)

        def tag(key: str) -> str | None:
            try:
                value = audio.get(key)
            except Exception:
                return None
            if not value:
                return None
            text = str(value[0]).strip()
            return text or None

        info = getattr(audio, "info", None)
        length = getattr(info, "length", None)
        bitrate = getattr(info, "bitrate", None)
        bitrate_kbps = int(bitrate / 1000) if bitrate else None
        sample_rate = getattr(info, "sample_rate", None)
        bits = getattr(info, "bits_per_sample", None)
        channels = getattr(info, "channels", None)
        extended = (tag("genre"), tag("isrc"), tag("musicbrainz_albumid"), None, None,
                    tag("musicbrainz_artistid"), tag("musicbrainz_trackid"), None, None,
                    None, None, None, None, TAGS_VERSION)

    title = tag("title") or Path(rel).stem
    artist = tag("artist")
    album_artist = tag("albumartist")
    album = tag("album")
    # Artist is the one field with a trustworthy fallback: the library layout
    # is Artist/Album/file, so the top folder is what Plex will call it anyway.
    effective_artist = artist or album_artist or artist_dir
    date = tag("date") or tag("originaldate")

    return (
        normalize(effective_artist), normalize(title), normalize(album or ""),
        rel, path.suffix.lstrip(".").lower(),
        length,
        artist, title, album, album_artist,
        _int(tag("tracknumber")), _int(tag("discnumber")),
        (date or "")[:4] or None,
        bitrate_kbps, sample_rate, bits, channels,
        size, mtime, dirpath, artist_dir, utcnow(), utcnow(), 0,
        *extended,
    )


def _read_batch(root_str: str, items: list[tuple]) -> list:
    """Tag-read a slice of the work list. Runs in a worker *process*: mutagen
    parsing is pure Python, so threads serialise on the GIL — measured on this
    library, thread pools plateau at ~100% of one core no matter the width.
    Batching amortises the pickling cost of handing work across."""
    root = Path(root_str)
    return [_read_file(root, rel, size, mtime) for rel, size, mtime in items]


def _sub_batches(chunk: list, size: int = 8) -> list[list]:
    """Small units of work, many more of them than there are workers.

    Sized so the pool always has something to steal: with sub-batches as large
    as the commit chunk divided by the worker count, the whole chunk waits on
    its slowest straggler and the pool sits idle in between."""
    return [chunk[i:i + size] for i in range(0, len(chunk), size)]


async def scan(full: bool = False, progress_cb=None) -> dict:
    """Refresh library_index from the filesystem.

    Incremental by default: a file whose (size, mtime) is unchanged is not
    reopened. That matters — walking this library takes ~30s but reading tags
    off 61k files over CIFS takes far longer, so a rescan after resolving
    duplicates has to cost a stat() per file, not a parse.
    """
    if _scan_lock.locked():
        raise RuntimeError("a library scan is already running")
    async with _scan_lock:
        state = scan_state
        state.__init__()
        state.running = True
        state.started_at = utcnow()
        state._t0 = time.monotonic()
        state.phase = "walking"
        root = settings.library_dir
        loop = asyncio.get_running_loop()
        try:
            found = await loop.run_in_executor(None, _walk, root)
            log.info("library scan: %d audio files on disk", len(found))

            known = {r["path"]: (r["filesize_bytes"], r["mtime"], r["scanned_at"],
                                 r.get("tags_version", TAGS_VERSION))
                     for r in map(dict, await db.fetchall(
                         "SELECT path, filesize_bytes, mtime, scanned_at, tags_version "
                         "FROM library_index"))}

            stale = []
            for rel, (size, mtime) in found.items():
                previous = known.get(rel)
                if (full or previous is None or previous[2] is None
                        or (previous[3] or 0) < TAGS_VERSION
                        or previous[0] != size
                        or previous[1] is None or abs(previous[1] - mtime) > 1):
                    stale.append((rel, size, mtime))
            state.total = len(stale)
            state.phase = "reading"
            log.info("library scan: %d files need reading", len(stale))

            workers = max(4, settings.library_scan_workers)
            pool, pooled = _make_pool(workers)

            def tick(n: int) -> None:
                # Called from the executor thread as each sub-batch lands, so
                # progress moves smoothly instead of jumping a commit at a time.
                state.done += n

            try:
                # The commit chunk is far larger than the pool so every worker
                # stays fed; the DB write is one executemany per chunk.
                for start in range(0, len(stale), COMMIT_CHUNK):
                    chunk = stale[start:start + COMMIT_CHUNK]
                    before = state.done
                    try:
                        results = await loop.run_in_executor(
                            None, _run_batches, pool, str(root), chunk, tick)
                    except BrokenExecutor:
                        # A worker process died (OOM killer, an unreadable file
                        # that segfaults a codec). Fall back to threads rather
                        # than abandoning a scan that is most of the way done.
                        log.warning("scan worker pool broke — falling back to threads")
                        pool.shutdown(wait=False)
                        pool, pooled = ThreadPoolExecutor(max_workers=workers), "thread"
                        state.done = before
                        results = await loop.run_in_executor(
                            None, _run_batches, pool, str(root), chunk, tick)
                    rows = [r for r in results if r]
                    state.unreadable += sum(1 for r in rows if r[-1])
                    for rel, _size, _mtime in chunk:
                        if rel in known:
                            state.updated += 1
                        else:
                            state.added += 1
                    if rows:
                        await db.executemany(_UPSERT, rows)
                    if progress_cb:
                        progress_cb(state.done, state.total)
            finally:
                pool.shutdown(wait=False)
            log.info("scan read %d files using a %s pool", state.done, pooled)

            state.phase = "pruning"
            gone = [p for p in known if p not in found]
            # A walk that hit IO errors returns a short list, and pruning
            # against it would delete index rows for files that are fine — the
            # mount dropping out mid-scan must not look like a mass deletion.
            if known and len(found) < 0.5 * len(known):
                log.warning("skipping prune: walk found %d files against %d indexed "
                            "— the mount looks unhealthy", len(found), len(known))
                state.error = (f"pruning skipped: only {len(found)} of {len(known)} "
                               "indexed files were visible")
                gone = []
            for start in range(0, len(gone), 500):
                batch = gone[start:start + 500]
                await db.execute(
                    f"DELETE FROM library_index WHERE path IN ({','.join('?' * len(batch))})",
                    tuple(batch))
            state.removed = len(gone)
            log.info("library scan complete: +%d ~%d -%d (%d unreadable)",
                     state.added, state.updated, state.removed, state.unreadable)
        except Exception as exc:
            state.error = str(exc)[:300]
            log.exception("library scan failed")
            raise
        finally:
            state.running = False
            state.phase = "idle"
            state.finished_at = utcnow()
        return state.snapshot()


# ------------------------------------------------------------------ scoring
def format_quality(row: dict) -> int:
    fmt = (row.get("format") or "").lower()
    if fmt in LOSSLESS_FORMATS:
        score = 1000
        if (row.get("bit_depth") or 16) > 16:
            score += 60
        if (row.get("sample_rate") or 44100) > 48000:
            score += 30
        return score
    return min(row.get("bitrate_kbps") or 0, 400)


def tag_quality(row: dict) -> int:
    score = 0
    if row.get("album_artist"):
        score += 40
    if row.get("album"):
        score += 40
    if row.get("track_number"):
        score += 30
    if row.get("year"):
        score += 10
    if not title_looks_like_junk(row.get("title")):
        score += 40
    return score


def score_file(row: dict, reference_duration: float | None = None) -> tuple[int, list[str]]:
    """Rank one copy of a track. Returns (score, human-readable reasons).

    The reasons are the point: the UI has to be able to say *why* it wants to
    delete the file you spent bandwidth on, or it will not be trusted.
    """
    reasons: list[str] = []
    fmt = (row.get("format") or "?").lower()
    quality = format_quality(row)
    if fmt in LOSSLESS_FORMATS:
        detail = f"lossless {fmt}"
        if (row.get("bit_depth") or 0) > 16:
            detail += f" {row['bit_depth']}-bit"
        if (row.get("sample_rate") or 0) > 48000:
            detail += f" {row['sample_rate'] // 1000}kHz"
        reasons.append(detail)
    elif row.get("bitrate_kbps"):
        reasons.append(f"{row['bitrate_kbps']} kbps {fmt}")
    else:
        reasons.append(fmt)

    score = quality
    duration = row.get("duration_sec")
    if reference_duration and duration:
        delta = duration - reference_duration
        if delta < -DURATION_TOLERANCE * 2:
            score -= 900
            reasons.append(f"{abs(round(delta))}s shorter than the other copies "
                           "— likely truncated")
        elif delta > 20:
            reasons.append(f"{round(delta)}s longer (extended version or trailing silence)")

    tags = tag_quality(row)
    score += tags
    missing = [name for name, key in
               (("album artist", "album_artist"), ("album", "album"),
                ("track number", "track_number")) if not row.get(key)]
    if missing:
        reasons.append("missing " + ", ".join(missing))
    if title_looks_like_junk(row.get("title")):
        reasons.append("title is a YouTube video title")
    return score, reasons


# ------------------------------------------------------------------ grouping
def pair_key(a: str, b: str) -> tuple[str, str]:
    """Order-independent identity for a pair of library items."""
    return (a, b) if a <= b else (b, a)


async def load_exclusions() -> dict[str, set[tuple[str, str]]]:
    """Pairs the user has said may coexist, by scope."""
    out: dict[str, set[tuple[str, str]]] = collections.defaultdict(set)
    for row in await db.fetchall(
            "SELECT scope, key_a, key_b FROM library_exclusions"):
        out[row["scope"]].add((row["key_a"], row["key_b"]))
    return out


async def add_exclusions(scope: str, keys: list[str], reason: str = "") -> int:
    """Record that every listed item may coexist with every other.

    Stored pairwise rather than as a group: a later scan may turn up a
    different set of copies, and the judgement that A and B are not duplicates
    of each other still holds within it."""
    unique = sorted(set(k for k in keys if k))
    if len(unique) < 2:
        raise LibraryError("need at least two items to mark as not duplicates")
    rows = [(scope, *pair_key(a, b), reason, utcnow())
            for i, a in enumerate(unique) for b in unique[i + 1:]]
    await db.executemany(
        "INSERT OR IGNORE INTO library_exclusions "
        "(scope, key_a, key_b, reason, created_at) VALUES (?,?,?,?,?)", rows)
    log.info("recorded %d %s exclusion pairs (%s)", len(rows), scope, reason or "no reason")
    return len(rows)


async def list_exclusions() -> dict:
    """Recorded decisions, collapsed back into the groups they came from so
    the UI can show and undo them as one thing."""
    rows = [dict(r) for r in await db.fetchall(
        "SELECT * FROM library_exclusions ORDER BY created_at DESC, id DESC")]
    buckets: dict[tuple, dict] = {}
    for row in rows:
        key = (row["scope"], row["reason"] or "", row["created_at"])
        entry = buckets.setdefault(key, {
            "scope": row["scope"], "reason": row["reason"],
            "created_at": row["created_at"], "ids": [], "members": set(),
        })
        entry["ids"].append(row["id"])
        entry["members"].update((row["key_a"], row["key_b"]))
    items = [{**e, "members": sorted(e["members"])} for e in buckets.values()]
    return {"total": len(rows), "items": items}


async def remove_exclusions(ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    await db.execute(
        f"DELETE FROM library_exclusions WHERE id IN ({placeholders})", tuple(ids))
    return len(ids)


class _Union:
    def __init__(self):
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def groups(self) -> dict:
        out = collections.defaultdict(list)
        for node in self.parent:
            out[self.find(node)].append(node)
        return out


def _artist_buckets(row: dict) -> set[str]:
    """Every name under which this file might be filed. An album split across
    `Bob Marley` and `Bob Marley & The Wailers` only groups because the tag
    and the folder are both offered as bucket keys."""
    keys = {strict_key(row.get("artist_dir") or ""),
            strict_key(row.get("album_artist") or "")}
    return {k for k in keys if k}


@dataclass
class AlbumInstance:
    """One album as it physically exists: a directory holding audio files."""
    dirpath: str
    artist_dir: str
    files: list = field(default_factory=list)

    @property
    def is_loose(self) -> bool:
        """Files sitting directly in the artist folder, not in an album folder.
        Never safe to remove wholesale — the folder is not the album."""
        return self.dirpath == self.artist_dir

    def _dominant(self, key: str) -> str | None:
        values = [f[key] for f in self.files if f.get(key)]
        return collections.Counter(values).most_common(1)[0][0] if values else None

    def summary(self) -> dict:
        album = self._dominant("album")
        album_artist = self._dominant("album_artist") or self._dominant("artist")
        durations = [f["duration_sec"] for f in self.files if f.get("duration_sec")]
        scores = sorted(score_file(f)[0] for f in self.files)
        formats = sorted({(f.get("format") or "?") for f in self.files})
        return {
            "dirpath": self.dirpath,
            "artist_dir": self.artist_dir,
            "album": album or Path(self.dirpath).name,
            "album_artist": album_artist or self.artist_dir,
            "year": self._dominant("year"),
            "track_count": len(self.files),
            "formats": formats,
            "lossless": all(f.get("format") in LOSSLESS_FORMATS for f in self.files),
            "bytes": sum(f.get("filesize_bytes") or 0 for f in self.files),
            "duration_sec": round(sum(durations)) if durations else None,
            "avg_bitrate": (round(sum(f["bitrate_kbps"] for f in self.files
                                      if f.get("bitrate_kbps"))
                                  / max(1, sum(1 for f in self.files
                                               if f.get("bitrate_kbps"))))
                            if any(f.get("bitrate_kbps") for f in self.files) else None),
            "score": scores[len(scores) // 2] if scores else 0,
            "is_loose": self.is_loose,
            "health": self.health(),
        }

    def health(self) -> list[str]:
        issues = []
        if not any(f.get("album") for f in self.files):
            issues.append("no album tag")
        if not any(f.get("album_artist") for f in self.files):
            issues.append("no album artist tag")
        if not all(f.get("track_number") for f in self.files):
            issues.append("missing track numbers")
        if sum(1 for f in self.files if title_looks_like_junk(f.get("title"))):
            issues.append("junk titles")
        if len({f.get("format") for f in self.files}) > 1:
            issues.append("mixed formats")
        return issues

    def key_index(self) -> dict[str, dict]:
        index = {}
        for f in self.files:
            for key in keys_for(f):
                index.setdefault(key, f)
        return index


def build_album_instances(rows: list[dict]) -> dict[str, AlbumInstance]:
    instances: dict[str, AlbumInstance] = {}
    for row in rows:
        dirpath = row.get("dirpath") or ""
        artist_dir = row.get("artist_dir") or dirpath
        inst = instances.get(dirpath)
        if inst is None:
            inst = instances[dirpath] = AlbumInstance(dirpath, artist_dir)
        inst.files.append(row)
    return instances


def _album_overlap(a: AlbumInstance, b: AlbumInstance) -> tuple[float, int]:
    """(fraction of the smaller album's tracks found in the larger, hit count).

    The raw count matters as much as the fraction: a one-track singles folder
    scores a perfect 1.0 against any album that happens to contain that song.
    """
    index_b = b.key_index()
    hits = sum(1 for f in a.files if any(k in index_b for k in keys_for(f)))
    return hits / max(1, min(len(a.files), len(b.files))), hits


def _instance_identity(inst: AlbumInstance) -> frozenset[str]:
    """Every name an album folder answers to: its files' album tags and the
    folder's own name, each through album_identity."""
    keys: set[str] = set()
    for f in inst.files:
        keys |= row_album_identity(f)
    return frozenset(keys)


def find_album_duplicates(rows: list[dict],
                          excluded: set[tuple[str, str]] = frozenset()) -> list[dict]:
    """Album folders holding the same album, however they are named.

    Both have to agree: the name (by tag or by folder, through every naming
    convention — `Yours Truly` vs `(2013) - Ariana Grande - Yours Truly`,
    where one side has no usable tags at all) and the tracks. A deluxe
    edition beside the standard one, or a compilation sharing most of an
    album's songs, is a different album and is not reported.
    """
    instances = build_album_instances(rows)
    buckets: dict[str, list[AlbumInstance]] = collections.defaultdict(list)
    for inst in instances.values():
        if inst.is_loose or not inst.files:
            continue   # loose artist-folder files are handled as track dupes
        keys = set()
        for f in inst.files:
            keys |= _artist_buckets(f)
        for key in keys:
            buckets[key].append(inst)

    names = {d: _instance_identity(inst) for d, inst in instances.items()}
    union = _Union()
    exact_pairs: set[tuple[str, str]] = set()
    for key, members in buckets.items():
        if len(members) < 2:
            continue
        if len(members) > MAX_BUCKET:
            log.info("skipping oversized artist bucket %r (%d albums)", key, len(members))
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if a.dirpath == b.dirpath:
                    continue
                if pair_key(a.dirpath, b.dirpath) in excluded:
                    continue   # the user has said these two are different albums
                if not (names[a.dirpath] & names[b.dirpath]):
                    continue   # different albums, whatever they share
                overlap, hits = _album_overlap(a, b)
                if overlap < ALBUM_OVERLAP_THRESHOLD:
                    continue
                union.union(a.dirpath, b.dirpath)
                exact_pairs.add(tuple(sorted((a.dirpath, b.dirpath))))

    groups = []
    for members in union.groups().values():
        if len(members) < 2:
            continue
        # Best copy first — this is the group's initial suggestion, and the
        # order the browser then holds steady while the user changes it.
        summaries = sorted((instances[d].summary() for d in members),
                           key=lambda s: (-s["score"], -s["track_count"], -s["bytes"]))
        keeper = summaries[0]
        exact = any(tuple(sorted((x["dirpath"], y["dirpath"]))) in exact_pairs
                    for x in summaries for y in summaries if x is not y)
        keeper_index = instances[keeper["dirpath"]].key_index()
        reclaim = 0
        for loser in summaries[1:]:
            inst = instances[loser["dirpath"]]
            covered, orphans = [], []
            for f in inst.files:
                (covered if any(k in keeper_index for k in keys_for(f))
                 else orphans).append(f)
            loser["covered"] = [_file_view(f, keeper_index) for f in covered]
            loser["orphans"] = [_file_view(f, keeper_index) for f in orphans]
            reclaim += sum(f.get("filesize_bytes") or 0 for f in covered)
        keeper["is_keeper"] = True
        keeper["files"] = [_file_view(f, {}) for f in
                           instances[keeper["dirpath"]].files]
        groups.append({
            # Two groups can legitimately share a keeper folder — a box set
            # duplicates several albums at once — so the id covers all members.
            "id": "alb:" + hashlib.blake2b(
                "\u0000".join(sorted(members)).encode(), digest_size=8).hexdigest(),
            "kind": "exact" if exact else "overlap",
            "album": _consensus_album(summaries),
            "album_artist": keeper["album_artist"],
            "instances": summaries,
            "reclaimable_bytes": reclaim,
            "has_orphans": any(s.get("orphans") for s in summaries[1:]),
        })
    _rename_box_set_groups(groups)
    groups.sort(key=lambda g: -g["reclaimable_bytes"])
    return groups


def _rename_box_set_groups(groups: list[dict]) -> None:
    """Strip box-set names that ended up heading several different albums.

    A name shared by more than one group cannot be the name of any of them —
    it is the set the discs were ripped from. Re-vote each affected group with
    those names excluded, so a group of Cowboys From Hell folders is called
    that even when half of them are tagged with the box set's name."""
    shared = {name for name, n in collections.Counter(
        g["album"] for g in groups).items() if n > 1}
    for group in groups:
        if group["album"] not in shared:
            continue
        candidates = [s for s in group["instances"]
                      if s.get("album") and s["album"] not in shared]
        if candidates:
            group["album"] = _consensus_album(candidates)
        else:
            # Every copy is tagged with the set name — the folders are the
            # only remaining clue.
            group["album"] = _album_name_from_folders(group["instances"])

    # Whatever the heuristics above produced, two groups sharing one heading
    # are two rows the user cannot tell apart. Fall back to the folder, which
    # is unique by construction.
    still_shared = {name for name, n in collections.Counter(
        g["album"] for g in groups).items() if n > 1}
    for group in groups:
        if group["album"] in still_shared:
            folder = _album_name_from_folders(group["instances"])
            if folder and folder != group["album"]:
                group["album"] = f"{group['album']} — {folder}"


_DISC_FOLDER = re.compile(r"^(disc|disk|cd)\s*\.?\s*\d+$", re.I)


def pretty_folder_name(name: str, artist_hint: str | None = None) -> str:
    """A folder name fit to print: year prefixes, scene brackets and a repeated
    artist stripped, but the actual words and capitalisation kept.

    album_key() answers "are these the same album"; this answers "what do I
    call it", so it cannot go through the strict key — a heading of
    `2003slayersoundtracktotheapocalypse` helps nobody."""
    for pattern in _DIR_NOISE[:3]:      # year prefix, [brackets], (YYYY)
        name = pattern.sub(" ", name)
    if artist_hint:
        name = re.sub(r"^\s*" + re.escape(artist_hint) + r"\s*[-–—]\s*", " ",
                      name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip(" -–—_")
    return name


def _album_name_from_folders(summaries: list[dict]) -> str:
    """Best album name the folder layout can offer.

    `Box Set/Disc 1` names the disc, not the album, so a folder called that
    defers to a sibling copy with a real name. When every copy is a disc of
    some box set, the set plus the disc is the only honest heading — and it
    keeps two discs of the same set from sharing one."""
    artist = summaries[0].get("album_artist")
    for summary in summaries:
        basename = Path(summary["dirpath"]).name
        if not _DISC_FOLDER.match(basename):
            return pretty_folder_name(basename, artist)
    path = Path(summaries[0]["dirpath"])
    parent = pretty_folder_name(path.parent.name, artist)
    return f"{parent} — {path.name}" if parent else path.name


def _consensus_album(summaries: list[dict]) -> str:
    """The album name most of the group agrees on.

    Box sets tag every disc with the set's name, so the copy that wins on
    quality is often the one least able to say what the album is: a group of
    `Cowboys From Hell` folders headed "Pantera: The Complete Albums 1990-2000"
    is unreadable. Majority rules, keeper breaks ties."""
    votes = collections.Counter(s["album"] for s in summaries if s.get("album"))
    if not votes:
        return summaries[0]["album"]
    best = max(votes.values())
    winners = [name for name, n in votes.items() if n == best]
    if summaries[0].get("album") in winners:
        return summaries[0]["album"]
    return winners[0]


def _file_view(row: dict, keeper_index: dict,
               reference_duration: float | None = None) -> dict:
    score, reasons = score_file(row, reference_duration)
    match = None
    for key in keys_for(row):
        if key in keeper_index:
            match = keeper_index[key]
            break
    return {
        "path": row["path"],
        "title": row.get("title"),
        "track_number": row.get("track_number"),
        "format": row.get("format"),
        "bitrate_kbps": row.get("bitrate_kbps"),
        "duration_sec": round(row["duration_sec"]) if row.get("duration_sec") else None,
        "filesize_bytes": row.get("filesize_bytes"),
        "score": score,
        "reasons": reasons,
        "matches": match["path"] if match else None,
        "duration_delta": (round(row["duration_sec"] - match["duration_sec"])
                           if match and match.get("duration_sec")
                           and row.get("duration_sec") else None),
    }


def _same_album(a: dict, b: dict) -> bool:
    """Two files on the same album — or, with no album to go on, two loose
    copies sitting side by side in one folder."""
    ia, ib = row_album_identity(a), row_album_identity(b)
    if ia or ib:
        return bool(ia & ib)
    return bool(a.get("dirpath")) and a.get("dirpath") == b.get("dirpath")


def find_track_duplicates(rows: list[dict], covered_dirs: set[str] | None = None,
                          excluded: set[tuple[str, str]] = frozenset()) -> list[dict]:
    """The same track on the same album, more than once: one album in two
    formats, a re-download beside the original, a loose file tagged with an
    album that is also on disk. The same song on a compilation or a single is
    that release's copy, not a duplicate.

    Two files are the same recording only if a title key matches *and* the
    durations agree: `Bob Marley — Keep On Moving` exists at 4:20 and 6:25,
    and those are two different mixes, not a duplicate.
    """
    buckets: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        for key in _artist_buckets(row):
            buckets[key].append(row)

    union = _Union()
    by_path = {r["path"]: r for r in rows}
    for members in buckets.values():
        if len(members) < 2:
            continue
        index: dict[str, list[dict]] = collections.defaultdict(list)
        for row in members:
            for key in keys_for(row):
                index[key].append(row)
        for peers in index.values():
            if len(peers) < 2:
                continue
            for i, a in enumerate(peers):
                for b in peers[i + 1:]:
                    da, dbn = a.get("duration_sec"), b.get("duration_sec")
                    if da and dbn and abs(da - dbn) > DURATION_TOLERANCE:
                        continue
                    if not _same_album(a, b):
                        continue
                    if pair_key(a["path"], b["path"]) in excluded:
                        continue
                    union.union(a["path"], b["path"])

    groups = []
    for paths in union.groups().values():
        if len(paths) < 2:
            continue
        members = [by_path[p] for p in paths]
        durations = [m["duration_sec"] for m in members if m.get("duration_sec")]
        reference = sorted(durations)[len(durations) // 2] if durations else None
        scored = sorted(
            ({**_file_view(m, {}, reference), "dirpath": m.get("dirpath"),
              "album": m.get("album"), "artist": m.get("artist")}
             for m in members),
            key=lambda f: (-f["score"], -(f["filesize_bytes"] or 0)))
        in_album_group = bool(covered_dirs) and all(
            m.get("dirpath") in covered_dirs for m in members)
        groups.append({
            "id": "trk:" + scored[0]["path"],
            "title": members[0].get("title"),
            "artist": (members[0].get("album_artist") or members[0].get("artist")
                       or members[0].get("artist_dir")),
            "copies": scored,
            "duration_spread": (round(max(durations) - min(durations))
                                if len(durations) > 1 else 0),
            "reclaimable_bytes": sum(f["filesize_bytes"] or 0 for f in scored[1:]),
            "in_album_group": in_album_group,
        })
    groups.sort(key=lambda g: -g["reclaimable_bytes"])
    return groups


def find_split_artists(rows: list[dict],
                       excluded: set[tuple[str, str]] = frozenset()) -> list[dict]:
    """Artist folders that are the same artist spelled differently.

    Folder merging alone will not un-split them in Plex — Plex groups on the
    albumartist tag, so the tag has to move with the files.
    """
    by_dir: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        if row.get("artist_dir"):
            by_dir[row["artist_dir"]].append(row)

    buckets: dict[str, list[str]] = collections.defaultdict(list)
    for artist_dir in by_dir:
        buckets[strict_key(artist_dir)].append(artist_dir)

    groups = []
    for key, names in buckets.items():
        if len(names) < 2:
            continue
        # Two spellings the user has separated stay separate. With only a
        # handful of names per bucket, dropping any name excluded against
        # every other is enough — no union-find needed.
        names = [n for n in names
                 if any(pair_key(n, other) not in excluded
                        for other in names if other != n)]
        if len(names) < 2:
            continue
        variants = []
        for name in names:
            files = by_dir[name]
            tags = collections.Counter(
                f["album_artist"] for f in files if f.get("album_artist"))
            variants.append({
                "artist_dir": name,
                "track_count": len(files),
                "album_count": len({f.get("dirpath") for f in files}),
                "bytes": sum(f.get("filesize_bytes") or 0 for f in files),
                "album_artist_tags": [t for t, _ in tags.most_common(3)],
                "tagged_pct": round(100 * sum(tags.values()) / max(1, len(files))),
            })
        # The canonical spelling is the one already carrying the most tagged
        # files — moving the smaller side means rewriting fewer tags.
        variants.sort(key=lambda v: (-v["track_count"], -v["bytes"]))
        groups.append({
            "id": "art:" + key,
            "suggested_keeper": variants[0]["artist_dir"],
            "variants": variants,
        })
    groups.sort(key=lambda g: -sum(v["track_count"] for v in g["variants"]))
    return groups


# ------------------------------------------------------------------ mutation
class LibraryError(Exception):
    pass


def _require_online() -> None:
    from .importer import library_online
    if not library_online():
        raise LibraryError("library sentinel missing — the mount looks offline")


def _safe_abs(relpath: str) -> Path:
    """Resolve a library-relative path, refusing anything that escapes the
    library root or reaches into our own trash. Every mutation goes through
    here: the API takes paths from the browser, and the browser takes them
    from a user."""
    if not relpath or relpath in (".", "/"):
        raise LibraryError("empty path")
    root = settings.library_dir.resolve()
    target = (root / relpath).resolve()
    if target == root or root not in target.parents:
        raise LibraryError(f"path escapes the library root: {relpath!r}")
    if settings.trash_dir.resolve() in (target, *target.parents):
        raise LibraryError(f"path is inside the trash: {relpath!r}")
    return target


def _dir_is_empty_of_audio(directory: Path) -> bool:
    try:
        with os.scandir(directory) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    if not _dir_is_empty_of_audio(Path(entry.path)):
                        return False
                elif Path(entry.name).suffix.lower() in AUDIO_SUFFIXES:
                    return False
    except OSError:
        return False
    return True


async def trash_paths(relpaths: list[str], reason: str = "") -> dict:
    """Move library files into .mdl-trash and forget them in the index.

    A rename inside the same filesystem, so a 40GB album moves instantly and
    the audio is still byte-identical if it needs to come back. Sidecars (.lrc,
    folder art) follow their track, and an album folder emptied of audio goes
    with its leftovers rather than lingering as a stub for Plex to re-find.
    """
    _require_online()
    batch_id = uuid.uuid4().hex[:12]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    batch_root = settings.trash_dir / f"{stamp}-{batch_id}"
    loop = asyncio.get_running_loop()

    entries: list[tuple] = []
    moved_dirs: set[str] = set()
    failures: list[dict] = []
    freed = 0

    # A folder and a file inside it in the same call would race: the folder
    # move takes the file with it, and the file move then fails on a path that
    # no longer exists. Folders first, descendants dropped.
    requested = sorted(dict.fromkeys(relpaths), key=lambda p: len(Path(p).parts))
    kept: list[str] = []
    for relpath in requested:
        parents = {str(p) for p in Path(relpath).parents}
        if parents & set(kept):
            continue
        kept.append(relpath)
    relpaths = kept

    def _move(source: Path, rel: str) -> int:
        destination = batch_root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        size = source.stat().st_size if source.is_file() else 0
        os.replace(source, destination)
        return size

    for relpath in relpaths:
        try:
            absolute = _safe_abs(relpath)
            if not absolute.exists():
                failures.append({"path": relpath, "error": "not found"})
                continue
            kind = "dir" if absolute.is_dir() else "audio"
            size = await loop.run_in_executor(None, _move, absolute, relpath)
            freed += size
            entries.append((batch_id, relpath, str(Path(f"{stamp}-{batch_id}") / relpath),
                            kind, reason, size, utcnow()))
            moved_dirs.add(str(Path(relpath).parent))
            if kind == "audio":
                for suffix in (".lrc", ".txt"):
                    sidecar = absolute.with_suffix(suffix)
                    sidecar_rel = str(Path(relpath).with_suffix(suffix))
                    if sidecar.exists():
                        s = await loop.run_in_executor(None, _move, sidecar, sidecar_rel)
                        entries.append((batch_id, sidecar_rel,
                                        str(Path(f"{stamp}-{batch_id}") / sidecar_rel),
                                        "sidecar", reason, s, utcnow()))
        except (LibraryError, OSError) as exc:
            log.warning("trash failed for %s: %s", relpath, exc)
            failures.append({"path": relpath, "error": str(exc)[:200]})

    # An album folder whose audio has all gone takes its leftovers with it —
    # otherwise Plex keeps a ghost album alive off the cover art. The leftovers
    # move one file at a time: the batch directory already holds this album's
    # path from the audio moves above, so renaming the folder onto it would
    # fail on a non-empty destination.
    emptied = []
    # Ancestors too, deepest first. A box set keeps its discs in subfolders, so
    # emptying `Set/Disc 1` and `Set/Disc 2` leaves `Set` holding nothing but
    # cover.jpg — and Plex will happily go on showing an album for it.
    candidates: set[str] = set()
    for dirrel in moved_dirs:
        if not dirrel or dirrel == ".":
            continue
        current = Path(dirrel)
        while len(current.parts) >= 2:    # never reach a whole artist folder
            candidates.add(str(current))
            current = current.parent
    for dirrel in sorted(candidates, key=lambda d: (-len(Path(d).parts), d)):
        try:
            directory = _safe_abs(dirrel)
        except LibraryError:
            continue
        if not directory.is_dir():
            continue
        if not await loop.run_in_executor(None, _dir_is_empty_of_audio, directory):
            continue
        try:
            leftovers = sorted(f for f in directory.rglob("*") if f.is_file())
            for leftover in leftovers:
                rel = str(leftover.relative_to(settings.library_dir))
                size = await loop.run_in_executor(None, _move, leftover, rel)
                entries.append((batch_id, rel, str(Path(f"{stamp}-{batch_id}") / rel),
                                "sidecar", reason or "emptied album folder",
                                size, utcnow()))
                freed += size
            await loop.run_in_executor(
                None, lambda d=directory: shutil.rmtree(d, ignore_errors=True))
            emptied.append(dirrel)
        except OSError as exc:
            log.warning("could not clear emptied folder %s: %s", dirrel, exc)

    if entries:
        await db.executemany(
            "INSERT INTO library_trash (batch_id, original_path, trash_path, kind, "
            "reason, filesize_bytes, trashed_at) VALUES (?,?,?,?,?,?,?)", entries)
    audio_paths = [e[1] for e in entries if e[3] == "audio"]
    for start in range(0, len(audio_paths), 400):
        batch = audio_paths[start:start + 400]
        await db.execute(
            f"DELETE FROM library_index WHERE path IN ({','.join('?' * len(batch))})",
            tuple(batch))
    for dirrel in emptied:
        await db.execute("DELETE FROM library_index WHERE dirpath = ?", (dirrel,))

    artist_dirs = {Path(e[1]).parts[0] for e in entries if Path(e[1]).parts}
    await _refresh_plex(artist_dirs)
    log.info("trashed %d entries (%.1f MB) as batch %s", len(entries), freed / 1e6, batch_id)
    return {"batch_id": batch_id, "trashed": len(entries), "freed_bytes": freed,
            "emptied_folders": emptied, "failures": failures}


async def _refresh_plex(relpaths) -> None:
    from .plex import plex
    for relpath in list(relpaths)[:20]:
        try:
            await plex.refresh_path(relpath)
        except Exception as exc:
            log.debug("plex refresh skipped for %s: %s", relpath, exc)


async def list_trash(limit: int = 200, include_restored: bool = False) -> dict:
    sql = ("SELECT batch_id, reason, COUNT(*) AS entries, "
           "SUM(filesize_bytes) AS bytes, MIN(trashed_at) AS trashed_at, "
           "SUM(restored_at IS NOT NULL) AS restored "
           "FROM library_trash ")
    if not include_restored:
        sql += "WHERE restored_at IS NULL "
    sql += "GROUP BY batch_id ORDER BY trashed_at DESC LIMIT ?"
    batches = [dict(r) for r in await db.fetchall(sql, (limit,))]
    total = await db.fetchone(
        "SELECT COUNT(*) AS n, COALESCE(SUM(filesize_bytes), 0) AS bytes "
        "FROM library_trash WHERE restored_at IS NULL")
    return {"batches": batches, "entries": total["n"], "bytes": total["bytes"]}


async def restore_batch(batch_id: str) -> dict:
    """Put a trashed batch back exactly where it came from."""
    _require_online()
    rows = await db.fetchall(
        "SELECT * FROM library_trash WHERE batch_id = ? AND restored_at IS NULL",
        (batch_id,))
    if not rows:
        raise LibraryError("nothing to restore in that batch")
    loop = asyncio.get_running_loop()
    restored, failures = [], []
    # Directories first: a file's parent has to exist before the file lands.
    ordered = sorted(rows, key=lambda r: (r["kind"] != "dir", len(r["original_path"])))
    for row in ordered:
        source = settings.trash_dir / row["trash_path"]
        try:
            destination = _safe_abs(row["original_path"])
            if not source.exists():
                failures.append({"path": row["original_path"], "error": "missing from trash"})
                continue
            if destination.exists():
                failures.append({"path": row["original_path"],
                                 "error": "something is already at that path"})
                continue
            await loop.run_in_executor(None, _relocate, source, destination)
            await db.execute("UPDATE library_trash SET restored_at = ? WHERE id = ?",
                             (utcnow(), row["id"]))
            restored.append(row["original_path"])
        except (LibraryError, OSError) as exc:
            failures.append({"path": row["original_path"], "error": str(exc)[:200]})
    await _refresh_plex({Path(p).parts[0] for p in restored if Path(p).parts})
    log.info("restored %d entries from batch %s", len(restored), batch_id)
    return {"restored": len(restored), "failures": failures,
            "rescan_needed": bool(restored)}


async def purge_trash(batch_id: str | None = None, older_than_days: int | None = None) -> dict:
    """The only code in this application that unlinks a library file."""
    clauses, params = ["restored_at IS NULL"], []
    if batch_id:
        clauses.append("batch_id = ?")
        params.append(batch_id)
    if older_than_days is not None:
        cutoff = time.time() - older_than_days * 86400
        clauses.append("trashed_at < ?")
        params.append(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff)))
    rows = await db.fetchall(
        f"SELECT * FROM library_trash WHERE {' AND '.join(clauses)}", tuple(params))
    if not rows:
        return {"purged": 0, "freed_bytes": 0}
    loop = asyncio.get_running_loop()
    freed = 0
    batches = {r["batch_id"] for r in rows}
    for row in rows:
        target = settings.trash_dir / row["trash_path"]
        try:
            if target.is_dir():
                await loop.run_in_executor(None, lambda t=target: shutil.rmtree(t, ignore_errors=True))
            elif target.exists():
                await loop.run_in_executor(None, target.unlink)
            freed += row["filesize_bytes"] or 0
        except OSError as exc:
            log.warning("purge failed for %s: %s", row["trash_path"], exc)
        await db.execute("DELETE FROM library_trash WHERE id = ?", (row["id"],))
    # drop now-empty batch directories
    for batch in batches:
        for directory in settings.trash_dir.glob(f"*-{batch}"):
            try:
                await loop.run_in_executor(
                    None, lambda d=directory: shutil.rmtree(d, ignore_errors=True))
            except OSError:
                pass
    log.info("purged %d trash entries (%.1f MB)", len(rows), freed / 1e6)
    return {"purged": len(rows), "freed_bytes": freed}


def _relocate(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)


async def merge_artist_folders(sources: list[str], target: str,
                               rewrite_tags: bool = True) -> dict:
    """Fold `sources` artist folders into `target`.

    Plex splits artists on the albumartist *tag*, not the folder, so moving
    folders without rewriting the tag just relocates the problem. The artist
    tag is only rewritten where it equals the old folder name — a featured
    guest's name on a track must survive the merge."""
    _require_online()
    loop = asyncio.get_running_loop()
    target_dir = _safe_abs(target)
    if not target_dir.is_dir():
        raise LibraryError(f"target artist folder does not exist: {target!r}")

    moved, merged_into, retagged, failures = [], [], 0, []
    leftovers: list[str] = []
    for source in sources:
        if source == target:
            continue
        source_dir = _safe_abs(source)
        if not source_dir.is_dir():
            failures.append({"path": source, "error": "not a folder"})
            continue
        for child in sorted(source_dir.iterdir()):
            destination = target_dir / child.name
            try:
                if destination.exists() and child.is_dir():
                    # Same album folder name on both sides — merge file by file.
                    for inner in sorted(child.rglob("*")):
                        if inner.is_dir():
                            continue
                        final = destination / inner.relative_to(child)
                        if final.exists():
                            continue   # a copy is already there; deal with it below
                        await loop.run_in_executor(None, _relocate, inner, final)
                        moved.append(str(final.relative_to(settings.library_dir)))
                    merged_into.append(child.name)
                    # Whatever could not move is a file the target already has.
                    # It still goes through the trash — this module does not
                    # delete, and "it looked like a duplicate" is exactly the
                    # judgement the duplicate screens exist to make explicit.
                    if any(f.is_file() for f in child.rglob("*")):
                        leftovers.append(str(child.relative_to(settings.library_dir)))
                    else:
                        await loop.run_in_executor(
                            None, lambda c=child: shutil.rmtree(c, ignore_errors=True))
                elif destination.exists():
                    failures.append({"path": child.name,
                                     "error": "a file of that name already exists in the target"})
                else:
                    await loop.run_in_executor(None, os.replace, child, destination)
                    moved.append(str(destination.relative_to(settings.library_dir)))
            except OSError as exc:
                failures.append({"path": child.name, "error": str(exc)[:200]})
        await db.execute("DELETE FROM library_index WHERE artist_dir = ?", (source,))

    trashed = {}
    if leftovers:
        trashed = await trash_paths(
            leftovers, f"already present in {target} after an artist merge")

    # Only now can the source folders go, and only if they really are empty.
    for source in sources:
        if source == target:
            continue
        try:
            _safe_abs(source).rmdir()
        except OSError as exc:
            if _safe_abs(source).is_dir():
                failures.append({"path": source,
                                 "error": f"folder not empty, left in place ({exc})"})

    if rewrite_tags:
        retagged = await _retag_album_artist(target, sources)
    await _refresh_plex({target})
    log.info("merged %s into %s: %d items moved, %d retagged",
             sources, target, len(moved), retagged)
    return {"moved": len(moved), "merged_albums": merged_into,
            "retagged": retagged, "trashed": trashed.get("trashed", 0),
            "failures": failures, "rescan_needed": True}


async def _retag_album_artist(target: str, old_names: list[str]) -> int:
    """Set albumartist to the canonical folder name across the merged folder."""
    import mutagen

    loop = asyncio.get_running_loop()
    old_keys = {strict_key(n) for n in old_names}
    target_dir = _safe_abs(target)

    def _rewrite() -> int:
        changed = 0
        for path in target_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            try:
                audio = mutagen.File(path, easy=True)
                if audio is None:
                    continue
                current_aa = (audio.get("albumartist") or [None])[0]
                current_artist = (audio.get("artist") or [None])[0]
                dirty = False
                if not current_aa or strict_key(current_aa) in old_keys:
                    audio["albumartist"] = [target]
                    dirty = True
                if current_artist and strict_key(current_artist) in old_keys:
                    audio["artist"] = [target]
                    dirty = True
                if dirty:
                    audio.save()
                    changed += 1
            except Exception as exc:
                log.debug("retag skipped for %s: %s", path, exc)
        return changed

    return await loop.run_in_executor(None, _rewrite)


# ------------------------------------------------------------------ browsing
_ROW_FIELDS = ("path, title, artist, album, album_artist, track_number, "
               "disc_number, year, format, bitrate_kbps, sample_rate, bit_depth, "
               "duration_sec, filesize_bytes, dirpath, artist_dir")


async def _all_rows() -> list[dict]:
    """Rows the duplicate analysis can reason about. A file whose tags would
    not parse has no title to match on and no bitrate to judge — guessing from
    its filename would invent duplicates, so it is left out."""
    return [dict(r) for r in await db.fetchall(
        f"SELECT {_ROW_FIELDS} FROM library_index WHERE unreadable = 0")]


async def overview() -> dict:
    row = await db.fetchone(
        "SELECT COUNT(*) AS files, COUNT(DISTINCT artist_dir) AS artists, "
        "COUNT(DISTINCT dirpath) AS albums, COALESCE(SUM(filesize_bytes), 0) AS bytes, "
        "COALESCE(SUM(duration_sec), 0) AS seconds, "
        "SUM(album_artist IS NULL) AS no_album_artist, "
        "SUM(album IS NULL) AS no_album, SUM(unreadable) AS unreadable, "
        "SUM(scanned_at IS NULL AND unreadable = 0) AS unenriched "
        "FROM library_index")
    formats = {r["format"]: r["n"] for r in await db.fetchall(
        "SELECT format, COUNT(*) AS n FROM library_index GROUP BY format ORDER BY n DESC")}
    trash = await db.fetchone(
        "SELECT COUNT(*) AS n, COALESCE(SUM(filesize_bytes), 0) AS bytes "
        "FROM library_trash WHERE restored_at IS NULL")
    lossless = sum(n for f, n in formats.items() if (f or "") in LOSSLESS_FORMATS)
    excl = await db.fetchone("SELECT COUNT(*) AS n FROM library_exclusions")
    return {**dict(row), "formats": formats, "lossless_files": lossless,
            "trash_entries": trash["n"], "trash_bytes": trash["bytes"],
            "exclusion_pairs": excl["n"], "scan": scan_state.snapshot()}


async def list_artists(q: str | None = None, sort: str = "name",
                       limit: int = 500, offset: int = 0) -> dict:
    rows = await db.fetchall(
        "SELECT artist_dir, COUNT(*) AS tracks, COUNT(DISTINCT dirpath) AS albums, "
        "COALESCE(SUM(filesize_bytes), 0) AS bytes "
        "FROM library_index WHERE artist_dir IS NOT NULL AND artist_dir != '' "
        "GROUP BY artist_dir")
    items = [dict(r) for r in rows]
    if q:
        needle = strict_key(q)
        items = [i for i in items if needle in strict_key(i["artist_dir"])]
    keys = {"name": lambda i: strict_key(i["artist_dir"]),
            "tracks": lambda i: -i["tracks"],
            "albums": lambda i: -i["albums"],
            "size": lambda i: -i["bytes"]}
    items.sort(key=keys.get(sort, keys["name"]))
    return {"total": len(items), "items": items[offset:offset + limit]}


async def list_albums(artist_dir: str) -> dict:
    rows = [dict(r) for r in await db.fetchall(
        "SELECT path, title, artist, album, album_artist, track_number, disc_number, "
        "year, format, bitrate_kbps, sample_rate, bit_depth, duration_sec, "
        "filesize_bytes, dirpath, artist_dir FROM library_index WHERE artist_dir = ?",
        (artist_dir,))]
    if not rows:
        raise LibraryError(f"no indexed files under {artist_dir!r}")
    instances = build_album_instances(rows)
    albums = sorted((i.summary() for i in instances.values()),
                    key=lambda a: (a["year"] or "9999", a["album"].casefold()))
    return {"artist_dir": artist_dir, "albums": albums,
            "tracks": len(rows),
            "bytes": sum(r.get("filesize_bytes") or 0 for r in rows)}


async def list_album_tracks(dirpath: str) -> dict:
    rows = [dict(r) for r in await db.fetchall(
        "SELECT path, title, artist, album, album_artist, track_number, disc_number, "
        "year, format, bitrate_kbps, sample_rate, bit_depth, duration_sec, "
        "filesize_bytes, dirpath, artist_dir FROM library_index WHERE dirpath = ? "
        "ORDER BY disc_number, track_number, path", (dirpath,))]
    if not rows:
        raise LibraryError(f"no indexed files in {dirpath!r}")
    instance = build_album_instances(rows)[dirpath]
    return {**instance.summary(), "tracks": [_file_view(r, {}) for r in rows]}


_analysis: dict = {"token": None, "albums": None, "tracks": None, "artists": None}
_analysis_lock = asyncio.Lock()


async def _analysis_token() -> tuple:
    """Cheap fingerprint of the indexed library plus its trash. Any scan,
    removal or restore moves it, and nothing else needs to."""
    row = await db.fetchone(
        "SELECT COUNT(*) AS n, COALESCE(MAX(scanned_at), '') AS last FROM library_index")
    trash = await db.fetchone("SELECT COUNT(*) AS n FROM library_trash")
    excl = await db.fetchone(
        "SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS last FROM library_exclusions")
    return (row["n"], row["last"], trash["n"], excl["n"], excl["last"])


async def _analyse() -> dict:
    """Run the duplicate analysis, reusing the last result when nothing moved.

    61k rows of regex and set work is a few seconds of straight CPU — long
    enough to stall the SSE stream and the download worker if it ran on the
    event loop, and wasteful to repeat for every tab switch."""
    token = await _analysis_token()
    if _analysis["token"] == token and _analysis["albums"] is not None:
        return _analysis
    # Opening the Library tab fires three of these at once; without the lock
    # each would pay the full analysis instead of two of them waiting on it.
    async with _analysis_lock:
        token = await _analysis_token()
        if _analysis["token"] == token and _analysis["albums"] is not None:
            return _analysis
        rows = await _all_rows()
        exclusions = await load_exclusions()

        def compute() -> dict:
            albums = find_album_duplicates(rows, exclusions["album"])
            covered = {i["dirpath"] for g in albums for i in g["instances"]}
            return {"token": token, "albums": albums,
                    "tracks": find_track_duplicates(rows, covered,
                                                    exclusions["track"]),
                    "artists": find_split_artists(rows, exclusions["artist"])}

        started = time.monotonic()
        result = await asyncio.get_running_loop().run_in_executor(None, compute)
        _analysis.update(result)
        log.info("duplicate analysis in %.1fs: %d album groups, %d track groups, "
                 "%d split artists", time.monotonic() - started,
                 len(result["albums"]), len(result["tracks"]), len(result["artists"]))
        return _analysis


async def duplicates(kind: str, limit: int = 100, offset: int = 0,
                     confident_only: bool = False,
                     include_covered: bool = False) -> dict:
    """Duplicate report. Album groups are computed first so track groups can
    say which of them are already explained by an album group — resolving
    albums is the bulk action, and the track list is what is left over."""
    analysis = await _analyse()
    if kind == "artists":
        groups = analysis["artists"]
        return {"total": len(groups), "items": groups[offset:offset + limit]}

    if kind == "albums":
        items = analysis["albums"]
        if confident_only:
            items = [g for g in items if g["kind"] == "exact" and not g["has_orphans"]]
        return {"total": len(items),
                "reclaimable_bytes": sum(g["reclaimable_bytes"] for g in items),
                "items": items[offset:offset + limit]}

    groups = analysis["tracks"]
    if not include_covered:
        groups = [g for g in groups if not g["in_album_group"]]
    if confident_only:
        groups = [g for g in groups if g["duration_spread"] <= DURATION_TOLERANCE]
    return {"total": len(groups),
            "reclaimable_bytes": sum(g["reclaimable_bytes"] for g in groups),
            "items": groups[offset:offset + limit]}


async def plan_album_merge(dirpaths: list[str], keepers: list[str]) -> dict:
    """Which files would go if `keepers` were the copies kept.

    More than one copy may be kept: three folders that share tracks are often
    three real compilations, and the honest answer is sometimes "keep two of
    these three". A track is redundant when it appears in *any* kept copy.

    Re-deriving the split here rather than in the browser keeps one
    implementation of "is this the same track" — a JS reimplementation of
    title_keys would drift on exactly the junk-tagged files it exists for.

    The caller's order is preserved. Sorting keepers to the top made the row
    you just clicked jump to position 0, which reads as the choice snapping
    back to the first option.
    """
    keepers = [k for k in dict.fromkeys(keepers)]
    if not keepers:
        raise LibraryError("keep at least one copy")
    unknown = [k for k in keepers if k not in dirpaths]
    if unknown:
        raise LibraryError(f"not in this group: {unknown[0]!r}")

    placeholders = ",".join("?" * len(dirpaths))
    rows = [dict(r) for r in await db.fetchall(
        f"SELECT {_ROW_FIELDS} FROM library_index "
        f"WHERE unreadable = 0 AND dirpath IN ({placeholders})", tuple(dirpaths))]
    instances = build_album_instances(rows)
    missing = [k for k in keepers if k not in instances]
    if missing:
        raise LibraryError(f"no indexed files in {missing[0]!r}")

    keeper_index: dict[str, dict] = {}
    for keeper in keepers:
        for key, file in instances[keeper].key_index().items():
            keeper_index.setdefault(key, file)

    out = []
    for dirpath in dirpaths:          # caller's order, not ours
        inst = instances.get(dirpath)
        if inst is None:
            continue
        summary = inst.summary()
        if dirpath in keepers:
            summary["is_keeper"] = True
            summary["files"] = [_file_view(f, {}) for f in inst.files]
        else:
            covered, orphans = [], []
            for f in inst.files:
                (covered if any(k in keeper_index for k in keys_for(f))
                 else orphans).append(f)
            summary["covered"] = [_file_view(f, keeper_index) for f in covered]
            summary["orphans"] = [_file_view(f, keeper_index) for f in orphans]
        out.append(summary)
    return {
        "keepers": keepers,
        "instances": out,
        "reclaimable_bytes": sum(f["filesize_bytes"] or 0 for s in out
                                 for f in s.get("covered", [])),
    }
