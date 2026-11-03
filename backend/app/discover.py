"""Discover: new releases from the artists in the library and from artists
like them, plus the YouTube Music charts and genre pages.

What an artist has put out is one request away — their artist page lists the
newest albums and singles first. The library has well over a thousand
artists, though, so the scan runs in the background, slowly: one request every
`discover_request_interval` seconds (12 by default — about 300 an hour, half
the browsing budget, so the artist browser always has room), biggest artists
first. A first pass over a large library takes hours. After that only artists
due a re-check are fetched: every two days for the ones the library holds a
lot of, weekly for the rest.

Each artist folder is matched to a YouTube Music artist once, by name — a
search, kept for months. Every artist page also lists related artists ("fans
also like"); counted across the library, weighted by how much of each artist
you have, those are the recommendations. The most recommended are scanned too,
which is where "new from artists like yours" comes from.

YouTube Music gives a release's year, not its date, so "new" is two things: a
recent year, and — once an artist has been checked before — a release that
was not on their page last time.
"""
import asyncio
import collections
import functools
import json
import logging
import math
import random
import re
import time
from datetime import datetime

from . import catalog, ownership
from .catalog import BACKGROUND, CatalogError
from .config import settings
from .db import db
from .events import bus
from .identity import (TYPE_WORDS, album_id, album_identity, album_keys, artist_keys, fold,
                       strict_key, type_words, unit_dir)
from .models import ErrorClass, JobStatus

log = logging.getLogger("mdl.discover")

DAY = 86400
FREQUENT_WEIGHT = 10            # library tracks that earn a re-check every…
FREQUENT_INTERVAL = 2 * DAY
DEFAULT_INTERVAL = 7 * DAY
UNMATCHED_RETRY = 30 * DAY      # a folder name no artist matched is searched again
REMATCH_AFTER = 180 * DAY       # …and a match is confirmed again this rarely
NOT_FOUND_RETRY = 30 * DAY
FAILED_RETRY = DAY
# Suggested artists are checked after library artists with this many tracks.
SUGGESTED_PRIORITY = 15
SYNC_EVERY = 600                # seconds between re-reading the library's artists
IDLE_WAIT = 300                 # nothing due: look again in this long
NEW_FOR = 30 * DAY              # how long a newly appeared release is flagged new
CHARTS_TTL = 6 * 3600
GENRES_TTL = 7 * DAY
GENRE_TTL = 12 * 3600
OWNED_TTL = 120

# Folder names that are not artists.
_NOT_ARTISTS = {"variousartists", "va", "various", "unknownartist", "unknown",
                "soundtrack", "soundtracks", "compilation", "compilations",
                "podcast", "podcasts", "audiobook", "audiobooks", "misc"}


# ------------------------------------------------------------------ library artists
async def library_artists() -> dict[str, tuple[str, int]]:
    """Artist folders in the library: strict key -> (name, tracks). Spellings
    of one artist (`AC_DC`, `ACDC`) are one entry."""
    rows = await db.fetchall(
        "SELECT substr(path, 1, instr(path, '/') - 1) AS folder, COUNT(*) AS n "
        "FROM library_index WHERE instr(path, '/') > 0 GROUP BY folder")
    out: dict[str, tuple[str, int]] = {}
    for r in rows:
        name, key = r["folder"], strict_key(r["folder"])
        if not name or name.startswith(".") or not key or key in _NOT_ARTISTS:
            continue
        prev_name, prev_n = out.get(key, (name, 0))
        out[key] = (prev_name if prev_n >= r["n"] else name, prev_n + r["n"])
    return out


async def sync_names() -> None:
    """Bring discover_names up to date with the library's artist folders."""
    names = await library_artists()
    known = {r["name_key"]: r["weight"] for r in await db.fetchall(
        "SELECT name_key, weight FROM discover_names")}
    await db.executemany(
        "INSERT INTO discover_names (name_key, name, weight) VALUES (?,?,?)",
        [(k, name, n) for k, (name, n) in names.items() if k not in known])
    await db.executemany(
        "UPDATE discover_names SET weight = ? WHERE name_key = ?",
        [(n, k) for k, (_, n) in names.items() if k in known and known[k] != n]
        + [(0, k) for k in known if k not in names and known[k]])
    await db.execute(
        "UPDATE discover_artists SET weight = COALESCE((SELECT SUM(n.weight) FROM "
        "discover_names n WHERE n.channel_id = discover_artists.channel_id), 0) "
        "WHERE source = 'library'")


_AUDIENCE_RE = re.compile(r"([\d.,]+)\s*([KMB])?", re.I)


def audience(text: str | None) -> float:
    """"649K monthly audience" -> 649000."""
    m = _AUDIENCE_RE.search(text or "")
    if not m:
        return 0.0
    try:
        number = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0.0
    return number * {"K": 1e3, "M": 1e6, "B": 1e9}.get((m.group(2) or "").upper(), 1)


def pick_artist(name: str, results: list[dict]) -> dict | None:
    """The search result that is this folder's artist: the same name, and of
    several same-named artists the one with the biggest audience. No exact
    name, no match — a wrong artist would fill Discover with strangers."""
    key = strict_key(name)
    same = [r for r in results if strict_key(r.get("name") or "") == key]
    return max(same, key=lambda r: audience(r.get("audience")), default=None)


# ------------------------------------------------------------------ scanning
async def _resolve(row) -> None:
    results = await catalog.search_artists(row["name"], priority=BACKGROUND)
    match = pick_artist(row["name"], results)
    await db.execute(
        "UPDATE discover_names SET channel_id = ?, resolved_at = ? WHERE name_key = ?",
        (match["channel_id"] if match else None, time.time(), row["name_key"]))
    if not match:
        return
    await db.execute(
        "INSERT INTO discover_artists (channel_id, name, thumbnail, source, weight) "
        "VALUES (?,?,?,'library',?) ON CONFLICT(channel_id) DO UPDATE SET "
        "source = 'library', thumbnail = COALESCE(discover_artists.thumbnail, "
        "excluded.thumbnail)",
        (match["channel_id"], match["name"], match["thumbnail"], row["weight"]))
    await db.execute(
        "UPDATE discover_artists SET weight = (SELECT SUM(weight) FROM discover_names "
        "WHERE channel_id = ?) WHERE channel_id = ?",
        (match["channel_id"], match["channel_id"]))


