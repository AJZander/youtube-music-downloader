"""A local mirror of the Plex music section, joined to our files by path.

List views are cheap (a few dozen paged requests for 60k tracks) and carry
almost everything: guid, MusicBrainz ids, titles, numbers, file paths and
genres. Moods, styles, countries and locked fields only come from an item's
own page, so those are read for artists and albums that are new or changed
since they were last read.

Incremental syncs fetch only what was added or updated since the last one.
Plex has no "deleted since" filter, so when the section's counts stop
matching the mirror's — or every few hours anyway — the listing is complete
and anything not seen is dropped.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import unicodedata
from collections import Counter

from ..db import db
from ..plex import ALBUM, ARTIST, TRACK, PlexError, mbid_of, plex, tag_values
from .vocab import vocab

log = logging.getLogger("mdl.plexsync")

FULL_EVERY = 6 * 3600
TYPE_NAME = {ARTIST: "artist", ALBUM: "album", TRACK: "track"}

_UPSERT = """
INSERT INTO plex_items (rating_key, type, guid, mbid, title, parent_key, grandparent_key,
  parent_title, grandparent_title, idx, parent_idx, year, studio, path, folder, genres,
  thumb, added_at, updated_at, seen_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(rating_key) DO UPDATE SET
  type=excluded.type, guid=excluded.guid, mbid=excluded.mbid, title=excluded.title,
  parent_key=excluded.parent_key, grandparent_key=excluded.grandparent_key,
  parent_title=excluded.parent_title, grandparent_title=excluded.grandparent_title,
  idx=excluded.idx, parent_idx=excluded.parent_idx, year=excluded.year,
  studio=excluded.studio, path=excluded.path,
  folder=COALESCE(excluded.folder, plex_items.folder),
  genres=excluded.genres, thumb=excluded.thumb, added_at=excluded.added_at,
  updated_at=excluded.updated_at, seen_at=excluded.seen_at,
  -- an item whose page changed must be read again
  detail_at=CASE WHEN COALESCE(excluded.updated_at, 0) > COALESCE(plex_items.detail_at, 0)
                 THEN NULL ELSE plex_items.detail_at END
"""


def _row(item: dict, type_num: int, now: float) -> tuple:
    path = folder = None
    if type_num == TRACK:
        for media in item.get("Media") or []:
            for part in media.get("Part") or []:
                path = plex.from_plex_path(part.get("file") or "")
                if path:
                    break
            if path:
                break
        if path:
            folder = path.rsplit("/", 1)[0] if "/" in path else ""
    genres = tag_values(item, "Genre")
    return (
        str(item.get("ratingKey")), TYPE_NAME[type_num], item.get("guid"), mbid_of(item),
        item.get("title"),
        str(item["parentRatingKey"]) if item.get("parentRatingKey") else None,
        str(item["grandparentRatingKey"]) if item.get("grandparentRatingKey") else None,
        item.get("parentTitle"), item.get("grandparentTitle"),
        item.get("index"), item.get("parentIndex"),
        item.get("year") or item.get("parentYear"), item.get("studio") or item.get("parentStudio"),
        path, folder, json.dumps(genres) if genres else None,
        item.get("thumb"), item.get("addedAt"), item.get("updatedAt"), now,
    )


async def _state(key: str) -> str | None:
    row = await db.fetchone("SELECT value FROM md_state WHERE key = ?", (key,))
    return row["value"] if row else None


async def _set_state(key: str, value) -> None:
    await db.execute(
        "INSERT INTO md_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value if isinstance(value, str) else json.dumps(value)))


async def last_sync() -> float:
    value = await _state("plex_last_sync")
    return float(value) if value else 0.0


async def sync(*, full: bool = False, progress=None) -> dict:
    """Bring plex_items up to date. Returns counts."""
    if not plex.configured:
        return {"ok": False, "error": "Plex is not configured"}
    started = time.time()
    previous = await last_sync()
    last_full = float(await _state("plex_last_full") or 0)
    counts = {t: await plex.count(t) for t in (ARTIST, ALBUM, TRACK)}
    mirrored = {r["type"]: r["n"] for r in await db.fetchall(
        "SELECT type, COUNT(*) AS n FROM plex_items GROUP BY type")}
    drifted = any(counts[t] != mirrored.get(TYPE_NAME[t], 0) for t in counts)
    full = full or drifted or not previous or started - last_full > FULL_EVERY

    seen = 0
    for type_num in (ARTIST, ALBUM, TRACK):
        rows: list[tuple] = []
        if full:
            sources = [plex.iter_items(type_num)]
        else:
            since = int(previous) - 120
            sources = [plex.iter_items(type_num, **{"updatedAt>>": since}),
                       plex.iter_items(type_num, **{"addedAt>>": since})]
        for source in sources:
            async for item in source:
                rows.append(_row(item, type_num, started))
                if len(rows) >= 2000:
                    await db.executemany(_UPSERT, rows)
                    seen += len(rows)
                    rows = []
                    if progress:
                        progress(f"Plex {TYPE_NAME[type_num]}s", seen)
        if rows:
            await db.executemany(_UPSERT, rows)
            seen += len(rows)
    removed = 0
    if full:
        row = await db.fetchone("SELECT COUNT(*) AS n FROM plex_items WHERE seen_at < ?",
                                (started,))
        removed = row["n"]
        await db.execute("DELETE FROM plex_items WHERE seen_at < ?", (started,))
        await _set_state("plex_last_full", str(started))

    # An album's folder: where most of its tracks are.
    await db.execute("""
        UPDATE plex_items SET folder = (
          SELECT t.folder FROM plex_items t WHERE t.parent_key = plex_items.rating_key
          AND t.type = 'track' GROUP BY t.folder ORDER BY COUNT(*) DESC LIMIT 1)
        WHERE type = 'album'""")
    details = await read_details(progress=progress)
    await _harvest_moods()
    await _map_artists()
    await _set_state("plex_last_sync", str(started))
    prefs = {}
    try:
        prefs = await plex.section_prefs()
        await _set_state("plex_prefs", prefs)
    except PlexError:
        pass
    result = {"ok": True, "full": full, "items": seen, "removed": removed,
              "details": details, "seconds": round(time.time() - started, 1)}
    log.info("plex sync: %s", result)
    return result


async def read_details(keys: list[str] | None = None, progress=None) -> int:
    """Read the item pages (moods, styles, countries, locks) of artists and
    albums that changed since they were last read, or of `keys`."""
    if keys is None:
        rows = await db.fetchall(
            "SELECT rating_key FROM plex_items WHERE type IN ('artist', 'album') "
            "AND detail_at IS NULL")
        keys = [r["rating_key"] for r in rows]
    done = 0

    async def one(key: str) -> bool:
        try:
            item = await plex.metadata(key)
        except PlexError as exc:
            log.debug("plex detail %s failed: %s", key, exc)
            return False
        if item is None:
            await db.execute("DELETE FROM plex_items WHERE rating_key = ?", (key,))
            return False
        await store_detail(item)
        return True

    # The client allows six requests in flight; feed it in batches.
    for i in range(0, len(keys), 24):
        results = await asyncio.gather(*(one(k) for k in keys[i:i + 24]))
        done += sum(results)
        if progress and (i // 24) % 10 == 0:
            progress("Plex details", done)
    return done


async def store_detail(item: dict) -> None:
    locked = [f.get("name") for f in item.get("Field") or [] if f.get("locked")]

    def tags(key):
        values = tag_values(item, key)
        return json.dumps(values) if values else None

    await db.execute(
        "UPDATE plex_items SET guid = ?, mbid = ?, title = ?, genres = ?, styles = ?, "
        "moods = ?, countries = ?, locked = ?, summary = ?, studio = ?, thumb = ?, "
        "year = COALESCE(?, year), updated_at = ?, detail_at = ? WHERE rating_key = ?",
        (item.get("guid"), mbid_of(item), item.get("title"), tags("Genre"), tags("Style"),
         tags("Mood"), tags("Country"), json.dumps(locked) if locked else None,
         (item.get("summary") or "")[:2000] or None, item.get("studio"), item.get("thumb"),
         item.get("year"), item.get("updatedAt"),
         int(item.get("updatedAt") or time.time()), str(item.get("ratingKey"))))


async def refresh_items(keys: list[str]) -> None:
    """Re-read specific items (after an edit) — list fields and detail."""
    for key in keys:
        try:
            item = await plex.metadata(key)
        except PlexError:
            continue
        if item is None:
            await db.execute("DELETE FROM plex_items WHERE rating_key = ?", (key,))
            continue
        type_num = {"artist": ARTIST, "album": ALBUM, "track": TRACK}.get(item.get("type"))
        if type_num is None:
            continue
        await db.execute(_UPSERT, _row(item, type_num, time.time()))
        if type_num in (ARTIST, ALBUM):
            await store_detail(item)


async def sync_recent(since: float) -> int:
    """Pick up what a scan just added or changed — cheaper than a sync."""
    n = 0
    now = time.time()
    for type_num in (ARTIST, ALBUM, TRACK):
        for flt in ("updatedAt>>", "addedAt>>"):
            rows = [_row(item, type_num, now) async for item in
                    plex.iter_items(type_num, **{flt: int(since) - 5})]
            if rows:
                await db.executemany(_UPSERT, rows)
                n += len(rows)
    await db.execute("""
        UPDATE plex_items SET folder = (
          SELECT t.folder FROM plex_items t WHERE t.parent_key = plex_items.rating_key
          AND t.type = 'track' GROUP BY t.folder ORDER BY COUNT(*) DESC LIMIT 1)
        WHERE type = 'album' AND (folder IS NULL OR updated_at >= ? OR added_at >= ?)""",
                     (int(since), int(since)))
    await read_details()
    return n


def spellings(paths: list[str]) -> dict[str, str]:
    """Each path as Plex may report it -> our spelling. The share hands back
    names as they were written ("a" + combining accent, from a Mac), Plex
    reports them composed ("à"): the same file."""
    out = {p: p for p in paths}
    for p in paths:
        out.setdefault(unicodedata.normalize("NFC", p), p)
    return out


async def keys_for_paths(paths: list[str]) -> list[str]:
    """The Plex tracks at these paths, and their albums."""
    keys = list(spellings(paths))
    out: list[str] = []
    for i in range(0, len(keys), 400):
        chunk = keys[i:i + 400]
        for r in await db.fetchall(
                "SELECT rating_key, parent_key FROM plex_items WHERE type = 'track' AND path IN "
                f"({','.join('?' * len(chunk))})", tuple(chunk)):
            out += [r["rating_key"], r["parent_key"]]
    return list(dict.fromkeys(k for k in out if k))


async def forget_paths(paths: list[str]) -> None:
    """Tracks Plex will drop at its next scan (their files moved or went)."""
    paths = list(spellings(paths))
    for i in range(0, len(paths), 400):
        chunk = paths[i:i + 400]
        await db.execute(
            f"DELETE FROM plex_items WHERE type = 'track' AND path IN ({','.join('?' * len(chunk))})",
            tuple(chunk))


async def _harvest_moods() -> None:
    try:
        moods = set(await plex.vocab("mood", ALBUM)) | set(await plex.vocab("mood", ARTIST))
    except PlexError:
        return
    if moods:
        await _set_state("plex_moods", sorted(moods))
        vocab.set_moods(sorted(moods))


async def _map_artists() -> None:
    """artist_dir -> the Plex artist most of its files are filed under."""
    rows = await db.fetchall(
        "SELECT substr(path, 1, instr(path, '/') - 1) AS top, grandparent_key AS key, "
        "COUNT(*) AS n FROM plex_items WHERE type = 'track' AND instr(path, '/') > 0 "
        "GROUP BY top, key")
    best: dict[str, Counter] = {}
    for r in rows:
        best.setdefault(r["top"], Counter())[r["key"]] += r["n"]
    pairs = [(top, c.most_common(1)[0][0]) for top, c in best.items() if c]
    await db.executemany(
        "INSERT INTO md_artists (artist_dir, plex_key) VALUES (?, ?) "
        "ON CONFLICT(artist_dir) DO UPDATE SET plex_key = excluded.plex_key", pairs)


# ------------------------------------------------------------------ reads
async def album_for_paths(paths: list[str]) -> dict | None:
    """The Plex album most of these files belong to, with its tracks."""
    if not paths:
        return None
    counter: Counter = Counter()
    tracks: dict[str, dict] = {}       # by our path
    ours = spellings(paths)
    keys = list(ours)
    for i in range(0, len(keys), 400):
        chunk = keys[i:i + 400]
        for r in await db.fetchall(
                "SELECT * FROM plex_items WHERE type = 'track' AND path IN "
                f"({','.join('?' * len(chunk))})", tuple(chunk)):
            row = dict(r)
            tracks[ours[row["path"]]] = row
            if row["parent_key"]:
                counter[row["parent_key"]] += 1
    if not counter:
        return {"album": None, "artist": None, "tracks": tracks}
    album_key = counter.most_common(1)[0][0]
    album = await db.fetchone("SELECT * FROM plex_items WHERE rating_key = ?", (album_key,))
    artist = None
    if album and album["parent_key"]:
        artist = await db.fetchone("SELECT * FROM plex_items WHERE rating_key = ?",
                                   (album["parent_key"],))
    return {"album": dict(album) if album else None,
            "artist": dict(artist) if artist else None,
            "tracks": tracks, "album_keys": list(counter)}


def jlist(value) -> list:
    if not value:
        return []
    try:
        out = json.loads(value)
        return out if isinstance(out, list) else []
    except (TypeError, ValueError):
        return []
