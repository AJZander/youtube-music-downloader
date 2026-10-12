"""YouTube Music artist catalogue: an artist's complete discography, with
tracklists, to browse and choose from before anything is queued.

`get_artist` returns the artist *page*, which is a stack of carousels holding
at most ten albums and ten singles — which is why a prolific artist used to
show "about ten albums". The complete lists sit behind each carousel's "More"
link, an MPAD… browse page, and `get_artist_albums` reads those in full.

All of this shares an IP address with the downloader, so it is built to be
cheap:

- One shared YTMusic client. A new client fetches the music.youtube.com
  homepage for a visitor id before its first real request, so constructing
  one per call doubled the traffic.
- Everything is cached in `catalog_cache`. A discography is reused for hours
  and a tracklist for weeks, so reopening an artist costs nothing — and the
  worker expands an album the browser has already shown without asking
  YouTube again.
- Requests go through `Pacer`: short gaps when someone is waiting on the
  answer, long ones for background tracklist prefetch, and an hourly budget
  over both. It honours the download governor's backoff in both directions —
  a 429 here pauses downloads and a throttled download pauses browsing —
  because YouTube rate-limits the address, not the endpoint.
"""
import asyncio
import collections
import json
import logging
import random
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from .config import settings
from .db import db
from .engine.governor import governor
from .engine.matcher import normalize
from .events import bus
from . import ownership
from .library import strict_key
from .ownership import LIVE_RE as _LIVE_RE
from .models import ErrorClass

log = logging.getLogger("mdl.catalog")

INTERACTIVE = "interactive"   # someone is waiting on the answer
BACKGROUND = "background"     # prefetch and the worker: nobody is

ARTIST_TTL = 12 * 3600          # new releases show up within a day
PARTIAL_TTL = 3600              # ...but retry an incomplete list sooner
ALBUM_TTL = 30 * 86400          # tracklists essentially never change
RESOLVE_TTL = 30 * 86400        # @handle -> channel id
SEARCH_TTL = 86400
CACHE_RETENTION = 60 * 86400
# The worker downloads from these ids, so it wants a fresher copy than the
# browser does. Videos do get pulled; a week-old tracklist is close enough.
WORKER_ALBUM_MAX_AGE = 7 * 86400
# A release whose tracklist would not load is not retried by every refresh
# of the view — the browser refreshes on each prefetch step.
FAILED_RETRY_AFTER = 15 * 60
REQUEST_TIMEOUT = 60

THROTTLED = (ErrorClass.RATE_LIMITED, ErrorClass.FORBIDDEN, ErrorClass.BOT_FLAGGED)

# The albums carousel and the singles carousel. "videos" is deliberately not
# here: music videos are what the downloader exists to avoid.
SECTIONS = ("albums", "singles")
ALBUM_CATEGORIES = ("album", "live", "compilation", "soundtrack")
# Past this many tracks an "album" is a box set; it is filed with the
# compilations rather than in among the studio albums.
BOX_SET_TRACKS = 40


class CatalogError(Exception):
    def __init__(self, message: str, status: int = 502,
                 error_class: ErrorClass = ErrorClass.TRANSIENT) -> None:
        super().__init__(message)
        self.status = status
        self.error_class = error_class