def _year(value) -> int | None:
    try:
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return None


async def _refresh(row) -> None:
    """Read an artist's whole discography — the artist page and the "More"
    lists behind it — into the shared catalogue cache, so opening the artist
    later asks YouTube nothing, and remember every release in it."""
    channel_id, now = row["channel_id"], time.time()
    try:
        doc = await catalog.discography(channel_id, refresh=True, priority=BACKGROUND)
    except CatalogError as exc:
        if exc.error_class in catalog.THROTTLED:
            raise
        gone = exc.status == 404 or isinstance(exc.__cause__, (KeyError, IndexError, TypeError))
        await db.execute(
            "UPDATE discover_artists SET error = ?, checked_at = ?, next_check = ? "
            "WHERE channel_id = ?",
            (str(exc)[:300], now, now + (NOT_FOUND_RETRY if gone else FAILED_RETRY),
             channel_id))
        return

    name = doc.get("name") or row["name"]
    releases = doc["releases"]
    # first_seen is never updated: it is how "new since last time" is known.
    # A release the artist page itself does not show, met for the first time,
    # is back catalogue — read now because the "More" lists are — and was
    # there all along; only what the page shows can have just appeared.
    first_page = set(doc.get("first_page") or [r["browse_id"] for r in releases])
    back = row["first_checked"] or now
    await db.executemany(
        "INSERT INTO discover_releases (browse_id, channel_id, artist, title, type, "
        "year, thumbnail, explicit, first_seen) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(browse_id) DO UPDATE SET title = excluded.title, "
        "type = excluded.type, year = excluded.year, thumbnail = excluded.thumbnail",
        [(r["browse_id"], channel_id, name, r["title"], r["type"], _year(r["year"]),
          r["thumbnail"], int(r["explicit"]),
          now if r["browse_id"] in first_page else back) for r in releases])
    await _derive([channel_id])
    related = [{k: r.get(k) for k in ("channel_id", "name", "thumbnail")}
               for r in doc.get("related") or []]
    frequent = row["source"] == "library" and (row["weight"] or 0) >= FREQUENT_WEIGHT
    interval = (FREQUENT_INTERVAL if frequent else DEFAULT_INTERVAL) * random.uniform(0.85, 1.15)
    await db.execute(
        "UPDATE discover_artists SET name = ?, thumbnail = COALESCE(thumbnail, ?), "
        "related = ?, first_checked = COALESCE(first_checked, ?), checked_at = ?, "
        "next_check = ?, error = NULL WHERE channel_id = ?",
        (name, doc.get("thumbnail"), json.dumps(related), now, now, now + interval,
         channel_id))


async def suggestions(limit: int | None = 40) -> list[dict]:
    """Artists the library's artists list as related, and the library does
    not have — ranked by how many of yours list them, each counted by how
    much of that artist you own (1 + log2 of the track count)."""
    rows = await db.fetchall(
        "SELECT channel_id, name, thumbnail, weight, related, source, dismissed "
        "FROM discover_artists")
    library = {r["channel_id"] for r in rows if r["source"] == "library"}
    dismissed = {r["channel_id"] for r in rows if r["dismissed"]}
    known = {r["channel_id"]: r for r in rows}
    score: collections.Counter = collections.Counter()
    because: dict[str, list] = collections.defaultdict(list)
    seen: dict[str, dict] = {}
    for r in rows:
        if r["source"] != "library" or not r["related"] or r["dismissed"]:
            continue
        weight = 1 + math.log2(1 + (r["weight"] or 0))
        for rel in json.loads(r["related"]):
            channel_id = rel["channel_id"]
            if channel_id in library or channel_id in dismissed:
                continue
            score[channel_id] += weight
            because[channel_id].append((r["weight"] or 0, r["name"]))
            seen.setdefault(channel_id, rel)
    out = []
    for channel_id, value in score.most_common(limit):
        own = known.get(channel_id)
        out.append({
            "channel_id": channel_id,
            "name": seen[channel_id]["name"],
            "thumbnail": seen[channel_id].get("thumbnail") or (own and own["thumbnail"]),
            "score": round(value, 1),
            "because": [name for _, name in sorted(because[channel_id], reverse=True)[:3]],
            "because_count": len(because[channel_id]),
        })
    return out


async def sync_suggestions() -> None:
    """Scan the most recommended artists too, for their new releases."""
    if settings.discover_suggested_artists <= 0:
        return
    top = await suggestions(settings.discover_suggested_artists)
    await db.executemany(
        "INSERT INTO discover_artists (channel_id, name, thumbnail, source, weight) "
        "VALUES (?,?,?,'suggested',?) ON CONFLICT(channel_id) DO UPDATE SET "
        "weight = excluded.weight WHERE discover_artists.source = 'suggested'",
        [(s["channel_id"], s["name"], s["thumbnail"], s["score"]) for s in top])


async def _next_work():
    """The most valuable thing to fetch next: an artist folder to match, or
    an artist due a re-check — whichever weighs more."""
    now = time.time()
    name = await db.fetchone(
        "SELECT * FROM discover_names WHERE weight > 0 AND (resolved_at = 0 "
        "OR (channel_id IS NULL AND resolved_at < ?) OR resolved_at < ?) "
        "ORDER BY weight DESC LIMIT 1", (now - UNMATCHED_RETRY, now - REMATCH_AFTER))
    artist = await db.fetchone(
        "SELECT *, CASE source WHEN 'library' THEN weight ELSE ? END AS priority "
        "FROM discover_artists WHERE dismissed = 0 AND next_check <= ? "
        "ORDER BY priority DESC LIMIT 1", (SUGGESTED_PRIORITY, now))
    if name and (artist is None or name["weight"] >= artist["priority"]):
        return "resolve", name
    if artist:
        return "refresh", artist
    return None


