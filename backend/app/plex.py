"""Plex API client. The library sits on CIFS, which produces no inotify
events for Plex — the targeted partial scan after each import is what makes
new music appear, not an optimization. NOTE: the refresh endpoint is GET
(verified against this server; PUT returns 404).

One pooled connection for everything, paced so a full sync of a 60k-track
section never monopolises the server Plex is also streaming from.

Two things about editing are easy to get wrong and expensive when you do:

* Editing a field on an item sends `<field>.value=...&<field>.locked=1`;
  tags send `genre[0].tag.tag=A&genre[1].tag.tag=B&genre.locked=1` and remove
  with `genre[].tag.tag-=A,B`. A locked field survives agent refreshes, which
  is the whole point — otherwise the next refresh puts Plex's value back.
* Setting `title` on an artist or album *merges* it into any other item with
  that title. Titles are therefore never edited here; they are fixed in the
  file tags, where Plex's scanner reads them.
"""
from __future__ import annotations

import asyncio
import logging
import time
import unicodedata

import httpx

from .config import settings

log = logging.getLogger("mdl.plex")

ARTIST, ALBUM, TRACK = 8, 9, 10
TYPE_NUM = {"artist": ARTIST, "album": ALBUM, "track": TRACK}
MUSIC_AGENT = "tv.plex.agents.music"


class PlexError(Exception):
    pass


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def mbid_of(item: dict) -> str | None:
    """The MusicBrainz id Plex holds for a matched (or MBID-tagged) item.
    For albums it is a release id; for tracks a release-track id."""
    for guid in item.get("Guid") or []:
        gid = guid.get("id") or ""
        if gid.startswith("mbid://"):
            return gid[7:]
    guid = item.get("guid") or ""
    return guid[7:] if guid.startswith("mbid://") else None


def is_matched(guid: str | None) -> bool:
    """plex:// = matched to Plex's catalogue. mbid:// = matched only through
    MusicBrainz ids in the files (Plex still lists it as unmatched, but it has
    MusicBrainz data). local:// = not matched at all."""
    return bool(guid) and guid.startswith("plex://")


def tag_values(item: dict, key: str) -> list[str]:
    return [t.get("tag") for t in item.get(key) or [] if t.get("tag")]


