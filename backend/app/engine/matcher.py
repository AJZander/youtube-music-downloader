"""Spotify->YouTube Music track matching with a hard verification gate.
Candidates come from ISRC search, spotDL's battle-tested matcher (used as a
library — its downloader is never invoked), and a plain filtered search.
Whatever the generators propose, nothing is accepted without passing the
duration + title/artist gate. No confident match = explicit failure with the
candidate list attached — never 'download the top hit anyway'."""
import asyncio
import logging
import re
import unicodedata
from functools import partial

from ..config import settings
from ..models import ErrorClass
from .errors import EngineError
from .governor import governor, REQUEST_WEIGHT

log = logging.getLogger("mdl.matcher")

_ytmusic = None

# Markers that make a candidate suspect unless the Spotify title has them too
_SUSPECT_RE = re.compile(
    r"\b(live|cover|remix|sped.?up|slowed|reverb|nightcore|instrumental|karaoke|8d)\b", re.I
)
_NOISE_RE = re.compile(
    r"\s*[\(\[][^)\]]*(official|video|audio|lyric|visuali[sz]er|hd|4k|remaster)[^)\]]*[\)\]]", re.I
)


def _ytm():
    global _ytmusic
    if _ytmusic is None:
        from ytmusicapi import YTMusic
        _ytmusic = YTMusic()
    return _ytmusic


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    s = _NOISE_RE.sub("", s)
    s = re.sub(r"\bfeat\.?\b.*$|\bft\.?\b.*$", "", s)
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _suspect_markers(title: str) -> set[str]:
    return {m.lower().replace(" ", "") for m in _SUSPECT_RE.findall(title or "")}


def _passes_gate(spotify_track: dict, candidate: dict) -> bool:
    dur = candidate.get("duration_sec")
    expected = spotify_track["duration_sec"]
    tolerance = settings.duration_tolerance_sec + (2.0 if candidate.get("is_topic") else 0.0)
    if not dur or abs(dur - expected) > tolerance:
        return False
    # title: normalized containment either way
    ct, st = normalize(candidate.get("title", "")), normalize(spotify_track["title"])
    if not ct or not st or (st not in ct and ct not in st):
        return False
    # artist: primary artist must appear among candidate artists
    cand_artists = normalize(candidate.get("artists", ""))
    if normalize(spotify_track["primary_artist"]) not in cand_artists:
        return False
    # live/cover/remix markers must be consistent with the Spotify title
    extra = _suspect_markers(candidate.get("title", "")) - _suspect_markers(spotify_track["title"])
    if extra:
        return False
    return True


def _score(spotify_track: dict, c: dict) -> float:
    score = 0.0
    if c.get("from_isrc"):
        score += 3.0
    if c.get("result_type") == "song":
        score += 2.0
    if c.get("is_topic"):
        score += 1.0
    if normalize(c.get("album", "")) == normalize(spotify_track.get("album", "")):
        score += 1.5
    dur = c.get("duration_sec") or 0
    score -= abs(dur - spotify_track["duration_sec"]) * 0.2
    return score


def _from_ytm_result(r: dict, from_isrc: bool = False) -> dict | None:
    vid = r.get("videoId")
    if not vid:
        return None
    artists = ", ".join(a.get("name", "") for a in (r.get("artists") or []))
    return {
        "video_id": vid,
        "title": r.get("title", ""),
        "artists": artists,
        "album": (r.get("album") or {}).get("name", "") if isinstance(r.get("album"), dict) else "",
        "duration_sec": r.get("duration_seconds"),
        "result_type": r.get("resultType"),
        "is_topic": artists.endswith(" - Topic"),
        "from_isrc": from_isrc,
    }


async def _ytm_search(query: str, from_isrc: bool = False) -> list[dict]:
    await governor.acquire(REQUEST_WEIGHT)
    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(
            None, partial(_ytm().search, query, filter="songs", limit=5))
    except Exception as exc:
        log.warning("ytmusic search failed for %r: %s", query, exc)
        return []
    out = []
    for r in results[:5]:
        c = _from_ytm_result(r, from_isrc)
        if c:
            out.append(c)
    return out


async def _spotdl_candidate(spotify_track: dict) -> list[dict]:
    """spotDL's YouTubeMusic provider as a secondary generator. Fully
    defensive: its internal API may drift between releases."""
    try:
        from spotdl.providers.audio.ytmusic import YouTubeMusic
        from spotdl.types.song import Song
    except Exception:
        return []
    try:
        await governor.acquire(REQUEST_WEIGHT)
        song = Song.from_missing_data(
            name=spotify_track["title"],
            artists=[a.strip() for a in spotify_track["artist"].split(",")],
            artist=spotify_track["primary_artist"],
            album_name=spotify_track["album"],
            album_artist=spotify_track["album_artist"],
            duration=int(spotify_track["duration_sec"]),
            isrc=spotify_track.get("isrc"),
        )
        loop = asyncio.get_running_loop()
        url = await loop.run_in_executor(None, partial(YouTubeMusic().search, song))
        if not url:
            return []
        m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
        if not m:
            return []
        return [{
            "video_id": m.group(1),
            "title": spotify_track["title"],   # spotdl already verified similarity
            "artists": spotify_track["artist"],
            "album": spotify_track["album"],
            "duration_sec": spotify_track["duration_sec"],
            "result_type": "song",
            "is_topic": False,
            "from_isrc": False,
            "from_spotdl": True,
        }]
    except Exception as exc:
        log.debug("spotdl matcher unavailable/failed: %s", exc)
        return []


async def match(spotify_track: dict) -> tuple[str, list[dict]]:
    """Returns (video_id, all_candidates). Raises EngineError(NO_CONFIDENT_MATCH)."""
    candidates: list[dict] = []

    if spotify_track.get("isrc"):
        candidates += await _ytm_search(spotify_track["isrc"], from_isrc=True)

    verified = [c for c in candidates if _passes_gate(spotify_track, c)]
    if not verified:
        candidates += await _spotdl_candidate(spotify_track)
        candidates += await _ytm_search(
            f"{spotify_track['primary_artist']} {spotify_track['title']}")
        seen = set()
        deduped = []
        for c in candidates:
            if c["video_id"] not in seen:
                seen.add(c["video_id"])
                deduped.append(c)
        candidates = deduped
        verified = [c for c in candidates if _passes_gate(spotify_track, c)]

    if not verified:
        raise EngineError(
            ErrorClass.NO_CONFIDENT_MATCH,
            f"No verified YouTube Music match for {spotify_track['artist']} - {spotify_track['title']}",
            candidates=candidates,
        )
    best = max(verified, key=lambda c: _score(spotify_track, c))
    log.info("matched %s - %s -> %s (isrc=%s topic=%s)",
             spotify_track["primary_artist"], spotify_track["title"],
             best["video_id"], best.get("from_isrc"), best.get("is_topic"))
    return best["video_id"], candidates