class Scanner:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.current: str | None = None
        self._wake = asyncio.Event()
        self._last_sync = 0.0
        self._steps = 0

    def start(self) -> None:
        if settings.discover_enabled and self.task is None:
            self.task = asyncio.create_task(self._run(), name="discover-scan")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    def wake(self) -> None:
        self._last_sync = 0.0   # a "scan now" also re-reads the library
        self._wake.set()

    async def _run(self) -> None:
        await asyncio.sleep(30)   # let startup and crash recovery go first
        try:
            # The list's stored columns, ready before anyone asks: after an
            # upgrade that is every release, after a restart the library.
            await _prepare()
        except Exception:
            log.exception("discover: preparing the release list failed")
        while True:
            before = catalog.pacer.count
            try:
                did = await self.step()
            except asyncio.CancelledError:
                raise
            except CatalogError as exc:
                # Throttled: the governor's backoff is set, and the pacer
                # holds the next request until it passes.
                log.info("discover scan paused: %s", exc)
                did = False
            except Exception:
                log.exception("discover scan step failed")
                did = False
            finally:
                self.current = None
            if did:
                # A whole discography can take a few requests; the interval
                # is per request, so the scan's share of the budget holds.
                used = max(1, catalog.pacer.count - before)
                await asyncio.sleep(settings.discover_request_interval * used)
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=IDLE_WAIT)
            except asyncio.TimeoutError:
                pass

    async def step(self) -> bool:
        if time.time() - self._last_sync > SYNC_EVERY:
            await sync_names()
            await sync_suggestions()
            self._last_sync = time.time()
        work = await _next_work()
        if work is None:
            # Out of due work: the pages read since the last sync may have
            # turned up recommendations worth a look of their own.
            await sync_suggestions()
            work = await _next_work()
            if work is None:
                return False
        kind, row = work
        self.current = row["name"]
        if kind == "resolve":
            await _resolve(row)
        else:
            await _refresh(row)
        self._steps += 1
        if self._steps % 25 == 0:
            await sync_suggestions()
        bus.publish("discover.progress", await status())
        return True


scanner = Scanner()


async def status() -> dict:
    now = time.time()
    names = await db.fetchone(
        "SELECT COUNT(*) AS total, COALESCE(SUM(resolved_at > 0), 0) AS searched, "
        "COALESCE(SUM(channel_id IS NOT NULL), 0) AS matched "
        "FROM discover_names WHERE weight > 0")
    artists = {r["source"]: r for r in await db.fetchall(
        "SELECT source, COUNT(*) AS total, COALESCE(SUM(checked_at IS NOT NULL), 0) AS "
        "checked, COALESCE(SUM(next_check <= ?), 0) AS due, MAX(checked_at) AS last "
        "FROM discover_artists WHERE dismissed = 0 GROUP BY source", (now,))}
    library = artists.get("library")
    suggested = artists.get("suggested")
    return {
        "enabled": settings.discover_enabled,
        "running": scanner.task is not None and not scanner.task.done(),
        "current": scanner.current,
        "library_artists": names["total"],
        "searched": names["searched"],
        "matched": names["matched"],
        "checked": library["checked"] if library else 0,
        "due": (library["due"] if library else 0) + (suggested["due"] if suggested else 0),
        "suggested": suggested["total"] if suggested else 0,
        "suggested_checked": suggested["checked"] if suggested else 0,
        "last_checked": max((r["last"] or 0) for r in artists.values()) if artists else None,
        "interval_sec": settings.discover_request_interval,
        "pacer": catalog.pacer.snapshot(),
    }


# ------------------------------------------------------------------ new releases
# Everything the list keeps between requests: the library's owned albums
# (refreshed every OWNED_TTL), the owned set the stored `owned` flags were
# last brought in line with, whether rows from before migration v10 have had
# their columns filled, and the finished-job stamp downloads were synced at.
_feed: dict = {"at": 0.0, "keys": frozenset()}
_prepare_lock = asyncio.Lock()
RELEASE_URL = "https://music.youtube.com/browse/"


async def owned_albums() -> frozenset[tuple[str, str]]:
    """(artist, album) keys of every album folder and album tag in the
    library — enough to say a listed release is already here without
    fetching its tracklist. Cached briefly; the library changes slowly. While
    nothing has changed the same set comes back, so callers can tell by
    identity.

    An album whose tag or folder says it is a single or EP (`(1980) The Boys
    Light Up [Single]`, tagged plainly) is keyed with its type (`…|single`):
    it is that single, never the album named like it (see _owned_by). Its
    names in any edition (every bracketed qualifier dropped) are keyed
    too, behind a `*`."""
    if time.time() - _feed["at"] < OWNED_TTL:
        return _feed["keys"]
    rows = await db.fetchall(
        "SELECT DISTINCT substr(path, 1, instr(path, '/') - 1) AS top, dirpath, album, "
        "norm_album, album_artist FROM library_index WHERE instr(path, '/') > 0")
    keys = set()
    for r in rows:
        hints = [r["top"], r["album_artist"]]
        folder = r["dirpath"] or ""
        names = set()
        for name in (r["album"], r["norm_album"],
                     folder.rsplit("/", 1)[-1] if folder and folder != r["top"] else None):
            names |= album_keys(name, hints)
            names |= {"*" + k for k in album_identity(name, hints)}
        unit = unit_dir(folder)
        leaf = unit.rsplit("/", 1)[-1] if unit and unit != r["top"] else None
        kind = _type_suffix(type_words(r["album"], leaf, hints=hints))
        # a release with several artists is filed under the whole credit
        # ("Will Sparks, AnGy KoRe/Describe") but listed under the one scanned
        for artist in artist_keys(r["top"]) | artist_keys(r["album_artist"]):
            for name in names:
                keys.add((artist, name + kind))
    keys = frozenset(keys)
    if keys != _feed["keys"]:
        _feed["keys"] = keys
    _feed["at"] = time.time()
    return _feed["keys"]


