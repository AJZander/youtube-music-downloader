"""MusicBrainz web service client.

The rules are strict and enforced per address: at most one request a second
(above it *every* request gets a 503), and a User-Agent naming the
application and a way to contact its operator. So there is one client, one
pacing lock for the whole process, and no requests at all until
MUSICBRAINZ_CONTACT is set.

Everything fetched is cached: a release's tracklist almost never changes, so
a re-check months later is free; searches are cached for a day.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from ..config import settings
from . import cache

log = logging.getLogger("mdl.musicbrainz")

VARIOUS_ARTISTS = "89ad4ac3-39f7-470e-963a-56509c546377"
RELEASE_INC = "recordings+artist-credits+labels+release-groups+isrcs+genres+tags+media"
RELEASE_TTL = 120 * 86400
GROUP_TTL = 60 * 86400
ARTIST_TTL = 60 * 86400
SEARCH_TTL = 14 * 86400     # under the 30-day retry of unidentified albums
ISRC_TTL = 30 * 86400
GENRES_TTL = 30 * 86400

_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


class MusicBrainzError(Exception):
    pass


class MusicBrainzOff(MusicBrainzError):
    """Lookups are disabled (no contact configured)."""


def enabled() -> bool:
    return bool(settings.musicbrainz_contact.strip())


def lucene(text: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", text or "").strip()


class Client:
    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._last = 0.0
        self.requests = 0

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(30, connect=10),
                headers={"User-Agent": f"MusicDownloader/2.0 ( {settings.musicbrainz_contact.strip()} )",
                         "Accept": "application/json"})
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def get(self, path: str, params: dict | None = None, *, ttl: float,
                  cache_key: str | None = None):
        key = cache_key or f"mb:{path}?{sorted((params or {}).items())}"
        cached = await cache.get(key, ttl)
        if cached is not None:
            return cached
        if not enabled():
            raise MusicBrainzOff("MUSICBRAINZ_CONTACT is not set")
        data = await self._fetch(path, params)
        await cache.put(key, data)
        return data

    async def _slot(self) -> None:
        """Wait for the next request slot. Slots are spaced by their *start*,
        so a slow response doesn't stretch the gap: one request a second,
        never more (MusicBrainz's limit), and no less than that either."""
        async with self._lock:
            wait = self._last + settings.musicbrainz_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    def _back_off(self, seconds: float) -> None:
        """Hold every caller back (MusicBrainz said slow down)."""
        self._last = max(self._last, time.monotonic() + seconds - settings.musicbrainz_interval)

    async def _fetch(self, path: str, params: dict | None):
        url = f"{settings.musicbrainz_url.rstrip('/')}/{path.lstrip('/')}"
        query = {**(params or {}), "fmt": "json"}
        delay = 2.0
        for attempt in range(5):
            await self._slot()
            try:
                r = await self._client().get(url, params=query)
            except httpx.HTTPError as exc:
                log.info("musicbrainz %s: %s (retrying)", path, exc)
                self._back_off(delay)
                delay *= 2
                continue
            self.requests += 1
            if r.status_code in (503, 429):
                log.info("musicbrainz asked us to slow down (%s); backing off %.0fs",
                         r.status_code, delay)
                self._back_off(delay)
                delay *= 2
                continue
            if r.status_code == 404:
                return None
            if r.status_code >= 400:
                raise MusicBrainzError(f"{path}: HTTP {r.status_code}")
            return r.json()
        raise MusicBrainzError(f"{path}: gave up after retries")


client = Client()


# ------------------------------------------------------------------ lookups
async def release(mbid: str) -> dict | None:
    return await client.get(f"release/{mbid}", {"inc": RELEASE_INC}, ttl=RELEASE_TTL,
                            cache_key=f"mb:release:{mbid}")


async def release_group(mbid: str) -> dict | None:
    return await client.get(f"release-group/{mbid}", {"inc": "genres+tags+artist-credits"},
                            ttl=GROUP_TTL, cache_key=f"mb:rg:{mbid}")


async def artist(mbid: str) -> dict | None:
    return await client.get(f"artist/{mbid}", {"inc": "genres+tags+url-rels"},
                            ttl=ARTIST_TTL, cache_key=f"mb:artist:{mbid}")


async def isrc_releases(isrc: str) -> list[str]:
    data = await client.get(f"isrc/{isrc}", {"inc": "releases"}, ttl=ISRC_TTL,
                            cache_key=f"mb:isrc:{isrc}")
    ids: list[str] = []
    for recording in (data or {}).get("recordings") or []:
        for rel in recording.get("releases") or []:
            if rel.get("id") and rel["id"] not in ids:
                ids.append(rel["id"])
    return ids


async def search_releases(album: str, artist_name: str | None = None,
                          tracks: int | None = None, limit: int = 10) -> list[dict]:
    terms = [f'release:"{lucene(album)}"']
    if artist_name:
        terms.append(f'artist:"{lucene(artist_name)}"')
    query = " AND ".join(terms)
    if tracks:
        query += f" tracks:{int(tracks)}"   # a boost, not a filter
    data = await client.get("release", {"query": query, "limit": limit}, ttl=SEARCH_TTL,
                            cache_key=f"mb:search:{query}:{limit}")
    return (data or {}).get("releases") or []


async def search_free(text: str, limit: int = 15) -> list[dict]:
    data = await client.get("release", {"query": text, "limit": limit}, ttl=SEARCH_TTL,
                            cache_key=f"mb:searchfree:{text}:{limit}")
    return (data or {}).get("releases") or []


async def search_artists(name: str, limit: int = 5) -> list[dict]:
    data = await client.get("artist", {"query": f'artist:"{lucene(name)}"', "limit": limit},
                            ttl=SEARCH_TTL, cache_key=f"mb:artistsearch:{name.casefold()}")
    return (data or {}).get("artists") or []


async def genre_names() -> list[str]:
    """Every genre MusicBrainz recognises (its curated list, ~2,000 names)."""
    key = "mb:genres:all"
    cached = await cache.get(key, GENRES_TTL)
    if cached is not None:
        return cached
    if not enabled():
        return []
    names: list[str] = []
    offset = 0
    while True:
        data = await client._fetch("genre/all", {"limit": 100, "offset": offset})
        batch = (data or {}).get("genres") or []
        names.extend(g["name"] for g in batch if g.get("name"))
        offset += len(batch)
        if not batch or offset >= int((data or {}).get("genre-count") or 0):
            break
    if names:
        await cache.put(key, names)
    return names


# ------------------------------------------------------------------ helpers
def credit_string(credits: list[dict] | None) -> str:
    """"Drake feat. Rihanna" from an artist-credit list, as released."""
    return "".join(f"{c.get('name') or (c.get('artist') or {}).get('name') or ''}"
                   f"{c.get('joinphrase') or ''}" for c in credits or []).strip()


def credit_names(credits: list[dict] | None) -> list[str]:
    return [c.get("name") or (c.get("artist") or {}).get("name") or ""
            for c in credits or [] if c.get("name") or c.get("artist")]


def credit_ids(credits: list[dict] | None) -> list[str]:
    return [(c.get("artist") or {}).get("id") for c in credits or []
            if (c.get("artist") or {}).get("id")]


def release_tracks(rel: dict) -> list[dict]:
    """A release's tracks, flattened across media, with what matching needs."""
    out = []
    for medium in rel.get("media") or []:
        disc = medium.get("position") or 1
        count = medium.get("track-count") or len(medium.get("tracks") or [])
        for t in medium.get("tracks") or []:
            recording = t.get("recording") or {}
            length = t.get("length") or recording.get("length")
            out.append({
                "disc": disc,
                "number": t.get("position") or int(re.sub(r"\D", "", str(t.get("number") or 0)) or 0),
                "disc_tracks": count,
                "medium_title": medium.get("title") or "",
                "title": t.get("title") or recording.get("title") or "",
                "length": length / 1000 if length else None,
                "track_id": t.get("id"),
                "recording_id": recording.get("id"),
                "isrcs": recording.get("isrcs") or [],
                "credits": t.get("artist-credit") or recording.get("artist-credit") or [],
            })
    return out


def top_genres(entity: dict | None) -> list[tuple[str, int]]:
    """(name, votes) of an entity's curated genres, most voted first."""
    genres = [(g["name"], int(g.get("count") or 0)) for g in (entity or {}).get("genres") or []
              if g.get("name")]
    return sorted(genres, key=lambda g: -g[1])