def _human(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 90:
        return f"{seconds}s"
    return f"{round(seconds / 60)} min"


# ------------------------------------------------------------------ pacing
class Pacer:
    """Spacing for YouTube Music metadata requests.

    Not the download governor: its hourly caps are about downloads, and a
    browser that stops working because 60 tracks went through this hour is
    broken. What the two do share is the backoff — when YouTube pushes back,
    everything stops.

    Requests run one at a time and each waits a gap after the previous one
    finished. Background work yields to anything interactive, so expanding an
    album mid-prefetch answers in about a second instead of queueing behind
    sixty others. Someone waiting is told about a backoff or a spent budget
    straight away; background work just waits it out."""

    def __init__(self) -> None:
        self._recent: deque[float] = deque()
        self._last_end = 0.0
        self._active = 0
        self._interactive_waiting = 0
        self._gap = {INTERACTIVE: 0.0, BACKGROUND: 0.0}
        self.waiting: str | None = None   # why background work is parked

    def _prune(self, now: float) -> None:
        while self._recent and self._recent[0] < now - 3600:
            self._recent.popleft()

    def snapshot(self) -> dict:
        self._prune(time.monotonic())
        return {"requests_last_hour": len(self._recent),
                "budget_per_hour": settings.catalog_requests_per_hour or None,
                "backoff_sec": round(governor.backoff_remaining()),
                "waiting": self.waiting}

    async def acquire(self, priority: str = INTERACTIVE) -> None:
        interactive = priority == INTERACTIVE
        if interactive:
            self._interactive_waiting += 1
        try:
            while True:
                backoff = governor.backoff_remaining()
                if backoff > 0:
                    if interactive:
                        raise CatalogError(
                            "YouTube asked us to slow down — browsing resumes in "
                            f"{_human(backoff)}", 503, ErrorClass.RATE_LIMITED)
                    self.waiting = "backoff"
                    await asyncio.sleep(min(backoff, 30))
                    continue
                now = time.monotonic()
                self._prune(now)
                budget = settings.catalog_requests_per_hour
                if budget > 0 and len(self._recent) >= budget:
                    wait = self._recent[0] + 3600 - now
                    if interactive:
                        raise CatalogError(
                            f"This hour's browsing budget ({budget} requests) is used up "
                            f"— try again in {_human(wait)}", 503)
                    self.waiting = "budget"
                    await asyncio.sleep(min(max(wait, 1), 30))
                    continue
                if self._active or (not interactive and self._interactive_waiting):
                    await asyncio.sleep(0.2)
                    continue
                ready = self._last_end + self._gap[priority]
                if now < ready:
                    # Short naps so background work notices an interactive
                    # request arriving partway through its gap.
                    await asyncio.sleep(min(ready - now, 0.5))
                    continue
                # Nothing above awaited since the checks, so claiming the
                # slot here cannot race another waiter.
                self._active += 1
                self._recent.append(now)
                if not interactive:
                    self.waiting = None
                return
        finally:
            if interactive:
                self._interactive_waiting -= 1

    def release(self) -> None:
        self._active = max(0, self._active - 1)
        self._last_end = time.monotonic()
        self._gap = {
            INTERACTIVE: random.uniform(0.5, 1.2),
            BACKGROUND: random.uniform(settings.catalog_prefetch_delay_min,
                                       settings.catalog_prefetch_delay_max),
        }


pacer = Pacer()

# ytmusicapi is synchronous and its client is not meant to be shared across
# threads, so every call runs on this one thread. The pacer serializes them
# anyway; this just makes it structural.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ytmusic")
_client = None
# The status of the last HTTP response. ytmusicapi parses the body as JSON
# before it looks at the status, so a 429 served as an HTML "sorry" page
# surfaces as a JSON error — this is how it is still recognised as a 429.
# Calls are serialized on _executor, so one slot is enough.
_last_status: dict[str, int | None] = {"code": None}


def _note_status(response, *args, **kwargs) -> None:
    _last_status["code"] = response.status_code


def client():
    global _client
    if _client is None:
        from ytmusicapi import YTMusic
        _client = YTMusic()
        session = getattr(_client, "_session", None)
        if session is not None:
            session.hooks["response"].append(_note_status)
    return _client


_HTTP_STATUS_RE = re.compile(r"HTTP (\d{3})")


def classify(exc: Exception, http_status: int | None = None) -> CatalogError:
    """Typed error for a failed request, from the response status when there
    was one, else from ytmusicapi's message. Throttling is reported to the
    governor — the same address downloads, and it should back off too."""
    message = str(exc)
    m = _HTTP_STATUS_RE.search(message)
    status = http_status if http_status and http_status >= 400 else (
        int(m.group(1)) if m else None)
    if status == 429:
        governor.report_error(ErrorClass.RATE_LIMITED)
        return CatalogError(
            "YouTube Music is rate-limiting us — browsing and downloads are "
            "paused for a few minutes", 503, ErrorClass.RATE_LIMITED)
    if status == 403:
        governor.report_error(ErrorClass.FORBIDDEN)
        return CatalogError("YouTube Music refused the request (HTTP 403) — backing off",
                            503, ErrorClass.FORBIDDEN)
    if status in (400, 404):
        return CatalogError("YouTube Music has nothing by that id", 404,
                            ErrorClass.PERMANENT)
    if status:
        return CatalogError(f"YouTube Music returned HTTP {status}", 502)
    try:
        import requests
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return CatalogError("Could not reach YouTube Music", 502)
    except ImportError:
        pass
    if isinstance(exc, (KeyError, IndexError, TypeError, AttributeError)):
        # ytmusicapi puts the whole response in the message; the start says enough.
        log.warning("ytmusicapi could not parse a response: %s", message[:300])
        return CatalogError(
            "Could not read YouTube Music's response — either this is not a music "
            "artist/release, or the page layout changed and ytmusicapi needs an update",
            502)
    return CatalogError(f"YouTube Music request failed: {message[:200]}", 502)


async def call(method: str, *args, priority: str = INTERACTIVE, **kwargs):
    """The one door to YouTube Music: paced, on the ytmusic thread, typed errors."""
    await pacer.acquire(priority)
    loop = asyncio.get_running_loop()
    _last_status["code"] = None
    try:
        fn = getattr(client(), method)
        return await asyncio.wait_for(
            loop.run_in_executor(_executor, partial(fn, *args, **kwargs)),
            timeout=REQUEST_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise CatalogError("YouTube Music did not answer in time", 504) from exc
    except CatalogError:
        raise
    except Exception as exc:
        raise classify(exc, _last_status["code"]) from exc
    finally:
        pacer.release()


# ------------------------------------------------------------------ cache
async def _cache_get(key: str, max_age: float) -> dict | None:
    row = await db.fetchone(
        "SELECT data, fetched_at FROM catalog_cache WHERE key = ?", (key,))
    if row and time.time() - row["fetched_at"] <= max_age:
        return json.loads(row["data"])
    return None


async def _cache_many(keys: list[str], max_age: float) -> dict[str, dict]:
    out: dict[str, dict] = {}
    cutoff = time.time() - max_age
    for i in range(0, len(keys), 500):
        chunk = keys[i:i + 500]
        rows = await db.fetchall(
            f"SELECT key, data FROM catalog_cache WHERE fetched_at >= ? "
            f"AND key IN ({','.join('?' * len(chunk))})", (cutoff, *chunk))
        for r in rows:
            out[r["key"]] = json.loads(r["data"])
    return out


async def _cache_put(key: str, data: dict) -> None:
    await db.execute(
        "INSERT INTO catalog_cache (key, data, fetched_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET data = excluded.data, "
        "fetched_at = excluded.fetched_at",
        (key, json.dumps(data, separators=(",", ":")), time.time()))


async def prune_cache() -> None:
    await db.execute("DELETE FROM catalog_cache WHERE fetched_at < ?",
                     (time.time() - CACHE_RETENTION,))


# ------------------------------------------------------------------ parsing
def _thumb(thumbnails, want: int) -> str | None:
    """The smallest thumbnail at least `want` px wide, else the largest."""
    thumbs = [t for t in (thumbnails or []) if t.get("url")]
    if not thumbs:
        return None
    thumbs.sort(key=lambda t: t.get("width") or 0)
    for t in thumbs:
        if (t.get("width") or 0) >= want:
            return t["url"]
    return thumbs[-1]["url"]


def release_from_listing(item: dict, section: str) -> dict | None:
    browse_id = item.get("browseId")
    if not browse_id:
        return None
    kind = (item.get("type") or "").strip().lower()
    if kind not in ("album", "ep", "single"):
        # Untyped cards take their section's type.
        kind = "single" if section == "singles" else "album"
    return {
        "browse_id": browse_id,
        "title": item.get("title") or "",
        "type": kind,
        "year": str(item.get("year") or "") or None,
        "explicit": bool(item.get("isExplicit")),
        "thumbnail": _thumb(item.get("thumbnails"), 226),
        "url": f"https://music.youtube.com/browse/{browse_id}",
    }


def album_from_raw(raw: dict, browse_id: str) -> dict:
    """The cached shape of one release: what the browser shows and what the
    worker downloads from, minus ytmusicapi's feedback tokens and thumbnails."""
    tracks = []
    # Numbered over every row, playable or not, the way the album numbers them.
    for i, t in enumerate(raw.get("tracks") or [], start=1):
        tracks.append({
            "video_id": t.get("videoId"),
            "title": t.get("title") or "",
            "artists": [a["name"] for a in (t.get("artists") or []) if a.get("name")],
            "track_number": t.get("trackNumber") or i,
            "duration_sec": t.get("duration_seconds"),
            "explicit": bool(t.get("isExplicit")),
            "available": bool(t.get("videoId")) and t.get("isAvailable") is not False,
        })
    artists = raw.get("artists") or []
    return {
        "browse_id": browse_id,
        "title": raw.get("title") or "",
        "type": (raw.get("type") or "Album").lower(),
        "year": str(raw.get("year") or "") or None,
        "artists": [a["name"] for a in artists if a.get("name")],
        "artist_ids": [a["id"] for a in artists if a.get("id")],
        "thumbnail": _thumb(raw.get("thumbnails"), 544),
        "duration_sec": raw.get("duration_seconds"),
        "audio_playlist_id": raw.get("audioPlaylistId"),
        "explicit": bool(raw.get("isExplicit")),
        "tracks": tracks,
    }


def track_artist(track: dict, album_artist: str | None) -> str | None:
    """The artist string a track is downloaded, tagged and deduplicated under."""
    return ", ".join(track.get("artists") or []) or album_artist


def album_track_rows(album: dict, album_artist: str | None) -> list[dict]:
    """Worker track rows for a cached album. `source_video_id` is the id the
    browser showed; provider_track_id may later be swapped for the album-audio
    version, and a partial selection is matched against the original."""
    year = album.get("year") or ""
    rows = []
    for t in album.get("tracks") or []:
        if not t.get("video_id"):
            continue
        rows.append({
            "provider": "youtube_music",
            "provider_track_id": t["video_id"],
            "source_video_id": t["video_id"],
            "source_url": f"https://music.youtube.com/watch?v={t['video_id']}",
            "title": t.get("title"),
            "artist": track_artist(t, album_artist),
            "album": album.get("title"), "album_artist": album_artist,
            "track_number": t.get("track_number"),
            "release_date": year,
            "duration_sec": t.get("duration_sec"),
        })
    return rows


# ------------------------------------------------------------------ classifying
# Release categories are a guess from the title. YouTube Music files live
# albums, compilations and soundtracks all as "Album", in one flat grid.
_LIVE_TRACK_RE = re.compile(r"[\(\[\-–—]\s*live\b|\blive\s+(?:at|in|from)\b", re.I)
_COMPILATION_RE = re.compile(
    r"\b(?:greatest\s+hits|best\s+of|the\s+very\s+best|the\s+essential|essentials"
    r"|anthology|the\s+(?:singles|collection|hits)|collected|retrospective"
    r"|definitive|b-?sides|rarities|number\s+ones|gold(?:en)?\s+hits)\b", re.I)
_SOUNDTRACK_RE = re.compile(
    r"\b(?:soundtrack|motion\s+picture|original\s+score|ost"
    r"|music\s+from\s+(?:the\s+)?(?:film|motion|movie|series|tv)"
    r"|original\s+(?:cast|television))\b", re.I)
_EDITION_RE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(?:deluxe|edition|expanded|remaster(?:ed)?|anniversary"
    r"|bonus|special|reissue|version|super|collector'?s?)\b[^\)\]]*[\)\]]"
    r"|\s*[-–—:]\s*(?:the\s+)?(?:deluxe|expanded|remastered|special)\b.*$",
    re.I)


def classify_release(title: str, kind: str,
                     track_titles: list[str] | None = None) -> str:
    """album | live | compilation | soundtrack | ep | single."""
    if kind in ("ep", "single"):
        return kind
    if _SOUNDTRACK_RE.search(title or ""):
        return "soundtrack"
    if _COMPILATION_RE.search(title or ""):
        return "compilation"
    if _LIVE_RE.search(title or ""):
        return "live"
    # A live album does not always say so on the cover, but its tracks do.
    if track_titles and len(track_titles) >= 3:
        live = sum(1 for t in track_titles if _LIVE_TRACK_RE.search(t or ""))
        if live / len(track_titles) >= 0.6:
            return "live"
    if track_titles and len(track_titles) >= BOX_SET_TRACKS:
        return "compilation"
    return "album"


def base_title(title: str) -> str:
    """`Seventy (Deluxe Edition)` and `Seventy` are one record in two editions."""
    return strict_key(_EDITION_RE.sub("", title or "")) or strict_key(title)


def _family(category: str) -> str:
    return "single" if category in ("ep", "single") else "album"


def relate(releases: list[dict]) -> None:
    """Annotate releases with how they overlap, in place.

    versions      — same record in other editions (by title)
    contained_in  — releases holding every track of this one (by tracklist)

    Tracks are compared by title, as the dedup gate does. Live albums are
    kept out of studio comparisons: `To Her Door` live is not `To Her Door`.
    Equal tracklists contain each other, so the tie goes to the release YouTube
    lists first — it marks the later one, never both."""
    by_base: dict[tuple[str, str], list[dict]] = {}
    for r in releases:
        r["versions"] = []
        r["contained_in"] = []
        by_base.setdefault((_family(r["category"]), base_title(r["title"])), []).append(r)
    for group in by_base.values():
        if len(group) > 1:
            for r in group:
                r["versions"] = [o["browse_id"] for o in group if o is not r]

    keyed = []
    for index, r in enumerate(releases):
        if r.get("tracks"):
            keys = {normalize(t["title"]) for t in r["tracks"] if t.get("title")}
            keys.discard("")
            if keys:
                keyed.append((index, r, keys))
    for i, r, keys in keyed:
        for j, other, other_keys in keyed:
            if other is r or other["category"] not in ALBUM_CATEGORIES:
                continue
            if (other["category"] == "live") != (r["category"] == "live"):
                continue
            if not keys <= other_keys:
                continue
            if len(keys) == len(other_keys) and j > i:
                continue
            r["contained_in"].append(other["browse_id"])


# ------------------------------------------------------------------ library
async def _index_for(albums: list[dict], artist_names) -> ownership.LibraryIndex:
    """The slice of the library these releases could already be in."""
    credit_keys, video_ids = set(), set()
    for album in albums:
        album_artist = ", ".join(album.get("artists") or []) or None
        for t in album.get("tracks") or []:
            video_ids.add(t.get("video_id"))
            credit_keys.add(track_artist(t, album_artist))
    return await ownership.load_index(credit_keys, artist_names, video_ids)


def _annotate_album(album: dict, index: ownership.LibraryIndex,
                    fallback_artist: str | None, category: str) -> dict:
    """Library state per track (see ownership.LibraryIndex.lookup) and for
    the release: how much of it is here, and what is left to download."""
    album_artist = ", ".join(album.get("artists") or []) or fallback_artist
    tracks = [{**t, **index.lookup(
        title=t.get("title"), credit=track_artist(t, album_artist),
        album=album.get("title"), video_id=t.get("video_id"),
        length=t.get("duration_sec"), live_album=category == "live")}
        for t in album.get("tracks") or []]
    owned = [t for t in tracks if t["library"] == "have"]
    folders = collections.Counter(t["library_folder"] for t in owned)
    return {
        **album,
        "tracks": tracks,
        "track_count": len(tracks),
        "duration_sec": album.get("duration_sec")
        or sum(t.get("duration_sec") or 0 for t in tracks) or None,
        "library": {
            "have": len(owned),
            "elsewhere": sum(1 for t in tracks if t["library"] == "elsewhere"),
            "total": len(tracks),
            # Playable tracks this album lacks: what would download.
            "new": sum(1 for t in tracks if t["available"] and t["library"] != "have"),
            "folders": [{"folder": f, "tracks": n} for f, n in folders.most_common(3)],
        },
    }


# ------------------------------------------------------------------ fetching
_MPAD_RE = re.compile(r"MPAD(UC[A-Za-z0-9_-]{22})")
_CHANNEL_RE = re.compile(r"(?<![A-Za-z0-9_-])(UC[A-Za-z0-9_-]{22})(?![A-Za-z0-9_-])")
_ALBUM_RE = re.compile(r"(MPREb_[A-Za-z0-9_-]+)")


def _artist_url(ref: str) -> str:
    """A pasted reference as a URL YouTube Music can resolve."""
    if ref.startswith("@"):
        return f"https://music.youtube.com/{ref}"
    return re.sub(r"^https?://(?:www\.|m\.)?youtube\.com/", "https://music.youtube.com/", ref)


async def resolve_channel(ref: str, *, priority: str = INTERACTIVE) -> str:
    """Channel id for a channel URL, an @handle, a bare UC… id, or an album
    link (whose first credited artist is the answer)."""
    ref = (ref or "").strip()
    m = _MPAD_RE.search(ref) or _CHANNEL_RE.search(ref)
    if m:
        return m.group(1)
    m = _ALBUM_RE.search(ref)
    if m:
        found = await album(m.group(1), priority=priority)
        if found["artist_ids"]:
            return found["artist_ids"][0]
        raise CatalogError("That album does not credit an artist", 404, ErrorClass.PERMANENT)
    if not (ref.startswith("@") or re.match(r"https?://", ref, re.I)):
        raise CatalogError("Paste an artist link or @handle, or search by name", 422,
                           ErrorClass.PERMANENT)
    url = _artist_url(ref)
    key = f"resolve:{url.casefold()}"
    cached = await _cache_get(key, RESOLVE_TTL)
    if cached:
        return cached["channel_id"]
    response = await call("_send_request", "navigation/resolve_url", {"url": url},
                          priority=priority)
    browse_id = (((response or {}).get("endpoint") or {})
                 .get("browseEndpoint") or {}).get("browseId") or ""
    if not browse_id.startswith("UC"):
        raise CatalogError("That link is not a YouTube Music artist", 404,
                           ErrorClass.PERMANENT)
    await _cache_put(key, {"channel_id": browse_id})
    return browse_id


def _runs_text(column: dict) -> str:
    text = (column.get("musicResponsiveListItemFlexColumnRenderer") or {}).get("text") or {}
    return "".join(r.get("text", "") for r in text.get("runs") or [])


def artist_results(raw: dict) -> list[dict]:
    """Artist rows from a raw search response.

    ytmusicapi's parser drops each row's subtitle — "210K monthly audience",
    "1.26K subscribers" — which is the only thing telling four artists called
    Paul Kelly apart. Returns [] if the layout is not what this expects."""
    out: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, list):
            for v in node:
                walk(v)
            return
        if not isinstance(node, dict):
            return
        item = node.get("musicResponsiveListItemRenderer")
        if item is None:
            for v in node.values():
                walk(v)
            return
        channel_id = (((item.get("navigationEndpoint") or {}).get("browseEndpoint") or {})
                      .get("browseId") or "")
        columns = [_runs_text(c) for c in item.get("flexColumns") or []]
        if not channel_id.startswith("UC") or not columns or not columns[0]:
            return
        subtitle = columns[1] if len(columns) > 1 else ""
        thumbs = (((item.get("thumbnail") or {}).get("musicThumbnailRenderer") or {})
                  .get("thumbnail") or {}).get("thumbnails")
        out.append({
            "channel_id": channel_id,
            "name": columns[0],
            "thumbnail": _thumb(thumbs, 120),
            "audience": subtitle.split("•", 1)[1].strip() if "•" in subtitle else None,
        })

    walk(raw)
    return out


async def search_artists(q: str, *, priority: str = INTERACTIVE) -> list[dict]:
    q = " ".join((q or "").split())
    if len(q) < 2:
        raise CatalogError("Type at least two characters", 422, ErrorClass.PERMANENT)
    key = f"search:{q.casefold()}"
    cached = await _cache_get(key, SEARCH_TTL)
    if cached:
        return cached["artists"]
    from ytmusicapi.parsers.search import get_search_params
    raw = await call("_send_request", "search",
                     {"query": q, "params": get_search_params("artists", None, False)},
                     priority=priority)
    artists = artist_results(raw)
    if not artists:
        # Layout drift (or genuinely nobody): ytmusicapi's own parser decides.
        results = await call("search", q, filter="artists", limit=10, priority=priority)
        artists = [{
            "channel_id": r["browseId"],
            "name": r.get("artist") or r.get("title") or "",
            "thumbnail": _thumb(r.get("thumbnails"), 120),
            "audience": r.get("subscribers"),
        } for r in results or [] if (r.get("browseId") or "").startswith("UC")]
    await _cache_put(key, {"artists": artists})
    return artists


async def album(browse_id: str, *, priority: str = INTERACTIVE,
                max_age: float = ALBUM_TTL, refresh: bool = False) -> dict:
    key = f"album:{browse_id}"
    if not refresh:
        cached = await _cache_get(key, max_age)
        if cached:
            return cached
    raw = await call("get_album", browse_id, priority=priority)
    view = album_from_raw(raw, browse_id)
    await _cache_put(key, view)
    _failed.pop(browse_id, None)
    return view


async def discography(ref: str, *, refresh: bool = False,
                      priority: str = INTERACTIVE) -> dict:
    """The artist and every album, single and EP they list — no tracklists."""
    channel_id = await resolve_channel(ref, priority=priority)
    key = f"artist:{channel_id}"
    if not refresh:
        cached = await _cache_get(key, ARTIST_TTL)
        if cached and not (cached.get("partial")
                           and time.time() - cached["fetched_at"] > PARTIAL_TTL):
            return cached

    try:
        raw = await call("get_artist", channel_id, priority=priority)
    except CatalogError as exc:
        # get_artist on an ordinary channel fails to parse rather than 404ing.
        if isinstance(exc.__cause__, (KeyError, IndexError, TypeError)):
            raise CatalogError("That channel has no YouTube Music artist page", 404,
                               ErrorClass.PERMANENT) from exc
        raise
    if not raw.get("name"):
        raise CatalogError("That channel is not a YouTube Music artist", 404,
                           ErrorClass.PERMANENT)
    releases, seen, partial = [], set(), False
    for section in SECTIONS:
        block = raw.get(section) or {}
        items = block.get("results") or []
        # A carousel with a "More" link is truncated; follow it for the rest.
        if block.get("browseId") and block.get("params"):
            try:
                items = await call("get_artist_albums", block["browseId"],
                                   block["params"], limit=None, priority=priority)
            except CatalogError as exc:
                if exc.error_class in THROTTLED:
                    raise
                log.warning("artist %s: full %s list failed, showing the first %d: %s",
                            channel_id, section, len(items), exc)
                partial = True
        for item in items:
            release = release_from_listing(item, section)
            if release and release["browse_id"] not in seen:
                seen.add(release["browse_id"])
                releases.append(release)

    # One artist can answer to two channel ids — @paulkelly is the official
    # channel, search returns the music artist — and the "More" links carry
    # the music artist's. Use that, so both ways in are one artist.
    canonical = channel_id
    for section in SECTIONS:
        m = _MPAD_RE.match((raw.get(section) or {}).get("browseId") or "")
        if m:
            canonical = m.group(1)
            break

    banner = raw.get("thumbnails") or []
    widest = max(banner, key=lambda t: t.get("width") or 0, default={})
    doc = {
        "channel_id": canonical,
        "name": raw.get("name"),
        "thumbnail": _thumb(banner, 1080),
        "thumbnail_wide": bool(widest.get("height"))
        and widest.get("width", 0) / widest["height"] > 1.4,
        "subscribers": raw.get("subscribers"),
        "monthly_listeners": raw.get("monthlyListeners"),
        "description": (raw.get("description") or "")[:1200],
        "related": [{
            "channel_id": r["browseId"],
            "name": r.get("title") or "",
            "thumbnail": _thumb(r.get("thumbnails"), 120),
            "subscribers": r.get("subscribers"),
        } for r in ((raw.get("related") or {}).get("results") or [])
            if (r.get("browseId") or "").startswith("UC")][:12],
        "releases": releases,
        "partial": partial,
        "fetched_at": time.time(),
    }
    await _cache_put(key, doc)
    if canonical != channel_id:
        await _cache_put(f"artist:{canonical}", doc)
    log.info("catalogue %s (%s): %d releases%s", doc["name"], canonical,
             len(releases), " (partial)" if partial else "")
    return doc


# ------------------------------------------------------------------ prefetch
class _Prefetch:
    def __init__(self, channel_id: str, browse_ids: list[str]) -> None:
        self.channel_id = channel_id
        self.browse_ids = browse_ids
        self.total = len(browse_ids)
        self.done = 0
        self.failed = 0
        self.finished = False
        self.task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return not self.finished and self.task is not None and not self.task.done()

    def snapshot(self) -> dict:
        return {"channel_id": self.channel_id, "total": self.total,
                "done": self.done, "failed": self.failed, "running": self.running,
                "waiting": pacer.waiting if self.running else None}


_prefetch: _Prefetch | None = None
_failed: dict[str, float] = {}   # browse_id -> when its tracklist last failed


def _publish(state: _Prefetch, browse_id: str | None = None) -> None:
    bus.publish("catalog.progress", {**state.snapshot(), "browse_id": browse_id})


async def _run_prefetch(state: _Prefetch) -> None:
    try:
        for browse_id in state.browse_ids:
            try:
                await album(browse_id, priority=BACKGROUND)
                state.done += 1
            except CatalogError as exc:
                state.failed += 1
                _failed[browse_id] = time.monotonic()
                log.warning("tracklist %s failed: %s", browse_id, exc)
            _publish(state, browse_id)
    finally:
        state.finished = True
        _publish(state)


def ensure_prefetch(channel_id: str, missing: list[str]) -> dict:
    """Fetch the tracklists the view is missing, in the background, in order.
    One artist at a time: opening another artist means the user moved on, so
    the old one stops — what it fetched stays cached."""
    global _prefetch
    if _prefetch and _prefetch.channel_id == channel_id and _prefetch.running:
        return _prefetch.snapshot()
    now = time.monotonic()
    todo = [b for b in missing if now - _failed.get(b, -1e9) > FAILED_RETRY_AFTER]
    if not todo:
        if _prefetch and _prefetch.channel_id == channel_id:
            return _prefetch.snapshot()
        return {"channel_id": channel_id, "total": 0, "done": 0, "failed": 0,
                "running": False, "waiting": None}
    if _prefetch and _prefetch.running:
        _prefetch.task.cancel()
    _prefetch = _Prefetch(channel_id, todo)
    _prefetch.task = asyncio.create_task(_run_prefetch(_prefetch),
                                         name=f"catalog-prefetch-{channel_id}")
    return _prefetch.snapshot()


# ------------------------------------------------------------------ views
async def artist_view(ref: str, *, refresh: bool = False) -> dict:
    """Everything the browser draws for one artist, from cache where possible:
    releases with whatever tracklists are loaded, library state, overlaps."""
    doc = await discography(ref, refresh=refresh)
    if refresh:
        for r in doc["releases"]:
            _failed.pop(r["browse_id"], None)
    cached = await _cache_many([f"album:{r['browse_id']}" for r in doc["releases"]],
                               ALBUM_TTL)
    albums = {k.removeprefix("album:"): v for k, v in cached.items()}
    index = await _index_for(list(albums.values()), [doc["name"]])

    releases = []
    for r in doc["releases"]:
        loaded = albums.get(r["browse_id"])
        out = {**r, "tracks": None, "track_count": None, "duration_sec": None,
               "artists": None, "library": None}
        out["category"] = classify_release(
            r["title"], r["type"], [t["title"] for t in (loaded or {}).get("tracks") or []])
        if loaded:
            annotated = _annotate_album(loaded, index, doc["name"], out["category"])
            out.update({k: annotated[k] for k in (
                "tracks", "track_count", "duration_sec", "artists", "library")})
        releases.append(out)
    relate(releases)

    missing = [r["browse_id"] for r in releases if r["tracks"] is None]
    return {
        "artist": {k: doc.get(k) for k in (
            "channel_id", "name", "thumbnail", "thumbnail_wide", "subscribers",
            "monthly_listeners", "description", "related")},
        "releases": releases,
        "partial": doc.get("partial", False),
        "fetched_at": doc["fetched_at"],
        "prefetch": ensure_prefetch(doc["channel_id"], missing),
        "pacer": pacer.snapshot(),
    }


async def album_view(browse_id: str, *, refresh: bool = False) -> dict:
    """One release on demand — an album expanded before prefetch reached it."""
    loaded = await album(browse_id, refresh=refresh)
    fallback = ", ".join(loaded.get("artists") or []) or None
    index = await _index_for([loaded], loaded.get("artists") or [])
    return _annotate_album(loaded, index, fallback, _category(loaded))


def _category(album: dict) -> str:
    return classify_release(album.get("title") or "", album.get("type") or "album",
                            [t["title"] for t in album.get("tracks") or []])


async def plan_queue(picks: list[dict], *, include_owned: bool = False,
                     force_ticked: bool = True) -> list[dict]:
    """What each picked release will really download: the ticked tracks (all
    of them, for a whole release) minus what the library already has on that
    album. Worked out here, at queue time, so owned tracks neither download
    nor pile up in the Skipped list; the dedup gate asks the same question
    again at download time, for anything that landed in between.

    A whole release leaves its owned tracks out. Tracks ticked one by one are
    taken as ticked — ticking an owned track in the browser is how you say
    "this one anyway", so those come back as force_ids, to skip the dedup gate
    as well. force_ticked=False (picks made somewhere that never showed the
    library, like a chart) leaves owned tracks out of those too.

    picks: {browse_id, video_ids | None, artist}. Returns, per pick, the
    video ids to keep (None = the whole release), the owned ones among them
    to force, how many tracks that is, and how many were left out as owned —
    or an error."""
    albums: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for browse_id in dict.fromkeys(pick["browse_id"] for pick in picks):
        try:
            albums[browse_id] = await album(browse_id)
        except CatalogError as exc:
            errors[browse_id] = str(exc)
    index = None
    if not include_owned:
        names = {pick.get("artist") for pick in picks}
        names |= {a["artists"][0] for a in albums.values() if a.get("artists")}
        index = await _index_for(list(albums.values()), [n for n in names if n])

    plans = []
    for pick in picks:
        loaded = albums.get(pick["browse_id"])
        if loaded is None:
            plans.append({"browse_id": pick["browse_id"], "error": errors[pick["browse_id"]]})
            continue
        if index is not None:
            loaded = _annotate_album(loaded, index, pick.get("artist"), _category(loaded))
        playable = [t for t in loaded["tracks"] if t["available"]]
        explicit = pick.get("video_ids") is not None and force_ticked
        ticked = set(pick.get("video_ids") or [])
        wanted = [t for t in playable
                  if pick.get("video_ids") is None or t["video_id"] in ticked]
        owned = [t["video_id"] for t in wanted if t.get("library") == "have"]
        keep = [t["video_id"] for t in wanted
                if explicit or t.get("library") != "have"]
        plans.append({
            "browse_id": pick["browse_id"],
            "video_ids": None if len(keep) == len(playable) else keep,
            "force_ids": owned if explicit else [],
            "tracks": len(keep),
            "left_out": len(wanted) - len(keep),
            "error": None,
        })
    return plans