def _type_suffix(words) -> str:
    """How owned_albums keys an album that says it is a single or EP."""
    found = sorted(set(words) & TYPE_WORDS)
    return "|" + ",".join(found) if found else ""


_ANY_TYPE = ("", "|ep", "|single", "|ep,single")

# Run over every release an artist has, each time one is stored; a title
# says the same thing each time.
_key = functools.lru_cache(maxsize=1 << 16)(strict_key)
_album = functools.lru_cache(maxsize=1 << 16)(album_id)
_classify = functools.lru_cache(maxsize=1 << 16)(catalog.classify_release)


def _owned_by(owned, artist_key: str, title: str, kind: str | None = None) -> bool:
    """Whether the library holds this release, by name in any edition
    (identity.same_album, "release_group"). A single or EP (`kind`) is held
    by its `[Single]` folder or under its plain name; an album is never held
    by a single named like it."""
    album = _album(title, kind=kind if kind in TYPE_WORDS else None)
    suffixes = _ANY_TYPE if album.kind else (_type_suffix(album.types),)
    names = list(album.keys) + ["*" + k for k in album.names]
    return any((artist_key, name + suffix) in owned for name in names for suffix in suffixes)


def _in_library(owned, artist: str | None, title: str, kind: str | None = None) -> bool:
    return _owned_by(owned, _key(artist or ""), title, kind)


def _chunks(items, size: int = 500):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _marks(n: int) -> str:
    return ",".join("?" * n)


async def _derive(channel_ids) -> None:
    """Fill the columns the list filters on (see migration v10) for every
    release of these artists: run whenever their releases are stored."""
    owned = await owned_albums()
    updates = []
    for chunk in _chunks(sorted(set(channel_ids))):
        rows = await db.fetchall(
            "SELECT r.browse_id, r.channel_id, r.artist, r.title, r.type, r.explicit, "
            "r.dismissed, a.name AS artist_name FROM discover_releases r "
            "LEFT JOIN discover_artists a ON a.channel_id = r.channel_id "
            f"WHERE r.channel_id IN ({_marks(len(chunk))})", tuple(chunk))
        groups: dict[tuple, list] = collections.defaultdict(list)
        for r in rows:
            groups[(r["channel_id"], _key(r["title"]), r["type"])].append(r)
        for (_, title_key, _), members in groups.items():
            # YouTube Music lists a clean and an explicit release under one
            # title as two; the explicit one is the original and stands for
            # both. A dismissal covers every edition, a new one included.
            members.sort(key=lambda r: (-r["explicit"], r["browse_id"]))
            dismissed = int(any(m["dismissed"] for m in members))
            for i, r in enumerate(members):
                artist = r["artist"] or r["artist_name"] or ""
                artist_key = _key(artist)
                updates.append((
                    _classify(r["title"], r["type"] or "album"), title_key, artist_key,
                    fold(f"{r['title']} {artist}"), int(i == 0),
                    int(_owned_by(owned, artist_key, r["title"], r["type"])), dismissed,
                    r["browse_id"]))
    await db.executemany(
        "UPDATE discover_releases SET category = ?, title_key = ?, artist_key = ?, "
        "search = ?, rep = ?, owned = ?, dismissed = ? WHERE browse_id = ?", updates)


async def _sync_owned() -> None:
    """Keep each release's `owned` in step with the library — looking again
    only at the artists whose albums came or went since last time."""
    owned = await owned_albums()
    synced = _feed.get("synced")
    if synced is owned:
        return
    columns = "SELECT browse_id, artist_key, title, type, owned FROM discover_releases"
    if synced is None:
        rows = await db.fetchall(columns)
    else:
        rows = []
        for chunk in _chunks({artist for artist, _ in owned ^ synced}):
            rows += await db.fetchall(
                f"{columns} WHERE artist_key IN ({_marks(len(chunk))})", tuple(chunk))
    changes = []
    for r in rows:
        have = int(_owned_by(owned, r["artist_key"] or "", r["title"], r["type"]))
        if have != r["owned"]:
            changes.append((have, r["browse_id"]))
    await db.executemany("UPDATE discover_releases SET owned = ? WHERE browse_id = ?",
                         changes)
    _feed["synced"] = owned


async def _sync_downloaded() -> None:
    """Mark releases downloaded here — every edition of one — once a job for
    one completes, whatever the library index makes of the files."""
    mark = await db.fetchone(
        "SELECT COUNT(*) AS n, MAX(finished_at) AS last FROM jobs WHERE status = ?",
        (JobStatus.COMPLETED,))
    stamp = (mark["n"], mark["last"])
    if _feed.get("jobs") == stamp:
        return
    await db.execute(
        "UPDATE discover_releases SET downloaded = 1 WHERE downloaded = 0 "
        "AND (channel_id, title_key, type) IN (SELECT r.channel_id, r.title_key, r.type "
        "FROM jobs j JOIN discover_releases r ON r.browse_id = substr(j.source_url, ?) "
        "WHERE j.status = ? AND j.source_url LIKE ?)",
        (len(RELEASE_URL) + 1, JobStatus.COMPLETED, RELEASE_URL + "%"))
    _feed["jobs"] = stamp


