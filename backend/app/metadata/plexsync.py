"""A local mirror of the Plex music section, joined to our files by path.

List views are cheap (a few dozen paged requests for 60k tracks) and carry
almost everything: guid, MusicBrainz ids, titles, numbers and file paths.
But they carry at most two genres an item, and moods, styles, countries and
locked fields only come from an item's own page, so the pages of items that
are new or changed since they were last read are read too (a hundred to a
request), and a page's genres stand until the item changes again.

Incremental syncs fetch only what was added or updated since the last one.
Plex has no "deleted since" filter, so when the section's counts stop
matching the mirror's — or every few hours anyway — the listing is complete
and anything not seen is dropped, but only when the listing held as many
items as Plex counted, and Plex, asked for it by key, says it is gone.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import unicodedata
from collections import Counter

from ..db import db
from ..plex import ALBUM, ARTIST, TRACK, PlexError, mbid_of, nfc, plex, tag_values
from .vocab import vocab

log = logging.getLogger("mdl.plexsync")

FULL_EVERY = 6 * 3600
TYPE_NAME = {ARTIST: "artist", ALBUM: "album", TRACK: "track"}
PAGE_BATCH = 100      # item pages to a request
# 2: tracks' pages are read too, and a list view no longer cuts an item's
# genres to two: every page is read again once
# 3: tracks edited with their album were never read back (an edit leaves
# Plex's updatedAt alone), so the mirror kept their old genres: again once
DETAIL_REV = "3"

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
  -- a list view's genres are cut to two: an unchanged item keeps its page's
  genres=CASE WHEN plex_items.detail_at IS NOT NULL
                   AND COALESCE(excluded.updated_at, 0) <= plex_items.detail_at
              THEN plex_items.genres ELSE excluded.genres END,
  thumb=excluded.thumb, added_at=excluded.added_at,
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
    listed: dict[int, set[str]] = {}       # the distinct items each type's listing held
    for type_num in (ARTIST, ALBUM, TRACK):
        rows: list[tuple] = []
        keys = listed.setdefault(type_num, set())
        if full:
            sources = [plex.iter_items(type_num)]
        else:
            since = int(previous) - 120
            sources = [plex.iter_items(type_num, **{"updatedAt>>": since}),
                       plex.iter_items(type_num, **{"addedAt>>": since})]
        for source in sources:
            async for item in source:
                rows.append(_row(item, type_num, started))
                keys.add(rows[-1][0])
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
        # What a listing didn't show is gone from Plex only if the listing
        # was whole: one that came up short (items added or removed mid-
        # sync shift the pages) would drop rows Plex still has. A count
        # Plex didn't give (None) is never reached.
        short = {t: len(listed[t]) for t in listed
                 if counts[t] is None or len(listed[t]) < counts[t]}
        missed: dict[int, int] = {}
        for type_num in listed:
            if type_num in short:
                continue
            # A delete and an add mid-sync keep the count yet shift the
            # pages past one item: each unseen row is asked for by key, and
            # only what Plex says is gone is dropped.
            name = TYPE_NAME[type_num]
            unseen = [r["rating_key"] for r in await db.fetchall(
                "SELECT rating_key FROM plex_items WHERE type = ? AND seen_at < ?",
                (name, started))]
            if not unseen:
                continue
            count = "SELECT COUNT(*) AS n FROM plex_items WHERE type = ?"
            before = (await db.fetchone(count, (name,)))["n"]
            await refresh_items(unseen)
            gone = before - (await db.fetchone(count, (name,)))["n"]
            removed += gone
            if len(unseen) > gone:
                missed[type_num] = len(unseen) - gone
        if short:
            log.warning("plex sync: the listing came up short (%s); unseen rows kept",
                        ", ".join(f"{TYPE_NAME[t]}s {n} of {'?' if counts[t] is None else counts[t]}"
                                  for t, n in short.items()))
        if missed:
            log.warning("plex sync: the listing missed %s Plex still has (or couldn't say); "
                        "rows kept", ", ".join(f"{n} {TYPE_NAME[t]}s" for t, n in missed.items()))
        if not short and not missed:
            await _set_state("plex_last_full", str(started))

    # An album's folder: where most of its tracks are.
    await db.execute("""
        UPDATE plex_items SET folder = (
          SELECT t.folder FROM plex_items t WHERE t.parent_key = plex_items.rating_key
          AND t.type = 'track' GROUP BY t.folder ORDER BY COUNT(*) DESC LIMIT 1)
        WHERE type = 'album'""")
    if await _state("plex_detail_rev") != DETAIL_REV:
        await db.execute("UPDATE plex_items SET detail_at = NULL")
        await _set_state("plex_detail_rev", DETAIL_REV)
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


async def _pages(keys: list[str]) -> list[dict] | None:
    """The pages of these items, in one request; an item left out of the
    answer is asked for alone, and dropped from the mirror if Plex has
    deleted it. None when Plex can't be asked."""
    try:
        items = await plex.metadata_many(keys)
    except PlexError as exc:
        log.debug("plex pages failed: %s", exc)
        return None
    found = {str(i.get("ratingKey")) for i in items}
    for key in keys:
        if key in found:
            continue
        try:
            item = await plex.metadata(key)
        except PlexError:
            continue
        if item is None:
            await db.execute("DELETE FROM plex_items WHERE rating_key = ?", (key,))
        else:
            items.append(item)
    return items


