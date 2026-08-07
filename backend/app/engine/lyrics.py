"""Synced lyrics from LRCLIB (free, keyless). Failures are always non-fatal."""
import logging

import httpx

log = logging.getLogger("mdl.lyrics")

API = "https://lrclib.net/api/get"


async def fetch_synced(artist: str, title: str, album: str | None = None,
                       duration_sec: float | None = None) -> str | None:
    """Try with the album filter first, then without — singles are often
    indexed under a different album name than the release we tagged."""
    attempts = [{"artist_name": artist, "track_name": title}]
    if album:
        attempts.insert(0, {"artist_name": artist, "track_name": title,
                            "album_name": album})
    for params in attempts:
        if duration_sec:
            params["duration"] = int(round(duration_sec))
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(API, params=params,
                                     headers={"User-Agent": "mdl/2.0 (self-hosted music tool)"})
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                synced = r.json().get("syncedLyrics")
                if synced:
                    return synced
        except Exception as exc:
            log.debug("lrclib fetch failed for %s - %s: %s", artist, title, exc)
    return None
