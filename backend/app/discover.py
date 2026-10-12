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
from .library import album_identity, strict_key
from .models import ErrorClass

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
    channel_id, now = row["channel_id"], time.time()
    try:
        raw = await catalog.call("get_artist", channel_id, priority=BACKGROUND)
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

    name = raw.get("name") or row["name"]
    releases = []
    for section in catalog.SECTIONS:
        for item in (raw.get(section) or {}).get("results") or []:
            release = catalog.release_from_listing(item, section)
            if release:
                releases.append(release)
    # first_seen is never updated: it is how "new since last time" is known.
    await db.executemany(
        "INSERT INTO discover_releases (browse_id, channel_id, artist, title, type, "
        "year, thumbnail, explicit, first_seen) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(browse_id) DO UPDATE SET title = excluded.title, "
        "type = excluded.type, year = excluded.year, thumbnail = excluded.thumbnail",
        [(r["browse_id"], channel_id, name, r["title"], r["type"], _year(r["year"]),
          r["thumbnail"], int(r["explicit"]), now) for r in releases])
    related = [{
        "channel_id": r["browseId"],
        "name": r.get("title") or "",
        "thumbnail": catalog._thumb(r.get("thumbnails"), 120),
    } for r in ((raw.get("related") or {}).get("results") or [])
        if (r.get("browseId") or "").startswith("UC")]
    frequent = row["source"] == "library" and (row["weight"] or 0) >= FREQUENT_WEIGHT
    interval = (FREQUENT_INTERVAL if frequent else DEFAULT_INTERVAL) * random.uniform(0.85, 1.15)
    await db.execute(
        "UPDATE discover_artists SET name = ?, thumbnail = COALESCE(thumbnail, ?), "
        "related = ?, first_checked = COALESCE(first_checked, ?), checked_at = ?, "
        "next_check = ?, error = NULL WHERE channel_id = ?",
        (name, catalog._thumb(raw.get("thumbnails"), 226), json.dumps(related), now, now,
         now + interval, channel_id))


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
        while True:
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
                await asyncio.sleep(settings.discover_request_interval)
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
_owned: dict = {"at": 0.0, "keys": frozenset()}


async def owned_albums() -> frozenset[tuple[str, str]]:
    """(artist, album) keys of every album folder and album tag in the
    library — enough to say a listed release is already here without
    fetching its tracklist. Cached briefly; the library changes slowly."""
    if time.time() - _owned["at"] < OWNED_TTL:
        return _owned["keys"]
    rows = await db.fetchall(
        "SELECT DISTINCT substr(path, 1, instr(path, '/') - 1) AS top, dirpath, album, "
        "norm_album, album_artist FROM library_index WHERE instr(path, '/') > 0")
    keys = set()
    for r in rows:
        hints = [r["top"], r["album_artist"]]
        names = album_identity(r["album"], hints) | album_identity(r["norm_album"], hints)
        folder = r["dirpath"] or ""
        if folder and folder != r["top"]:
            names |= album_identity(folder.rsplit("/", 1)[-1], hints)
        for artist in {strict_key(r["top"]), strict_key(r["album_artist"] or "")} - {""}:
            for name in names:
                keys.add((artist, name))
    _owned.update(at=time.time(), keys=frozenset(keys))
    return _owned["keys"]


def _in_library(owned, artist: str | None, title: str) -> bool:
    artist_key = strict_key(artist or "")
    return any((artist_key, name) in owned for name in album_identity(title))


async def new_releases(*, years: int = 2, source: str | None = None,
                       include_owned: bool = False, limit: int = 600) -> list[dict]:
    """Recent releases by scanned artists, newest first, shaped like the
    artist browser's releases so the same rows draw them."""
    min_year = datetime.now().year - max(1, years) + 1
    params: list = [min_year]
    where = "r.dismissed = 0 AND a.dismissed = 0 AND r.year >= ?"
    if source in ("library", "suggested"):
        where += " AND a.source = ?"
        params.append(source)
    rows = await db.fetchall(
        "SELECT r.*, a.source, a.name AS artist_name, a.first_checked "
        "FROM discover_releases r JOIN discover_artists a ON a.channel_id = r.channel_id "
        f"WHERE {where} ORDER BY r.year DESC, r.first_seen DESC LIMIT ?",
        (*params, limit))
    owned = await owned_albums()
    now = time.time()
    # YouTube Music lists a clean and an explicit release under one title as
    # two releases; one row is enough, and the explicit one is the original.
    rows = sorted(rows, key=lambda r: -r["explicit"])
    seen: set[tuple] = set()
    out = []
    for r in rows:
        key = (r["channel_id"], strict_key(r["title"]), r["type"])
        if key in seen:
            continue
        seen.add(key)
        artist = r["artist"] or r["artist_name"]
        in_library = _in_library(owned, artist, r["title"])
        if in_library and not include_owned:
            continue
        out.append({
            "browse_id": r["browse_id"],
            "title": r["title"],
            "type": r["type"] or "album",
            "category": catalog.classify_release(r["title"], r["type"] or "album"),
            "year": str(r["year"]) if r["year"] else None,
            "explicit": bool(r["explicit"]),
            "thumbnail": r["thumbnail"],
            "url": f"https://music.youtube.com/browse/{r['browse_id']}",
            "artist_name": artist,
            "channel_id": r["channel_id"],
            "source": r["source"],
            "in_library": in_library,
            # On the page now, and not the first time this artist was looked at.
            "is_new": bool(r["first_checked"]) and r["first_seen"] > r["first_checked"] + 3600
            and now - r["first_seen"] < NEW_FOR,
            "first_seen": r["first_seen"],
            "tracks": None, "track_count": None, "duration_sec": None, "artists": None,
            "library": None, "contained_in": [], "versions": [],
        })
    out.sort(key=lambda r: (-(int(r["year"] or 0)), -r["first_seen"]))
    return out


async def dismiss_release(browse_id: str) -> None:
    await db.execute("UPDATE discover_releases SET dismissed = 1 WHERE browse_id = ?",
                     (browse_id,))


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
        f"AND year >= ? AND channel_id IN ({','.join('?' * len(ids))}) GROUP BY channel_id",
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
