"""REST + SSE API. Same-origin behind the frontend's nginx — no CORS, no
auth (the user's external reverse proxy authenticates)."""
import asyncio
import json
import logging
import shutil

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import settings
from .db import db, utcnow
from .engine import spotify, ytdlp_engine
from .engine.governor import governor
from .engine.router import UnsupportedURL, route
from .events import bus, sse_format
from .importer import library_online
from .models import (JobStatus, TrackStatus, row_to_job, row_to_track)
from .plex import plex
from .worker import worker

log = logging.getLogger("mdl.api")
router = APIRouter(prefix="/api/v1")


class JobIn(BaseModel):
    url: str
    force: bool = False


class ResolveIn(BaseModel):
    action: str  # retry | discard | force_import


# ------------------------------------------------------------------ jobs
@router.post("/jobs", status_code=201)
async def create_job(body: JobIn):
    try:
        routed = route(body.url)
    except UnsupportedURL as exc:
        raise HTTPException(422, str(exc))
    job_id = await worker.enqueue(body.url, force=body.force)
    return {"id": job_id, "provider": routed.provider, "source_type": routed.source_type}


@router.get("/jobs")
async def list_jobs(status: str | None = None, limit: int = Query(50, le=200),
                    offset: int = 0):
    if status:
        rows = await db.fetchall(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (status, limit, offset))
    else:
        rows = await db.fetchall(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset))
    return [row_to_job(r).model_dump(exclude={"tracks"}) for r in rows]


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = await db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not job:
        raise HTTPException(404, "job not found")
    tracks = await db.fetchall(
        "SELECT * FROM tracks WHERE job_id = ? ORDER BY position", (job_id,))
    return row_to_job(job, tracks).model_dump()


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    job = await db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not job:
        raise HTTPException(404, "job not found")
    await db.execute(
        "UPDATE tracks SET status = ?, finished_at = ? WHERE job_id = ? AND status = ?",
        (TrackStatus.CANCELLED, utcnow(), job_id, TrackStatus.PENDING))
    await db.execute(
        "UPDATE jobs SET status = ?, finished_at = ? WHERE id = ?",
        (JobStatus.CANCELLED, utcnow(), job_id))
    return {"ok": True}


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str):
    job = await db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not job:
        raise HTTPException(404, "job not found")
    result = await db.fetchall(
        "SELECT id FROM tracks WHERE job_id = ? AND status IN (?,?,?,?)",
        (job_id, TrackStatus.FAILED, TrackStatus.IMPORT_FAILED,
         TrackStatus.NEEDS_REVIEW, TrackStatus.CANCELLED))
    for row in result:
        await db.execute(
            "UPDATE tracks SET status = ?, attempts = 0, error_code = NULL, "
            "error_message = NULL, finished_at = NULL WHERE id = ?",
            (TrackStatus.PENDING, row["id"]))
    await db.execute("UPDATE jobs SET status = ?, finished_at = NULL WHERE id = ?",
                     (JobStatus.DOWNLOADING, job_id))
    worker.wake()
    return {"retried": len(result)}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    """History removal only — never touches library files."""
    job = await db.fetchone("SELECT status FROM jobs WHERE id = ?", (job_id,))
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] in (JobStatus.DOWNLOADING, JobStatus.RESOLVING):
        raise HTTPException(409, "cancel the job before deleting it")
    await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    return {"ok": True}


# ------------------------------------------------------------------ tracks
@router.get("/tracks")
async def list_tracks(status: str = "needs_review", limit: int = Query(100, le=500)):
    rows = await db.fetchall(
        "SELECT * FROM tracks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
        (status, limit))
    return [row_to_track(r).model_dump() for r in rows]


@router.post("/tracks/{track_id}/resolve")
async def resolve_track(track_id: str, body: ResolveIn):
    track = await db.fetchone("SELECT * FROM tracks WHERE id = ?", (track_id,))
    if not track:
        raise HTTPException(404, "track not found")
    if body.action == "retry":
        await db.execute(
            "UPDATE tracks SET status = ?, attempts = 0, error_code = NULL, "
            "error_message = NULL, finished_at = NULL WHERE id = ?",
            (TrackStatus.PENDING, track_id))
        await db.execute("UPDATE jobs SET status = ?, finished_at = NULL WHERE id = ?",
                         (JobStatus.DOWNLOADING, track["job_id"]))
        worker.wake()
    elif body.action == "discard":
        if track["staging_path"]:
            shutil.rmtree(track["staging_path"], ignore_errors=True)
        await db.execute(
            "UPDATE tracks SET status = ?, finished_at = ? WHERE id = ?",
            (TrackStatus.CANCELLED, utcnow(), track_id))
    elif body.action == "force_import":
        # re-queue with the duplicate/exists checks bypassed via job force flag
        await db.execute("UPDATE jobs SET force = 1 WHERE id = ?", (track["job_id"],))
        await db.execute(
            "UPDATE tracks SET status = ?, attempts = 0, error_code = NULL, "
            "error_message = NULL, finished_at = NULL WHERE id = ?",
            (TrackStatus.PENDING, track_id))
        worker.wake()
    else:
        raise HTTPException(422, "action must be retry | discard | force_import")
    return {"ok": True}