async def read_details(keys: list[str] | None = None, progress=None) -> int:
    """Read the item pages (every genre, moods, styles, countries, locks) of
    items that changed since they were last read, or of `keys`."""
    if keys is None:
        rows = await db.fetchall("SELECT rating_key FROM plex_items WHERE detail_at IS NULL")
        keys = [r["rating_key"] for r in rows]
    done = 0

    async def batch(chunk: list[str]) -> int:
        items = await _pages(chunk)
        if items:
            await db.executemany(_DETAIL, [_detail_row(i) for i in items])
        return len(items or [])

    # The client allows six requests in flight; keep four going.
    step = PAGE_BATCH * 4
    for i in range(0, len(keys), step):
        results = await asyncio.gather(*(batch(keys[j:j + PAGE_BATCH])
                                         for j in range(i, min(i + step, len(keys)), PAGE_BATCH)))
        done += sum(results)
        if progress:
            progress("Plex details", done)
    return done


_DETAIL = (
    "UPDATE plex_items SET guid = ?, mbid = ?, title = ?, genres = ?, styles = ?, "
    "moods = ?, countries = ?, locked = ?, summary = ?, studio = COALESCE(?, studio), "
    "thumb = COALESCE(?, thumb), year = COALESCE(?, year), updated_at = ?, detail_at = ? "
    "WHERE rating_key = ?")


def _detail_row(item: dict) -> tuple:
    locked = [f.get("name") for f in item.get("Field") or [] if f.get("locked")]

    def tags(key):
        values = tag_values(item, key)
        return json.dumps(values) if values else None

    return (item.get("guid"), mbid_of(item), item.get("title"), tags("Genre"), tags("Style"),
            tags("Mood"), tags("Country"), json.dumps(locked) if locked else None,
            (item.get("summary") or "")[:2000] or None, item.get("studio"), item.get("thumb"),
            item.get("year"), item.get("updatedAt"),
            int(item.get("updatedAt") or time.time()), str(item.get("ratingKey")))


async def refresh_items(keys: list[str]) -> None:
    """Re-read specific items (after an edit) — list fields and detail."""
    now = time.time()
    for i in range(0, len(keys), PAGE_BATCH):
        items = await _pages(keys[i:i + PAGE_BATCH]) or []
        rows = []
        for item in items:
            type_num = {"artist": ARTIST, "album": ALBUM, "track": TRACK}.get(item.get("type"))
            if type_num is not None:
                rows.append((item, _row(item, type_num, now)))
        if rows:
            await db.executemany(_UPSERT, [r for _, r in rows])
            await db.executemany(_DETAIL, [_detail_row(item) for item, _ in rows])


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
            "tracks": tracks, "album_keys": list(counter),
            "others": await album_others(album_key, paths)}