async def _prepare() -> None:
    """Bring the stored columns up to date before reading them."""
    async with _prepare_lock:
        if not _feed.get("derived"):
            rows = await db.fetchall(
                "SELECT DISTINCT channel_id FROM discover_releases WHERE category IS NULL")
            if rows:
                await _derive([r["channel_id"] for r in rows])
            await catalog.index_cached_tracks()
            _feed["derived"] = True
        await _sync_owned()
        await _sync_downloaded()


_FROM = "FROM discover_releases r JOIN discover_artists a ON a.channel_id = r.channel_id"
# On the page now, and not the first time this artist was looked at. Takes
# the oldest first_seen that still counts as new.
_IS_NEW = ("(a.first_checked IS NOT NULL AND r.first_seen > a.first_checked + 3600 "
           "AND r.first_seen > ?)")
_OWNED = "(r.owned = 1 OR r.downloaded = 1)"


def _scope(*, years: int, source: str | None, artist: str | None,
           q: str | None) -> tuple[str, list]:
    where = ["r.rep = 1", "r.dismissed = 0", "a.dismissed = 0"]
    params: list = []
    if years > 0:
        where.append("r.year >= ?")
        params.append(datetime.now().year - years + 1)
    if source in ("library", "suggested"):
        where.append("a.source = ?")
        params.append(source)
    if artist:
        where.append("r.channel_id = ?")
        params.append(artist)
    needle = fold((q or "").strip())
    if needle:
        where.append("instr(r.search, ?) > 0")
        params.append(needle)
    return " AND ".join(where), params


def shape(r) -> dict:
    """A stored release as the release rows draw it."""
    return {
        "browse_id": r["browse_id"],
        "title": r["title"],
        "type": r["type"] or "album",
        "category": r["category"],
        "year": str(r["year"]) if r["year"] else None,
        "explicit": bool(r["explicit"]),
        "thumbnail": r["thumbnail"],
        "url": f"{RELEASE_URL}{r['browse_id']}",
        "artist_name": r["artist"] or r["artist_name"],
        "channel_id": r["channel_id"],
        "source": r["source"],
        "in_library": bool(r["owned"]),
        "downloaded": bool(r["downloaded"]),
        "is_new": bool(r["is_new"]),
        "first_seen": r["first_seen"],
        "tracks": None, "track_count": None, "duration_sec": None, "artists": None,
        "library": None, "contained_in": [], "versions": [],
    }


def owned_release(r: dict) -> bool:
    """In the library already, or downloaded here."""
    return r["in_library"] or r["downloaded"]


