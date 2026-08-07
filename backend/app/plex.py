"""Plex API client. The library sits on CIFS, which produces no inotify
events for Plex — the targeted partial scan after each import is what makes
new music appear, not an optimization. NOTE: the refresh endpoint is GET
(verified against this server; PUT returns 404)."""
import logging
import urllib.parse

import httpx

from .config import settings

log = logging.getLogger("mdl.plex")


class PlexClient:
    def __init__(self) -> None:
        self._base = settings.plex_url.rstrip("/")

    def _params(self, **extra) -> dict:
        return {"X-Plex-Token": settings.plex_token, **extra}

    async def reachable(self) -> bool:
        if not settings.plex_token:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self._base}/identity", params=self._params())
                return r.status_code == 200
        except Exception:
            return False

    def to_plex_path(self, library_relpath: str) -> str:
        """Translate a path relative to our /library mount into Plex's view."""
        return f"{settings.plex_library_root.rstrip('/')}/{library_relpath.lstrip('/')}"

    async def refresh_path(self, library_relpath: str) -> bool:
        plex_path = self.to_plex_path(library_relpath)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{self._base}/library/sections/{settings.plex_section_id}/refresh",
                    params=self._params(path=plex_path),
                )
                ok = r.status_code == 200
                log.info("plex partial scan %s -> %s", plex_path, r.status_code)
                return ok
        except Exception as exc:
            log.warning("plex refresh failed for %s: %s", plex_path, exc)
            return False

    async def refresh_all(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{self._base}/library/sections/{settings.plex_section_id}/refresh",
                    params=self._params(),
                )
                return r.status_code == 200
        except Exception as exc:
            log.warning("plex full refresh failed: %s", exc)
            return False

    async def empty_trash(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.put(
                    f"{self._base}/library/sections/{settings.plex_section_id}/emptyTrash",
                    params=self._params(),
                )
                return r.status_code == 200
        except Exception as exc:
            log.warning("plex empty trash failed: %s", exc)
            return False

    async def find_track(self, title: str, artist: str) -> str | None:
        """Best-effort search for an imported track; returns ratingKey."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self._base}/library/sections/{settings.plex_section_id}/search",
                    params=self._params(type=10, title=title),
                    headers={"Accept": "application/json"},
                )
                r.raise_for_status()
                metadata = (r.json().get("MediaContainer") or {}).get("Metadata") or []
                artist_cf = artist.casefold()
                for item in metadata:
                    grandparent = (item.get("grandparentTitle") or "").casefold()
                    if artist_cf in grandparent or grandparent in artist_cf:
                        return str(item.get("ratingKey"))
                if metadata:
                    return str(metadata[0].get("ratingKey"))
        except Exception as exc:
            log.debug("plex track search failed: %s", exc)
        return None


plex = PlexClient()