class PlexClient:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._sem = asyncio.Semaphore(6)
        self._pace_lock = asyncio.Lock()
        self._last = 0.0

    # ------------------------------------------------------------ plumbing
    @property
    def _base(self) -> str:
        return settings.plex_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(settings.plex_token)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30, connect=10),
                headers={"Accept": "application/json",
                         "X-Plex-Product": "Music Downloader",
                         "X-Plex-Client-Identifier": "mdl-music-downloader"},
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=8))
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _params(self, **extra) -> dict:
        return {"X-Plex-Token": settings.plex_token, **extra}

    async def _pace(self) -> None:
        interval = 1.0 / max(settings.plex_requests_per_sec, 1.0)
        async with self._pace_lock:
            wait = self._last + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    async def request(self, method: str, path: str, params=None, *,
                      content: bytes | None = None, raw: bool = False,
                      timeout: float | None = None):
        """One API call, retried on transport errors and 5xx. Returns the
        MediaContainer dict (or raw bytes); raises PlexError otherwise.
        `params` may be a dict or a list of pairs (repeated keys)."""
        if not self.configured:
            raise PlexError("Plex is not configured (PLEX_TOKEN)")
        if isinstance(params, dict) or params is None:
            query = list((params or {}).items())
        else:
            query = list(params)
        query.append(("X-Plex-Token", settings.plex_token))
        last_exc: Exception | None = None
        for attempt in range(3):
            async with self._sem:
                await self._pace()
                try:
                    r = await self._http().request(
                        method, f"{self._base}{path}", params=query, content=content,
                        timeout=timeout or httpx.USE_CLIENT_DEFAULT)
                except httpx.HTTPError as exc:
                    last_exc = exc
                    await asyncio.sleep(1 + attempt * 2)
                    continue
            if r.status_code >= 500:
                last_exc = PlexError(f"{method} {path} -> {r.status_code}")
                await asyncio.sleep(1 + attempt * 2)
                continue
            if r.status_code == 404:
                raise PlexError(f"{method} {path} -> 404")
            if r.status_code >= 400:
                raise PlexError(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
            if raw:
                return r.content
            if not r.content:
                return {}
            try:
                return r.json().get("MediaContainer") or {}
            except ValueError:
                return {}
        raise PlexError(f"{method} {path} failed: {last_exc}")

    async def get(self, path: str, **params) -> dict:
        return await self.request("GET", path, params)

    # ------------------------------------------------------------ paths
    def to_plex_path(self, library_relpath: str) -> str:
        """Translate a path relative to our /library mount into Plex's view."""
        return f"{settings.plex_library_root.rstrip('/')}/{library_relpath.lstrip('/')}"

    def from_plex_path(self, plex_file: str) -> str | None:
        """Plex's absolute path back to a library-relative one (NFC, since the
        two sides do not always agree on Unicode normalisation)."""
        root = settings.plex_library_root.rstrip("/") + "/"
        if not plex_file or not plex_file.startswith(root):
            return None
        return nfc(plex_file[len(root):])

    # ------------------------------------------------------------ reads
    async def reachable(self) -> bool:
        if not settings.plex_token:
            return False
        try:
            await self.request("GET", "/identity", timeout=5)
            return True
        except Exception:
            return False

    async def section_prefs(self) -> dict:
        data = await self.get(f"/library/sections/{settings.plex_section_id}/prefs")
        return {s["id"]: s.get("value") for s in data.get("Setting") or []}

    async def count(self, type_num: int) -> int:
        data = await self.get(f"/library/sections/{settings.plex_section_id}/all",
                              type=type_num, **{"X-Plex-Container-Start": 0,
                                                "X-Plex-Container-Size": 0})
        return int(data.get("totalSize") or 0)

    async def iter_items(self, type_num: int, page: int = 2000, **filters):
        """Every item of a type in the music section, paged."""
        start = 0
        while True:
            data = await self.get(
                f"/library/sections/{settings.plex_section_id}/all",
                type=type_num, includeGuids=1, **filters,
                **{"X-Plex-Container-Start": start, "X-Plex-Container-Size": page})
            items = data.get("Metadata") or []
            for item in items:
                yield item
            start += len(items)
            total = int(data.get("totalSize") or 0)
            if not items or start >= total:
                return

    async def metadata(self, rating_key: str) -> dict | None:
        try:
            data = await self.get(f"/library/metadata/{rating_key}", includeGuids=1)
        except PlexError as exc:
            if "404" in str(exc):
                return None
            raise
        items = data.get("Metadata") or []
        return items[0] if items else None

    async def children(self, rating_key: str) -> list[dict]:
        data = await self.get(f"/library/metadata/{rating_key}/children", includeGuids=1)
        return data.get("Metadata") or []

    async def vocab(self, tag: str, type_num: int = ALBUM) -> list[str]:
        """Every value of a tag type used in the section (genre, mood, style)."""
        data = await self.get(f"/library/sections/{settings.plex_section_id}/{tag}",
                              type=type_num)
        return [d.get("title") for d in data.get("Directory") or [] if d.get("title")]

    async def activities(self) -> list[dict]:
        data = await self.get("/activities")
        return data.get("Activity") or []

    async def section_title(self) -> str | None:
        if getattr(self, "_section_title", None) is None:
            try:
                data = await self.get("/library/sections")
                for d in data.get("Directory") or []:
                    if str(d.get("key")) == str(settings.plex_section_id):
                        self._section_title = d.get("title") or ""
                        break
                else:
                    self._section_title = ""
            except PlexError:
                return None
        return self._section_title or None

    async def scanning(self) -> bool:
        """Is Plex scanning *the music library* right now? Sonic analysis and
        scans of other libraries (a TV Shows scan can run for an hour) don't
        count — only what changes which music items exist."""
        title = await self.section_title()
        try:
            activities = await self.activities()
        except PlexError:
            return False
        for activity in activities:
            kind = activity.get("type") or ""
            if not (kind.startswith("library.update") or kind in (
                    "library.refresh.items", "library.scan")):
                continue
            section = str((activity.get("Context") or {}).get("librarySectionID") or "")
            if section:
                if section == str(settings.plex_section_id):
                    return True
                continue
            text = f"{activity.get('title') or ''} {activity.get('subtitle') or ''}".casefold()
            if title and title.casefold() in text:
                return True
        return False

    async def wait_for_scan(self, timeout: float = 180, settle: float = 4.0) -> bool:
        """Wait until Plex has finished scanning (True) or give up (False)."""
        await asyncio.sleep(settle)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not await self.scanning():
                return True
            await asyncio.sleep(3)
        return False

    async def matches(self, rating_key: str, *, title: str | None = None,
                      parent_title: str | None = None, year: int | str | None = None
                      ) -> list[dict]:
        params = {"manual": 1, "agent": MUSIC_AGENT}
        if title:
            params["title"] = title
        if parent_title:
            params["parentTitle"] = parent_title
        if year:
            params["year"] = year
        data = await self.get(f"/library/metadata/{rating_key}/matches", **params)
        return data.get("SearchResult") or []

    # ------------------------------------------------------------ writes
    async def match(self, rating_key: str, guid: str, name: str,
                    year: int | str | None = None) -> None:
        params = {"guid": guid, "name": name}
        if year:
            params["year"] = year
        await self.request("PUT", f"/library/metadata/{rating_key}/match", params)

    async def unmatch(self, rating_key: str) -> None:
        await self.request("PUT", f"/library/metadata/{rating_key}/unmatch")

    async def refresh_item(self, rating_key: str, agent: str | None = None) -> None:
        params = {"agent": agent} if agent else None
        await self.request("PUT", f"/library/metadata/{rating_key}/refresh", params)

    async def edit(self, type_num: int, rating_keys: list[str] | str, pairs: list) -> None:
        """One PUT for any number of field/tag edits across any number of
        items of one type (Plex applies them to every id)."""
        keys = [rating_keys] if isinstance(rating_keys, str) else list(rating_keys)
        if not keys or not pairs:
            return
        params = [("type", type_num), ("id", ",".join(keys)), *pairs]
        await self.request("PUT", f"/library/sections/{settings.plex_section_id}/all",
                           params)

    @staticmethod
    def tag_pairs(tag: str, values: list[str], current: list[str] | None = None,
                  lock: bool = True) -> list[tuple[str, str]]:
        """Params that make `tag` exactly `values`: add each wanted value,
        remove whatever is there that isn't wanted, and lock it."""
        pairs: list[tuple[str, str]] = [
            (f"{tag}[{i}].tag.tag", v) for i, v in enumerate(values)]
        wanted = {v.casefold() for v in values}
        stale = [v for v in (current or []) if v.casefold() not in wanted]
        if stale:
            pairs.append((f"{tag}[].tag.tag-", ",".join(stale)))
        pairs.append((f"{tag}.locked", "1" if lock else "0"))
        return pairs

    @staticmethod
    def field_pairs(field: str, value, lock: bool = True) -> list[tuple[str, str]]:
        if field == "title":
            raise PlexError("titles are fixed in file tags; editing them in Plex merges items")
        return [(f"{field}.value", "" if value is None else str(value)),
                (f"{field}.locked", "1" if lock else "0")]

    async def set_tags(self, type_num: int, rating_keys, tag: str, values: list[str],
                       current: list[str] | None = None, lock: bool = True) -> None:
        await self.edit(type_num, rating_keys, self.tag_pairs(tag, values, current, lock))

    async def upload_poster(self, rating_key: str, *, url: str | None = None,
                            data: bytes | None = None, kind: str = "posters") -> None:
        if url:
            await self.request("POST", f"/library/metadata/{rating_key}/{kind}",
                               {"url": url})
        elif data:
            await self.request("POST", f"/library/metadata/{rating_key}/{kind}",
                               content=data)
        else:
            raise PlexError("poster needs a url or bytes")

    async def photo(self, thumb_path: str, size: int = 500) -> bytes:
        """A poster through Plex's transcoder, for the UI. The token stays
        server-side: the browser only ever talks to us."""
        return await self.request(
            "GET", "/photo/:/transcode",
            {"url": thumb_path, "width": size, "height": size, "minSize": 1, "upscale": 1},
            raw=True)

    async def refresh_path(self, library_relpath: str) -> bool:
        plex_path = self.to_plex_path(library_relpath)
        try:
            await self.request(
                "GET", f"/library/sections/{settings.plex_section_id}/refresh",
                {"path": plex_path})
            log.info("plex partial scan %s", plex_path)
            return True
        except Exception as exc:
            log.warning("plex refresh failed for %s: %s", plex_path, exc)
            return False

    async def refresh_all(self) -> bool:
        try:
            await self.request("GET", f"/library/sections/{settings.plex_section_id}/refresh")
            log.info("plex scan of the whole music library")
            return True
        except Exception as exc:
            log.warning("plex full refresh failed: %s", exc)
            return False

    async def empty_trash(self) -> bool:
        try:
            await self.request("PUT", f"/library/sections/{settings.plex_section_id}/emptyTrash")
            return True
        except Exception as exc:
            log.warning("plex empty trash failed: %s", exc)
            return False

    # ------------------------------------------------------------ lookups
    async def find_track_by_path(self, library_relpath: str,
                                 added_since: int | None = None) -> str | None:
        """The ratingKey of the Plex track playing exactly this file, if Plex
        has it. Path-based, so it can't mistake another recording of the
        same song for this one the way a title search can."""
        wanted = nfc(library_relpath)
        filters = {}
        if added_since:
            filters["addedAt>>"] = int(added_since)
        try:
            async for item in self.iter_items(TRACK, page=500, **filters):
                for media in item.get("Media") or []:
                    for part in media.get("Part") or []:
                        if self.from_plex_path(part.get("file") or "") == wanted:
                            return str(item.get("ratingKey"))
        except Exception as exc:
            log.debug("plex path lookup failed: %s", exc)
        return None

    async def find_track(self, title: str, artist: str) -> str | None:
        """Title search for a track by this artist; returns ratingKey. Only an
        artist match counts — the first hit for a common title is usually
        someone else's song, and "verified" must not mean that."""
        try:
            data = await self.get(
                f"/library/sections/{settings.plex_section_id}/search", type=TRACK,
                title=title)
            artist_cf = (artist or "").casefold()
            for item in data.get("Metadata") or []:
                grandparent = (item.get("grandparentTitle") or "").casefold()
                if artist_cf and (artist_cf in grandparent or grandparent in artist_cf):
                    return str(item.get("ratingKey"))
        except Exception as exc:
            log.debug("plex track search failed: %s", exc)
        return None


plex = PlexClient()
