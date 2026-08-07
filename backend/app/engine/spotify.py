"""Spotify metadata via spotipy. Client-credentials covers all public
entities; a cached user token (carried over from v1) additionally enables
the personal-playlists browser. Spotify audio is never downloaded — this
is a metadata source only."""
import asyncio
import logging
from functools import partial

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

from ..config import settings

log = logging.getLogger("mdl.spotify")

_client: spotipy.Spotify | None = None
_user_client: spotipy.Spotify | None = None


def _get_client() -> spotipy.Spotify:
    global _client
    if _client is None:
        _client = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=settings.spotify_client_id,
                client_secret=settings.spotify_client_secret,
            ),
            requests_timeout=15,
            retries=2,
        )
    return _client


def _get_user_client() -> spotipy.Spotify | None:
    global _user_client
    if _user_client is None:
        cache = settings.data_dir / "spotify_token.json"
        if not cache.exists():
            return None
        try:
            auth = SpotifyOAuth(
                client_id=settings.spotify_client_id,
                client_secret=settings.spotify_client_secret,
                redirect_uri=settings.spotify_redirect_uri or "http://localhost/callback",
                scope="user-library-read playlist-read-private",
                cache_path=str(cache),
                open_browser=False,
            )
            if not auth.get_cached_token():
                return None
            _user_client = spotipy.Spotify(auth_manager=auth, requests_timeout=15, retries=2)
        except Exception as exc:
            log.warning("spotify user client unavailable: %s", exc)
            return None
    return _user_client


async def _call(func, *args, **kwargs):
    return await asyncio.get_running_loop().run_in_executor(None, partial(func, *args, **kwargs))


def _norm_track(t: dict, album: dict | None = None) -> dict:
    album = album or t.get("album") or {}
    artists = [a["name"] for a in t.get("artists", [])]
    album_artists = [a["name"] for a in album.get("artists", [])] or artists
    images = album.get("images") or []
    return {
        "provider": "spotify",
        "provider_track_id": t["id"],
        "title": t["name"],
        "artist": ", ".join(artists),
        "primary_artist": artists[0] if artists else "",
        "album": album.get("name") or "",
        "album_artist": album_artists[0] if album_artists else "",
        "track_number": t.get("track_number"),
        "disc_number": t.get("disc_number"),
        "release_date": album.get("release_date") or "",
        "duration_sec": (t.get("duration_ms") or 0) / 1000.0,
        "isrc": (t.get("external_ids") or {}).get("isrc"),
        "cover_url": images[0]["url"] if images else None,
        "source_url": f"https://open.spotify.com/track/{t['id']}",
    }


async def get_track(track_id: str) -> dict:
    sp = _get_client()
    t = await _call(sp.track, track_id)
    return _norm_track(t)


async def get_album_tracks(album_id: str) -> tuple[dict, list[dict]]:
    sp = _get_client()
    album = await _call(sp.album, album_id)
    tracks = []
    page = album["tracks"]
    while True:
        for t in page["items"]:
            # album_tracks items lack external_ids; fetch full objects in bulk below
            tracks.append(t)
        if page.get("next"):
            page = await _call(sp.next, page)
        else:
            break
    # Bulk-fetch full track objects (50/call) to get ISRCs
    full = []
    ids = [t["id"] for t in tracks if t.get("id")]
    for i in range(0, len(ids), 50):
        batch = await _call(sp.tracks, ids[i:i + 50])
        full.extend(batch["tracks"])
    meta = {"title": album["name"],
            "artist": ", ".join(a["name"] for a in album.get("artists", []))}
    return meta, [_norm_track(t, album) for t in full if t]


async def get_playlist_tracks(playlist_id: str) -> tuple[dict, list[dict]]:
    sp = _get_user_client() or _get_client()
    pl = await _call(sp.playlist, playlist_id, fields="name,owner.display_name")
    results = []
    page = await _call(sp.playlist_items, playlist_id, limit=100)
    while True:
        for entry in page.get("items") or []:
            # Spotify 2026 API nests the track under 'item' (was 'track');
            # support both shapes. is_local lives at either level.
            t = entry.get("track") or entry.get("item")
            if not t or not t.get("id"):
                continue
            if entry.get("is_local") or t.get("is_local"):
                continue
            if t.get("type") not in (None, "track"):  # skip podcast episodes
                continue
            results.append(_norm_track(t))
        if page.get("next"):
            page = await _call(sp.next, page)
        else:
            break
    return {"title": pl["name"], "artist": (pl.get("owner") or {}).get("display_name", "")}, results


async def get_artist_albums(artist_id: str) -> tuple[dict, list[dict]]:
    """Returns artist meta + list of album stubs (id, name, type, date)."""
    sp = _get_client()
    artist = await _call(sp.artist, artist_id)
    albums, seen_names = [], set()
    page = await _call(sp.artist_albums, artist_id, album_type="album,single", limit=50)
    while True:
        for a in page["items"]:
            key = a["name"].casefold()
            if key not in seen_names:  # dedupe regional duplicates
                seen_names.add(key)
                albums.append({"id": a["id"], "name": a["name"],
                               "album_type": a["album_type"],
                               "release_date": a.get("release_date")})
        if page.get("next"):
            page = await _call(sp.next, page)
        else:
            break
    return {"title": artist["name"], "artist": artist["name"]}, albums


async def get_user_playlists() -> list[dict]:
    sp = _get_user_client()
    if sp is None:
        return []
    out = []
    page = await _call(sp.current_user_playlists, limit=50)
    while page:
        # Spotify can return null items and playlists without a tracks object
        for p in page.get("items") or []:
            if not p or not p.get("id"):
                continue
            out.append({"id": p["id"], "name": p.get("name") or "Untitled",
                        "tracks": (p.get("tracks") or {}).get("total", 0),
                        "url": f"https://open.spotify.com/playlist/{p['id']}"})
        page = await _call(sp.next, page) if page.get("next") else None
    return out


def user_client_available() -> bool:
    return _get_user_client() is not None


def make_oauth() -> SpotifyOAuth:
    return SpotifyOAuth(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        redirect_uri=settings.spotify_redirect_uri,
        scope="user-library-read playlist-read-private",
        cache_path=str(settings.data_dir / "spotify_token.json"),
        open_browser=False,
    )


def reset_user_client() -> None:
    """Drop the cached client so a freshly exchanged token is picked up."""
    global _user_client
    _user_client = None