async def release_page(*, years: int = 2, source: str | None = None,
                       include_owned: bool = False, categories=None,
                       artist: str | None = None, q: str | None = None,
                       offset: int = 0, limit: int = 500) -> dict:
    """One page of releases by scanned artists from the last `years` years
    (0: any year), filtered here so a page of singles is a full page. What
    appeared since the last look comes first, then newest first. With it,
    the totals the filters need: per category (whatever categories are
    picked), and how many owned ones are hidden or shown."""
    await _prepare()
    where, params = _scope(years=years, source=source, artist=artist, q=q)
    counts, total, owned = {}, 0, 0
    for t in await db.fetchall(
            f"SELECT r.category AS category, COUNT(*) AS n, SUM({_OWNED}) AS owned "
            f"{_FROM} WHERE {where} GROUP BY r.category", tuple(params)):
        shown = t["n"] if include_owned else t["n"] - t["owned"]
        if shown:
            counts[t["category"]] = shown
        if not categories or t["category"] in categories:
            total += shown
            owned += t["owned"]
    if categories:
        where += f" AND r.category IN ({_marks(len(categories))})"
        params += sorted(categories)
    if not include_owned:
        where += f" AND NOT {_OWNED}"
    rows = await db.fetchall(
        f"SELECT r.*, a.source, a.name AS artist_name, {_IS_NEW} AS is_new {_FROM} "
        f"WHERE {where} ORDER BY is_new DESC, r.year DESC, r.first_seen DESC, "
        "r.browse_id LIMIT ? OFFSET ?",
        (time.time() - NEW_FOR, *params, limit, offset))
    return {
        "releases": [shape(r) for r in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
        "counts": counts,
        "owned": owned,
    }


async def new_releases(*, years: int = 2, source: str | None = None,
                       include_owned: bool = False) -> list[dict]:
    """Every release release_page would page through."""
    page = await release_page(years=years, source=source, include_owned=include_owned,
                              limit=-1)
    return page["releases"]


async def release_artists(*, years: int = 2, source: str | None = None,
                          include_owned: bool = False, categories=None) -> list[dict]:
    """The artists behind those releases, with how many each has under the
    same filters."""
    await _prepare()
    where, params = _scope(years=years, source=source, artist=None, q=None)
    if categories:
        where += f" AND r.category IN ({_marks(len(categories))})"
        params += sorted(categories)
    if not include_owned:
        where += f" AND NOT {_OWNED}"
    rows = await db.fetchall(
        f"SELECT r.channel_id, a.name, a.source, COUNT(*) AS releases, "
        f"SUM({_IS_NEW}) AS new, MAX(r.first_seen) AS latest {_FROM} WHERE {where} "
        "GROUP BY r.channel_id", (time.time() - NEW_FOR, *params))
    return sorted(({k: r[k] for k in ("channel_id", "name", "source", "releases", "new",
                                       "latest")} for r in rows),
                  key=lambda a: (a["name"] or "").casefold())


async def known_artists() -> list[dict]:
    """Every artist Discover follows — yours and the recommended ones — with
    what there is to find: releases, how many just appeared, and how many
    the library does not have."""
    await _prepare()
    found = {r["channel_id"]: r for r in await db.fetchall(
        f"SELECT r.channel_id, COUNT(*) AS releases, SUM({_IS_NEW} AND NOT {_OWNED}) AS new, "
        f"SUM(NOT {_OWNED}) AS missing, MAX(r.first_seen) AS latest {_FROM} "
        "WHERE r.rep = 1 AND r.dismissed = 0 GROUP BY r.channel_id", (time.time() - NEW_FOR,))}
    out = []
    for a in await db.fetchall("SELECT channel_id, name, source, weight, checked_at "
                               "FROM discover_artists WHERE dismissed = 0"):
        r = found.get(a["channel_id"])
        out.append({
            "channel_id": a["channel_id"], "name": (a["name"] or "").strip(),
            "source": a["source"],
            "tracks": int(a["weight"] or 0) if a["source"] == "library" else 0,
            "checked": a["checked_at"] is not None,
            "releases": r["releases"] if r else 0, "new": r["new"] if r else 0,
            "missing": r["missing"] if r else 0, "latest": r["latest"] if r else 0,
        })
    return sorted(out, key=lambda a: (a["name"] or "").casefold())


# ------------------------------------------------------------------ search
async def _track_states(tracks: list[dict]) -> list[dict]:
    """Library state for songs from anywhere (same-album rule; see ownership)."""
    credits = {t["artist"] for t in tracks if t.get("artist")}
    names = {re.split(r",| & ", t["artist"])[0].strip() for t in tracks if t.get("artist")}
    index = await ownership.load_index(credits, names, [t["video_id"] for t in tracks])
    return [{**t, **index.lookup(title=t["title"], credit=t.get("artist"),
                                 album=t.get("album"), video_id=t["video_id"],
                                 length=t.get("duration_sec"))} for t in tracks]


async def search_tracks(q: str, limit: int = 50) -> dict:
    """Songs on the tracklists already loaded, by title or artist."""
    needle = fold(" ".join((q or "").split()))
    if len(needle) < 2:
        return {"total": 0, "items": []}
    total = (await db.fetchone(
        "SELECT COUNT(*) AS n FROM catalog_tracks WHERE instr(search, ?) > 0", (needle,)))["n"]
    rows = await db.fetchall(
        "SELECT * FROM catalog_tracks WHERE instr(search, ?) > 0 "
        "ORDER BY instr(search, ?) = 1 DESC, year DESC, browse_id, position LIMIT ?",
        (needle, needle, limit))
    tracks = [{"video_id": r["video_id"], "title": r["title"], "artist": r["artists"],
               "artist_id": r["channel_id"], "album": r["album"], "album_id": r["browse_id"],
               "year": r["year"], "thumbnail": r["thumbnail"],
               "duration_sec": r["duration_sec"]} for r in rows]
    return {"total": total, "items": await _track_states(tracks)}


async def search_local(q: str) -> dict:
    """Everything Discover already knows that matches: releases in the
    catalogue (any year, owned ones included) and songs on loaded
    tracklists. Instant — nothing is asked of YouTube."""
    releases = await release_page(years=0, include_owned=True, q=q, limit=100)
    return {"releases": {"total": releases["total"], "items": releases["releases"]},
            "tracks": await search_tracks(q)}


async def annotate_youtube(found: dict) -> dict:
    """YouTube Music search results with what is already here: artists
    Discover follows, albums the library has, songs it holds."""
    ids = [a["channel_id"] for a in found["artists"]]
    known = {r["channel_id"]: r["source"] for r in await db.fetchall(
        "SELECT channel_id, source FROM discover_artists WHERE dismissed = 0 AND channel_id IN "
        f"({_marks(len(ids))})", tuple(ids))} if ids else {}
    await _prepare()
    owned = await owned_albums()
    browse_ids = [a["browse_id"] for a in found["albums"]]
    stored = {r["browse_id"]: r for r in await db.fetchall(
        "SELECT browse_id, owned, downloaded FROM discover_releases WHERE browse_id IN "
        f"({_marks(len(browse_ids))})", tuple(browse_ids))} if browse_ids else {}
    albums = []
    for a in found["albums"]:
        row = stored.get(a["browse_id"])
        albums.append({**a, "in_library": bool(row["owned"]) if row
                       else _in_library(owned, a["artist"], a["title"], a.get("type")),
                       "downloaded": bool(row and row["downloaded"])})
    return {
        "artists": [{**a, "source": known.get(a["channel_id"])} for a in found["artists"]],
        "songs": await _track_states(found["songs"]) if found["songs"] else [],
        "albums": albums,
    }


def _because(rows, channel_id: str) -> tuple[list[str], int]:
    """Which of your artists list this one as related, biggest first."""
    who = []
    for r in rows:
        if r["source"] == "library" and r["related"] and not r["dismissed"] \
                and any(x.get("channel_id") == channel_id for x in json.loads(r["related"])):
            who.append((r["weight"] or 0, r["name"]))
    return [name for _, name in sorted(who, reverse=True)[:3]], len(who)


async def artist_context(view: dict) -> dict | None:
    """Discover's side of an artist view: on each release, whether it just
    appeared, was dismissed or was downloaded here; for the artist, whether
    they are yours or recommended, and why. None for an artist Discover does
    not follow."""
    await _prepare()
    ids = [r["browse_id"] for r in view["releases"]]
    stored: dict[str, dict] = {}
    for chunk in _chunks(ids):
        for r in await db.fetchall(
                f"SELECT r.browse_id, r.channel_id, r.dismissed, r.downloaded, {_IS_NEW} AS is_new "
                f"{_FROM} WHERE r.browse_id IN ({_marks(len(chunk))})",
                (time.time() - NEW_FOR, *chunk)):
            stored[r["browse_id"]] = r
    for release in view["releases"]:
        row = stored.get(release["browse_id"])
        release["known"] = row is not None
        release["is_new"] = bool(row and row["is_new"])
        release["dismissed"] = bool(row and row["dismissed"])
        release["downloaded"] = bool(row and row["downloaded"])
    # Discover may know the artist by another of their channel ids.
    channels = collections.Counter(r["channel_id"] for r in stored.values())
    candidates = [c for c, _ in channels.most_common()] + [view["artist"]["channel_id"]]
    rows = await db.fetchall(
        "SELECT channel_id, name, source, weight, related, dismissed, checked_at "
        "FROM discover_artists")
    by_id = {r["channel_id"]: r for r in rows}
    artist = next((by_id[c] for c in candidates if c in by_id), None)
    if artist is None:
        return None
    because, because_count = ([], 0)
    if artist["source"] == "suggested":
        because, because_count = _because(rows, artist["channel_id"])
    return {
        "channel_id": artist["channel_id"],
        "source": artist["source"],
        "tracks": int(artist["weight"] or 0) if artist["source"] == "library" else 0,
        "dismissed": bool(artist["dismissed"]),
        "checked_at": artist["checked_at"],
        "because": because,
        "because_count": because_count,
    }


async def dismiss_release(browse_id: str) -> None:
    await dismiss_releases([browse_id])


async def dismiss_releases(browse_ids, dismissed: bool = True) -> int:
    """Hide releases from Discover — or, with dismissed=False, bring them
    back. A clean and an explicit edition are listed as one release, so both
    go: hiding only the one shown would let the other take its place.
    Returns how many listed releases that was."""
    await _prepare()
    groups = set()
    for chunk in _chunks(dict.fromkeys(browse_ids)):
        groups |= {(r["channel_id"], r["title_key"], r["type"]) for r in await db.fetchall(
            "SELECT channel_id, title_key, type FROM discover_releases "
            f"WHERE browse_id IN ({_marks(len(chunk))})", tuple(chunk))}
    for chunk in _chunks(sorted(groups, key=str)):
        await db.execute(
            "UPDATE discover_releases SET dismissed = ? WHERE (channel_id, title_key, type) "
            f"IN (VALUES {','.join(['(?,?,?)'] * len(chunk))})",
            (int(dismissed), *[v for g in chunk for v in g]))
    return len(groups)


async def dismiss_artist(channel_id: str, name: str = "") -> None:
    await db.execute(
        "INSERT INTO discover_artists (channel_id, name, source, dismissed) "
        "VALUES (?,?,'suggested',1) ON CONFLICT(channel_id) DO UPDATE SET dismissed = 1",
        (channel_id, name or channel_id))


async def suggested_artists(limit: int = 40, years: int = 2) -> list[dict]:
    """Recommendations, each with how many recent releases the scan found."""
    found = await suggestions(limit)
    if not found:
        return found
    min_year = datetime.now().year - max(1, years) + 1
    ids = [s["channel_id"] for s in found]
    counts = {r["channel_id"]: r["n"] for r in await db.fetchall(
        "SELECT channel_id, COUNT(*) AS n FROM discover_releases WHERE dismissed = 0 "
        f"AND rep = 1 AND year >= ? AND channel_id IN ({','.join('?' * len(ids))}) GROUP BY channel_id",
        (min_year, *ids))}
    checked = {r["channel_id"] for r in await db.fetchall(
        "SELECT channel_id FROM discover_artists WHERE checked_at IS NOT NULL "
        f"AND channel_id IN ({','.join('?' * len(ids))})", tuple(ids))}
    for s in found:
        s["recent_releases"] = counts.get(s["channel_id"], 0) if s["channel_id"] in checked else None
    return found


# ------------------------------------------------------------------ charts
async def charts(country: str | None = None) -> dict:
    """A country's charts: its top artists, and its top music-video playlists."""
    country = (country or settings.discover_country or "ZZ").upper()
    key = f"charts:{country}"
    cached = await catalog._cache_get(key, CHARTS_TTL)
    if cached:
        return cached
    raw = await catalog.call("get_charts", country)
    doc = {
        "country": country,
        "countries": (raw.get("countries") or {}).get("options") or [],
        "artists": [{
            "rank": int(a["rank"]) if str(a.get("rank") or "").isdigit() else i,
            "channel_id": a["browseId"],
            "name": a.get("title") or "",
            "thumbnail": catalog._thumb(a.get("thumbnails"), 120),
            "subscribers": a.get("subscribers"),
            "trend": a.get("trend"),
        } for i, a in enumerate(raw.get("artists") or [], start=1) if a.get("browseId")],
        "playlists": [{
            "title": p.get("title") or "",
            "playlist_id": p["playlistId"],
            "thumbnail": catalog._thumb(p.get("thumbnails"), 226),
        } for p in raw.get("videos") or [] if p.get("playlistId")],
    }
    await catalog._cache_put(key, doc)
    return doc


async def genres() -> dict:
    cached = await catalog._cache_get("genres", GENRES_TTL)
    if cached:
        return cached
    raw = await catalog.call("get_mood_categories")

    def listing(name):
        return [{"title": g["title"], "params": g["params"]}
                for g in raw.get(name) or [] if g.get("params")]

    doc = {"genres": listing("Genres"), "moods": listing("Moods & moments")}
    await catalog._cache_put("genres", doc)
    return doc


def _runs(node) -> list:
    return ((node or {}).get("runs")) or []


def _text(node) -> str:
    return "".join(r.get("text", "") for r in _runs(node))


def _browse_id(runs, prefix: str) -> str | None:
    for r in runs:
        target = ((r.get("navigationEndpoint") or {}).get("browseEndpoint") or {}).get("browseId")
        if target and target.startswith(prefix):
            return target
    return None


def _shelves(raw):
    """(title, items) for every carousel on a browse page."""
    found = []

    def walk(node):
        if isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, dict):
            shelf = node.get("musicCarouselShelfRenderer")
            if shelf is not None:
                header = (shelf.get("header") or {}).get("musicCarouselShelfBasicHeaderRenderer") or {}
                found.append((_text(header.get("title")), shelf.get("contents") or []))
                return
            for v in node.values():
                walk(v)

    walk(raw)
    return found


