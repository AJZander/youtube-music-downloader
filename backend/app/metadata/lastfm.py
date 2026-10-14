"""Last.fm top tags — the genre and mood signal for releases and artists that
MusicBrainz has no genres for (most small artists).

Free API key, non-commercial use, and a cap on how much may be stored: only
the top tag names and counts are kept, in the shared metadata cache.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from ..config import settings
from . import cache

log = logging.getLogger("mdl.lastfm")

API = "https://ws.audioscrobbler.com/2.0/"
TTL = 30 * 86400
MIN_INTERVAL = 0.25


def enabled() -> bool:
    return bool(settings.lastfm_api_key.strip())


class Client:
    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._last = 0.0

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=20,
                                           headers={"User-Agent": "MusicDownloader/2.0"})
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def top_tags(self, method: str, **params) -> list[tuple[str, int]]:
        if not enabled():
            return []
        key = "lfm:" + method + ":" + ":".join(
            f"{k}={str(v).casefold()}" for k, v in sorted(params.items()))
        cached = await cache.get(key, TTL)
        if cached is not None:
            return [tuple(t) for t in cached]
        async with self._lock:
            wait = self._last + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            try:
                r = await self._client().get(API, params={
                    "method": method, "api_key": settings.lastfm_api_key.strip(),
                    "format": "json", "autocorrect": 1, **params})
                data = r.json() if r.status_code == 200 else {}
            except (httpx.HTTPError, ValueError) as exc:
                log.info("last.fm %s failed: %s", method, exc)
                return []
        tags = [(t.get("name") or "", int(t.get("count") or 0))
                for t in ((data.get("toptags") or {}).get("tag") or [])
                if t.get("name")][:30]
        if "error" not in data:
            await cache.put(key, tags)
        return tags


client = Client()


async def album_tags(artist: str, album: str) -> list[tuple[str, int]]:
    return await client.top_tags("album.gettoptags", artist=artist, album=album)


async def artist_tags(artist: str) -> list[tuple[str, int]]:
    return await client.top_tags("artist.gettoptags", artist=artist)
