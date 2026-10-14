"""Cover art: for the UI's album grid and artist header, and for filling a
missing Plex poster. Local first (folder art, then the art embedded in a
track), then Plex's own poster through its transcoder — so the browser
never sees the Plex token."""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from pathlib import Path

from ..config import settings
from ..db import db
from ..engine import tagger
from ..plex import PlexError, plex

log = logging.getLogger("mdl.art")

_NAMES = ("folder", "cover", "front", "album", "albumart", "albumartsmall")
_EXTS = (".jpg", ".jpeg", ".png", ".webp")
_CACHE: OrderedDict[str, tuple[bytes, str] | None] = OrderedDict()
_CACHE_MAX = 400


def _sniff(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _remember(key: str, value) -> None:
    _CACHE[key] = value
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)


def forget(prefix: str) -> None:
    for key in [k for k in _CACHE if k.startswith(prefix)]:
        _CACHE.pop(key, None)


async def local_cover(folder: str, paths: list[str]) -> bytes | None:
    root = settings.library_dir

    def find() -> bytes | None:
        directory = root / folder
        try:
            files = {p.name.lower(): p for p in directory.iterdir() if p.is_file()}
        except OSError:
            files = {}
        for name in _NAMES:
            for ext in _EXTS:
                hit = files.get(name + ext)
                if hit:
                    try:
                        return hit.read_bytes()
                    except OSError:
                        continue
        for rel in paths[:3]:
            data = tagger.extract_embedded_cover(root / rel)
            if data:
                return data
        return None

    return await asyncio.get_running_loop().run_in_executor(None, find)


async def album_art(key: str) -> tuple[bytes, str] | None:
    cache_key = f"album:{key}"
    if cache_key in _CACHE:
        _CACHE.move_to_end(cache_key)
        return _CACHE[cache_key]
    folder = key.split("|", 1)[0]
    within = (folder, folder + "/", folder + "0")      # the folder and its disc subfolders
    rows = await db.fetchall("SELECT path FROM library_index WHERE dirpath = ? OR "
                             "(dirpath >= ? AND dirpath < ?) "
                             "ORDER BY disc_number, track_number LIMIT 3", within)
    data = await local_cover(folder, [r["path"] for r in rows])
    if data is None and plex.configured:
        thumb = await db.fetchone(
            "SELECT a.thumb FROM plex_items t JOIN plex_items a ON a.rating_key = t.parent_key "
            "WHERE t.type = 'track' AND (t.folder = ? OR (t.folder >= ? AND t.folder < ?)) "
            "AND a.thumb IS NOT NULL LIMIT 1", within)
        if thumb:
            try:
                data = await plex.photo(thumb["thumb"], 400)
            except PlexError:
                data = None
    result = (data, _sniff(data)) if data else None
    _remember(cache_key, result)
    return result


async def artist_art(artist_dir: str) -> tuple[bytes, str] | None:
    cache_key = f"artist:{artist_dir}"
    if cache_key in _CACHE:
        _CACHE.move_to_end(cache_key)
        return _CACHE[cache_key]
    data = None
    row = await db.fetchone(
        "SELECT p.thumb FROM md_artists m JOIN plex_items p ON p.rating_key = m.plex_key "
        "WHERE m.artist_dir = ?", (artist_dir,))
    if row and row["thumb"] and plex.configured:
        try:
            data = await plex.photo(row["thumb"], 400)
        except PlexError:
            data = None
    if data is None:
        url = await youtube_thumbnail(artist_dir)
        if url:
            from .apply import _fetch_image
            data = await _fetch_image([url])
    if data is None:
        root = settings.library_dir

        def find():
            for name in ("artist", "folder", "cover"):
                for ext in _EXTS:
                    p = root / artist_dir / (name + ext)
                    if p.is_file():
                        try:
                            return p.read_bytes()
                        except OSError:
                            return None
            return None

        data = await asyncio.get_running_loop().run_in_executor(None, find)
    result = (data, _sniff(data)) if data else None
    _remember(cache_key, result)
    return result


async def youtube_thumbnail(artist_dir: str) -> str | None:
    """The artist's YouTube Music picture, from what Discover already knows."""
    from ..library import strict_key
    row = await db.fetchone(
        "SELECT a.thumbnail FROM discover_names n JOIN discover_artists a "
        "ON a.channel_id = n.channel_id WHERE n.name_key = ? AND a.thumbnail IS NOT NULL",
        (strict_key(artist_dir),))
    if not row:
        return None
    url = row["thumbnail"]
    # YouTube image URLs carry their size; ask for a big one
    import re
    return re.sub(r"=w\d+-h\d+", "=w800-h800", url) if url else None