def _song(item: dict) -> dict | None:
    row = item.get("musicResponsiveListItemRenderer")
    if not row:
        return None
    video_id = (row.get("playlistItemData") or {}).get("videoId")
    columns = [(c.get("musicResponsiveListItemFlexColumnRenderer") or {}).get("text")
               for c in row.get("flexColumns") or []]
    if not video_id or not columns:
        return None
    by_line = _text(columns[1]) if len(columns) > 1 else ""
    artist, _, views = by_line.partition("•")
    album_runs = _runs(columns[2]) if len(columns) > 2 else []
    thumbs = (((row.get("thumbnail") or {}).get("musicThumbnailRenderer") or {})
              .get("thumbnail") or {}).get("thumbnails")
    return {
        "video_id": video_id,
        "title": _text(columns[0]),
        "artist": artist.strip(),
        "artist_id": _browse_id(_runs(columns[1]) if len(columns) > 1 else [], "UC"),
        "album": "".join(r.get("text", "") for r in album_runs) or None,
        "album_id": _browse_id(album_runs, "MPREb_"),
        "views": views.strip() or None,
        "thumbnail": catalog._thumb(thumbs, 60),
    }


def _card(item: dict) -> dict | None:
    card = item.get("musicTwoRowItemRenderer")
    if not card:
        return None
    target = ((card.get("navigationEndpoint") or {}).get("browseEndpoint") or {}).get("browseId") or ""
    thumbs = (((card.get("thumbnailRenderer") or {}).get("musicThumbnailRenderer") or {})
              .get("thumbnail") or {}).get("thumbnails")
    subtitle = card.get("subtitle")
    return {
        "id": target,
        "title": _text(card.get("title")),
        "subtitle": _text(subtitle),
        "artist_id": _browse_id(_runs(subtitle), "UC"),
        "thumbnail": catalog._thumb(thumbs, 226),
    }