# ------------------------------------------------------------------ search / browse
@router.get("/search")
async def search(q: str):
    from functools import partial

    from ytmusicapi import YTMusic
    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(
            None, partial(YTMusic().search, q, filter="songs", limit=10))
    except Exception as exc:
        raise HTTPException(502, f"search failed: {exc}")
    return [{
        "video_id": r.get("videoId"),
        "title": r.get("title"),
        "artists": ", ".join(a.get("name", "") for a in (r.get("artists") or [])),
        "album": (r.get("album") or {}).get("name") if isinstance(r.get("album"), dict) else None,
        "duration": r.get("duration"),
        "url": f"https://music.youtube.com/watch?v={r.get('videoId')}",
    } for r in results if r.get("videoId")]


@router.get("/spotify/resolve")
async def spotify_resolve(url: str):
    """Preview a Spotify URL's contents before queueing."""
    try:
        routed = route(url)
    except UnsupportedURL as exc:
        raise HTTPException(422, str(exc))
    if routed.provider != "spotify":
        raise HTTPException(422, "not a Spotify URL")
    if routed.source_type == "track":
        t = await spotify.get_track(routed.entity_id)
        return {"type": "track", "title": t["title"], "artist": t["artist"], "tracks": [t]}
    if routed.source_type == "album":
        meta, tracks = await spotify.get_album_tracks(routed.entity_id)
        return {"type": "album", **meta, "tracks": tracks}
    if routed.source_type == "playlist":
        meta, tracks = await spotify.get_playlist_tracks(routed.entity_id)
        return {"type": "playlist", **meta, "tracks": tracks}
    meta, albums = await spotify.get_artist_albums(routed.entity_id)
    return {"type": "artist", **meta, "albums": albums}


@router.get("/spotify/playlists")
async def spotify_playlists():
    try:
        return {"connected": spotify.user_client_available(),
                "playlists": await spotify.get_user_playlists()}
    except Exception as exc:
        log.warning("spotify playlists failed: %s", exc)
        return {"connected": False, "playlists": [], "error": str(exc)[:200]}


@router.get("/spotify/auth-url")
async def spotify_auth_url():
    if not settings.spotify_redirect_uri:
        raise HTTPException(422, "SPOTIFY_REDIRECT_URI is not configured in .env")
    return {"url": spotify.make_oauth().get_authorize_url(),
            "redirect_uri": settings.spotify_redirect_uri}


@router.get("/spotify/callback")
async def spotify_callback(code: str):
    from fastapi.responses import RedirectResponse
    import asyncio as _asyncio
    oauth = spotify.make_oauth()
    await _asyncio.get_running_loop().run_in_executor(
        None, lambda: oauth.get_access_token(code, as_dict=False))
    spotify.reset_user_client()
    return RedirectResponse("/")


@router.get("/artists/releases")
async def artist_releases(url: str):
    """YT Music artist release browser (albums/singles with queueable URLs)."""
    from functools import partial

    from ytmusicapi import YTMusic
    loop = asyncio.get_running_loop()
    channel_id = None
    for part in url.split("/"):
        if part.startswith("UC"):
            channel_id = part.split("?")[0]
    if not channel_id:
        info = await ytdlp_engine.extract_info(url, flat=True)
        channel_id = info.get("channel_id") or info.get("uploader_id")
    if not channel_id:
        raise HTTPException(422, "could not resolve artist channel")
    ytm = YTMusic()
    artist = await loop.run_in_executor(None, partial(ytm.get_artist, channel_id))
    releases = []
    for section in ("albums", "singles"):
        for r in (artist.get(section) or {}).get("results") or []:
            if r.get("browseId"):
                releases.append({
                    "browse_id": r["browseId"],
                    "title": r.get("title"),
                    "year": r.get("year"),
                    "type": section[:-1],
                    "url": f"https://music.youtube.com/browse/{r['browseId']}",
                })
    return {"artist": artist.get("name"), "releases": releases}


# ------------------------------------------------------------------ realtime
@router.get("/events")
async def events():
    async def stream():
        q = bus.subscribe()
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(q.get(), timeout=15)
                    yield sse_format(message)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ ops
@router.get("/health")
async def health():
    checks = {}
    try:
        await db.fetchone("SELECT 1")
        checks["db"] = True
    except Exception:
        checks["db"] = False
    checks["staging_writable"] = _writable(settings.staging_dir)
    checks["library_online"] = library_online()
    checks["plex_reachable"] = await plex.reachable()
    ytdlp_engine.cookies.refresh()
    checks["youtube_authenticated"] = ytdlp_engine.cookies.authenticated
    ok = checks["db"] and checks["staging_writable"]
    return {"ok": ok, "checks": checks}


def _writable(path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


@router.get("/stats")
async def stats():
    queue_rows = await db.fetchall(
        "SELECT status, COUNT(*) AS n FROM tracks GROUP BY status")
    usage = shutil.disk_usage(settings.staging_dir)
    staging_used = sum(f.stat().st_size for f in settings.staging_dir.rglob("*")
                       if f.is_file())
    return {
        "tracks_by_status": {r["status"]: r["n"] for r in queue_rows},
        "governor": governor.snapshot(),
        "caps": {"per_hour": settings.tracks_per_hour or None,
                 "per_day": settings.tracks_per_day or None},
        "staging_used_gb": round(staging_used / 1e9, 2),
        "staging_cap_gb": settings.staging_max_gb,
        "disk_free_gb": round(usage.free / 1e9, 1),
        "ytdlp_version": ytdlp_engine.ytdlp_version(),
        "current_track_id": worker.current_track_id,
    }


@router.post("/governor/resume")
async def governor_resume():
    governor.resume()
    worker.wake()
    return {"ok": True}