async def album_others(album_key: str, paths: list[str]) -> list[dict]:
    """The album's other tracks, as our files. Plex may file other albums'
    tracks in it too: a track it first saw untagged keeps its first album."""
    mine = {nfc(p) for p in spellings(paths)}
    other_paths = [r["path"] for r in await db.fetchall(
        "SELECT path FROM plex_items WHERE type = 'track' AND parent_key = ?", (album_key,))
        if r["path"] and nfc(r["path"]) not in mine]
    others: list[dict] = []
    for i in range(0, len(other_paths), 400):
        chunk = other_paths[i:i + 400]
        others += [dict(r) for r in await db.fetchall(
            "SELECT path, dirpath, artist_dir, album, album_artist, mb_releasegroupid "
            f"FROM library_index WHERE path IN ({','.join('?' * len(chunk))})", tuple(chunk))]
    return others


def foreign_files(info: dict | None, key: str | None) -> list[dict]:
    """The tracks of the Plex album these files map to that belong to
    another unit: a file of another album folder (disc subfolders count as
    their album's), or, for a unit split out of a folder by album tag, of
    one of the folder's other albums. Plex lumps two editions tagged alike
    into one album, and keeps a track in the album it first saw it in. Such
    an album is no unit's to match, tag, mood or give a cover: each unit in
    it would overwrite the last (until the reconciler, P1-C4, sorts it out).

    Only indexed files count (`others`), so a ghost entry doesn't; a file
    of ours the index has no folder for yet (an import) is placed by its
    path, so it doesn't either. [{"path", "folder", "split"}]: the file's
    album folder, and the split key its album tag gives it there."""
    if key is None:
        return []
    from ..identity import split_key, unit_dir
    folder, _, split = key.partition("|")
    out: list[dict] = []
    for other in (info or {}).get("others") or []:
        path = other.get("path") or ""
        theirs = unit_dir(other.get("dirpath") or (path.rsplit("/", 1)[0] if "/" in path else ""))
        k = split_key(other.get("album"), other.get("album_artist") or other.get("artist_dir"))
        # (in a split folder an untagged file joins its largest album: not foreign)
        if nfc(theirs).casefold() != nfc(folder).casefold() or (split and k and k != split):
            out.append({"path": path, "folder": theirs, "split": k})
    return out


def album_share(info: dict | None, rows: list[dict], releasegroup: str | None = None) -> dict:
    """Who the Plex album these files map to holds tracks of. Plex never
    regroups a track it filed before its tags were fixed, so an untitled
    album may collect tracks from all over the library.

    `ours`: this album's tracks in it (from any folder); `foreign`: other
    albums'; `owner_tagged`: whether one other, identified album holds most
    of it (and so looks after it)."""
    others = (info or {}).get("others") or []
    album = (info or {}).get("album") or {}
    mine = sum(1 for t in ((info or {}).get("tracks") or {}).values()
               if t.get("parent_key") == album.get("rating_key")) or len(rows)
    if not others:
        return {"ours": mine, "foreign": 0, "owner_tagged": False}
    from ..identity import album_id, album_key, same_album
    groups = ({releasegroup} if releasegroup
              else {r.get("mb_releasegroupid") for r in rows if r.get("mb_releasegroupid")})
    # by album tag: one album, in any edition (a [Single] is not its album)
    names = [album_id(t) for t in sorted({r.get("album") for r in rows if r.get("album")})]
    foreign: Counter = Counter()
    same = 0
    for other in others:
        rg = other.get("mb_releasegroupid")
        if rg and groups:
            if rg in groups:
                same += 1
                continue
        elif other.get("album") and any(
                same_album(album_id(other["album"]), mine_, "release_group") for mine_ in names):
            same += 1
            continue
        foreign[f"rg:{rg}" if rg else f"name:{album_key(other.get('album') or '')}"] += 1
    total = mine + len(others)
    top = foreign.most_common(1)
    return {"ours": mine + same, "foreign": sum(foreign.values()),
            "owner_tagged": bool(top) and top[0][0].startswith("rg:") and top[0][1] * 2 > total}


def shares_album(info: dict | None, rows: list[dict], releasegroup: str | None = None) -> bool:
    """Whether the Plex album these files map to is mostly other albums'
    tracks: then it is not theirs to match, name or tag, or each album in it
    would overwrite the last. The album holding most of it looks after it."""
    share = album_share(info, rows, releasegroup)
    return share["foreign"] > 0 and share["foreign"] >= share["ours"]


def jlist(value) -> list:
    if not value:
        return []
    try:
        out = json.loads(value)
        return out if isinstance(out, list) else []
    except (TypeError, ValueError):
        return []