def parse_genre(raw: dict) -> dict:
    """A genre page: its top songs, its top albums and its curated playlists.
    ytmusicapi's own parser for these pages broke when YouTube Music added
    song rows to them, so they are read directly."""
    songs, albums, playlists = [], [], []
    for title, items in _shelves(raw):
        name = title.casefold()
        if name.startswith("songs") or name.endswith("songs"):
            songs += [s for s in map(_song, items) if s]
        elif "album" in name:
            for card in filter(None, map(_card, items)):
                if card["id"].startswith("MPREb_"):
                    artist = card["subtitle"].rpartition("•")[2].strip()
                    albums.append({"browse_id": card["id"], "title": card["title"],
                                   "artist": artist, "artist_id": card["artist_id"],
                                   "thumbnail": card["thumbnail"]})
        elif "playlist" in name and "community" not in name:
            for card in filter(None, map(_card, items)):
                if card["id"].startswith("VL"):
                    playlists.append({"playlist_id": card["id"][2:], "title": card["title"],
                                      "subtitle": card["subtitle"],
                                      "thumbnail": card["thumbnail"]})
    for rank, song in enumerate(songs, start=1):
        song["rank"] = rank
    return {"songs": songs, "albums": albums, "playlists": playlists}


async def genre(title: str, *, refresh: bool = False) -> dict:
    listing = await genres()
    match = next((g for g in listing["genres"] + listing["moods"]
                  if g["title"].casefold() == (title or "").casefold()), None)
    if match is None:
        raise CatalogError(f"No genre called {title!r}", 404, ErrorClass.PERMANENT)
    key = f"genre:{match['params']}"
    doc = None if refresh else await catalog._cache_get(key, GENRE_TTL)
    if doc is None:
        raw = await catalog.call("_send_request", "browse", {
            "browseId": "FEmusic_moods_and_genres_category", "params": match["params"]})
        doc = {"title": match["title"], **parse_genre(raw)}
        await catalog._cache_put(key, doc)
    return await _annotate_genre(doc)


async def _annotate_genre(doc: dict) -> dict:
    """Library state for a genre page's songs (same-album rule) and albums."""
    songs = doc["songs"]
    credits = {s["artist"] for s in songs if s["artist"]}
    names = {re.split(r",| & ", s["artist"])[0].strip() for s in songs if s["artist"]}
    index = await ownership.load_index(credits, names, [s["video_id"] for s in songs])
    owned = await owned_albums()
    out_songs = [{**s, **index.lookup(title=s["title"], credit=s["artist"],
                                      album=s["album"], video_id=s["video_id"])}
                 for s in songs]
    out_albums = [{**a, "in_library": _in_library(owned, a["artist"], a["title"])}
                  for a in doc["albums"]]
    return {**doc, "songs": out_songs, "albums": out_albums}


async def plan_songs(songs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Chart songs to queue, as album picks wherever possible.

    A chart lists a song by its music video. Downloading it through its album
    instead gets the album audio, the album's tags and the right folder — so
    each song is found in its album's tracklist by title. One that cannot be
    (no album, or no track by that name) is returned to be fetched as the
    bare video."""
    by_album: dict[str, list[dict]] = collections.defaultdict(list)
    loose: list[dict] = []
    for song in songs:
        (by_album[song["album_id"]] if song.get("album_id") else loose).append(song)
    picks = []
    for album_id, group in by_album.items():
        try:
            album = await catalog.album(album_id)
        except CatalogError:
            loose += group
            continue
        video_ids = []
        for song in group:
            keys = ownership.title_keys(song["title"])
            track = next((t for t in album["tracks"]
                          if t["available"] and ownership.title_keys(t["title"]) & keys), None)
            if track:
                video_ids.append(track["video_id"])
            else:
                loose.append(song)
        if video_ids:
            picks.append({"browse_id": album_id, "video_ids": video_ids,
                          "title": album["title"],
                          "artist": ", ".join(album["artists"]) or group[0].get("artist")})
    return picks, loose
