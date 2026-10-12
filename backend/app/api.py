"""REST + SSE API. Same-origin behind the frontend's nginx — no CORS, no
auth (the user's external reverse proxy authenticates)."""
import asyncio
import json
import logging
import re
import shutil

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import settings
from .db import db, utcnow
from .engine import spotify, ytdlp_engine
from .engine.governor import governor
from .engine.matcher import normalize
from .engine.router import UnsupportedURL, route
from . import catalog, discover, library, ordering
from .events import bus, sse_format
from .importer import library_online
from .models import (ACTIVE_JOB_STATUSES, JobStatus, TrackStatus, row_to_job,
                     row_to_track)
from .paths import PathBuildError, sanitize_component
from .plex import plex
from .worker import FORCE_GUARDS, FORCE_VALIDATION, worker

log = logging.getLogger("mdl.api")
router = APIRouter(prefix="/api/v1")


class JobIn(BaseModel):
    url: str
    force: bool = False


class ResolveIn(BaseModel):
    action: str  # retry | discard | force_import | force_download | acknowledge


class AcknowledgeIn(BaseModel):
    track_ids: list[str] | None = None   # None = every track currently listed


class MoveIn(BaseModel):
    action: str | None = None        # top | up | down | bottom
    to: int | None = None            # or an absolute 1-based position


class ReorderIn(BaseModel):
    job_ids: list[str]


class TrashIn(BaseModel):
    paths: list[str]
    reason: str = ""
    # Items the user chose to keep side by side. Recorded so the next scan
    # does not offer the same group again.
    keep_together: list[str] | None = None
    scope: str = "album"


class MergeArtistsIn(BaseModel):
    sources: list[str]
    target: str
    rewrite_tags: bool = True


class AlbumPlanIn(BaseModel):
    dirpaths: list[str]
    keepers: list[str]


class ExclusionIn(BaseModel):
    scope: str                       # album | track | artist
    keys: list[str]
    reason: str = ""


class ExclusionDeleteIn(BaseModel):
    ids: list[int]


class PurgeIn(BaseModel):
    batch_id: str | None = None
    older_than_days: int | None = None


class CatalogPickIn(BaseModel):
    browse_id: str
    # The tracks ticked in the browser; None means the whole release.
    video_ids: list[str] | None = None
    title: str | None = None
    artist: str | None = None


class CatalogQueueIn(BaseModel):
    releases: list[CatalogPickIn]
    force: bool = False


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


async def _reopen_job(job_id: str) -> None:
    """A re-queued track only runs if its job is out of the statuses the worker
    filters on (see Worker._step) — otherwise it sits PENDING forever."""
    await db.execute("UPDATE jobs SET status = ?, finished_at = NULL WHERE id = ?",
                     (JobStatus.DOWNLOADING, job_id))


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
        # force is cleared too: a job-wide retry is not a per-track review
        # decision, so it must not inherit one made earlier.
        await db.execute(
            "UPDATE tracks SET status = ?, force = 0, attempts = 0, error_code = NULL, "
            "error_message = NULL, finished_at = NULL WHERE id = ?",
            (TrackStatus.PENDING, row["id"]))
    await db.execute("UPDATE jobs SET status = ?, finished_at = NULL WHERE id = ?",
                     (JobStatus.DOWNLOADING, job_id))
    worker.wake()
    return {"retried": len(result)}


# ------------------------------------------------------------------ queue
@router.get("/queue")
async def get_queue():
    """The active queue in run order, plus the track running right now."""
    return await ordering.snapshot(worker.current_track_id)


@router.post("/queue/reorder")
async def reorder_queue(body: ReorderIn):
    """Apply a whole ordering at once — what a drag-and-drop drop sends."""
    try:
        ids = await ordering.reorder(body.job_ids)
    except ordering.QueueError as exc:
        raise HTTPException(409, str(exc))
    worker.wake()
    bus.publish("queue.reordered", {"job_ids": ids})
    return {"job_ids": ids}


@router.post("/jobs/{job_id}/move")
async def move_job(job_id: str, body: MoveIn):
    try:
        ids = await ordering.move(job_id, action=body.action, to=body.to)
    except ordering.QueueError as exc:
        raise HTTPException(409, str(exc))
    worker.wake()
    bus.publish("queue.reordered", {"job_ids": ids})
    return {"job_ids": ids}


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str):
    try:
        status = await ordering.set_paused(job_id, True)
    except ordering.QueueError as exc:
        raise HTTPException(409, str(exc))
    await worker._publish_job(job_id)
    return {"status": status}


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    try:
        status = await ordering.set_paused(job_id, False)
    except ordering.QueueError as exc:
        raise HTTPException(409, str(exc))
    await worker._publish_job(job_id)
    worker.wake()
    return {"status": status}


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
async def list_tracks(status: str = "needs_review", limit: int = Query(100, le=500),
                      include_acked: bool = False):
    sql = "SELECT * FROM tracks WHERE status = ?"
    if not include_acked:
        sql += " AND review_acked = 0"
    rows = await db.fetchall(sql + " ORDER BY created_at DESC LIMIT ?", (status, limit))
    return [row_to_track(r).model_dump() for r in rows]


def _matched_album(library_path: str | None) -> str | None:
    """Library layout is Artist/Album/file — pull the album the dedup hit."""
    if not library_path:
        return None
    parts = library_path.replace("\\", "/").split("/")
    return parts[-2] if len(parts) >= 2 else None


def _album_key(name: str) -> str:
    """Comparison key for 'is this the same album as the folder it matched'.

    It has to go through sanitize_component first: that is what built the
    folder, and it maps / : ? * etc to '_'. normalize() keeps underscores (they
    are \\w) while stripping the punctuation they replaced, so comparing the raw
    album name reports 'Speakerboxxx/The Love Below' as a different album from
    its own folder 'Speakerboxxx_The Love Below'."""
    try:
        name = sanitize_component(name)
    except PathBuildError:
        pass
    return normalize(str(name).replace("_", " "))


def _different_album(row) -> bool:
    """A skip whose library match sits in another album's folder."""
    matched = _matched_album(row["library_path"])
    return bool(matched and row["album"] and _album_key(matched) != _album_key(row["album"]))


@router.get("/tracks/skipped")
async def list_skipped(limit: int = Query(100, le=500), offset: int = 0,
                       only_different_album: bool = False):
    """Tracks the dedup gate skipped, with what each one actually matched.

    The gate used to match on artist+title and ignore the album, so a track
    on a compilation blocked the studio-album copy; skips from then are
    flagged by album disagreement. It now requires the same album, so new
    skips should not disagree — /tracks/skipped/recheck re-queues old ones."""
    rows = await db.fetchall(
        "SELECT * FROM tracks WHERE status = ? AND review_acked = 0 "
        "ORDER BY created_at DESC", (TrackStatus.SKIPPED_DUPLICATE,))

    items = []
    for r in rows:
        track = row_to_track(r).model_dump()
        track["matched_album"] = _matched_album(r["library_path"])
        track["matched_path"] = r["library_path"]
        track["different_album"] = _different_album(r)
        items.append(track)

    differing = sum(1 for t in items if t["different_album"])
    if only_different_album:
        items = [t for t in items if t["different_album"]]
    return {"total": len(rows), "different_album": differing,
            "returned": len(items[offset:offset + limit]),
            "items": items[offset:offset + limit]}


async def _cross_album_skips() -> list:
    """Every skip — reviewed or not — that matched a song on another album,
    outside cancelled jobs. Under the same-album rule these should not have
    been skipped."""
    rows = await db.fetchall(
        "SELECT t.* FROM tracks t JOIN jobs j ON j.id = t.job_id "
        "WHERE t.status = ? AND j.status != ? ORDER BY j.created_at, t.position",
        (TrackStatus.SKIPPED_DUPLICATE, JobStatus.CANCELLED))
    return [r for r in rows if _different_album(r)]


@router.get("/tracks/skipped/recheck")
async def skipped_recheck_preview():
    """How many earlier skips the same-album rule would download now."""
    rows = await _cross_album_skips()
    return {"tracks": len(rows), "jobs": len({r["job_id"] for r in rows})}


@router.post("/tracks/skipped/recheck")
async def skipped_recheck():
    """Put skips that matched another album back in the queue, for the
    same-album gate to judge again. Not forced: one that really is on the same
    album is skipped again. Their jobs rejoin the queue at the end, so they do
    not jump ahead of whatever is already waiting."""
    rows = await _cross_album_skips()
    if not rows:
        return {"requeued": 0, "jobs": 0}
    await db.executemany(
        "UPDATE tracks SET status = ?, force = 0, review_acked = 0, attempts = 0, "
        "error_code = NULL, error_message = NULL, library_path = NULL, "
        "finished_at = NULL WHERE id = ?",
        [(TrackStatus.PENDING, r["id"]) for r in rows])
    job_ids = list(dict.fromkeys(r["job_id"] for r in rows))
    last = (await db.fetchone(
        "SELECT COALESCE(MAX(queue_position), 0) AS n FROM jobs"))["n"]
    for offset, job_id in enumerate(job_ids, start=1):
        # Finished jobs reopen at the back; one still in the queue (or
        # paused) keeps its place and simply has more to do.
        await db.execute(
            "UPDATE jobs SET status = ?, finished_at = NULL, queue_position = ? "
            "WHERE id = ? AND status IN (?, ?, ?)",
            (JobStatus.DOWNLOADING, last + offset, job_id,
             JobStatus.COMPLETED, JobStatus.PARTIAL, JobStatus.FAILED))
    worker.wake()
    bus.publish("queue.reordered", {})
    return {"requeued": len(rows), "jobs": len(job_ids)}


@router.post("/tracks/skipped/acknowledge")
async def acknowledge_skipped(body: AcknowledgeIn):
    """Mark skips as reviewed so they drop off the list. History is untouched —
    the track stays a skip for job accounting and stats."""
    if body.track_ids:
        for track_id in body.track_ids:
            await db.execute(
                "UPDATE tracks SET review_acked = 1 WHERE id = ? AND status = ?",
                (track_id, TrackStatus.SKIPPED_DUPLICATE))
        return {"acknowledged": len(body.track_ids)}
    row = await db.fetchone(
        "SELECT COUNT(*) AS n FROM tracks WHERE status = ? AND review_acked = 0",
        (TrackStatus.SKIPPED_DUPLICATE,))
    await db.execute("UPDATE tracks SET review_acked = 1 WHERE status = ?",
                     (TrackStatus.SKIPPED_DUPLICATE,))
    return {"acknowledged": row["n"]}


@router.post("/tracks/{track_id}/resolve")
async def resolve_track(track_id: str, body: ResolveIn):
    track = await db.fetchone("SELECT * FROM tracks WHERE id = ?", (track_id,))
    if not track:
        raise HTTPException(404, "track not found")
    if body.action == "retry":
        await db.execute(
            "UPDATE tracks SET status = ?, force = 0, attempts = 0, error_code = NULL, "
            "error_message = NULL, finished_at = NULL WHERE id = ?",
            (TrackStatus.PENDING, track_id))
        await _reopen_job(track["job_id"])
        worker.wake()
    elif body.action == "discard":
        if track["staging_path"]:
            shutil.rmtree(track["staging_path"], ignore_errors=True)
        await db.execute(
            "UPDATE tracks SET status = ?, force = 0, finished_at = ? WHERE id = ?",
            (TrackStatus.CANCELLED, utcnow(), track_id))
    elif body.action == "force_import":
        # Take the reviewed file as-is. This is the ONE action that may silence
        # validation, because the file is sitting in quarantine and a human has
        # just looked at it. Integrity checks still apply.
        await db.execute(
            "UPDATE tracks SET status = ?, force = ?, attempts = 0, error_code = NULL, "
            "error_message = NULL, finished_at = NULL WHERE id = ?",
            (TrackStatus.PENDING, FORCE_VALIDATION, track_id))
        await _reopen_job(track["job_id"])
        worker.wake()
    elif body.action == "force_download":
        # "That wasn't a duplicate" — download it despite the library match.
        # Guards only: this says nothing about the audio, which has not been
        # fetched yet, so a bad file still lands in Needs Review as normal.
        await db.execute(
            "UPDATE tracks SET status = ?, force = ?, review_acked = 0, attempts = 0, "
            "error_code = NULL, error_message = NULL, library_path = NULL, "
            "finished_at = NULL WHERE id = ?",
            (TrackStatus.PENDING, FORCE_GUARDS, track_id))
        await _reopen_job(track["job_id"])
        worker.wake()
    elif body.action == "acknowledge":
        await db.execute("UPDATE tracks SET review_acked = 1 WHERE id = ?", (track_id,))
    else:
        raise HTTPException(
            422, "action must be retry | discard | force_import | force_download | acknowledge")
    return {"ok": True}


# ------------------------------------------------------------------ search / browse
@router.get("/search")
async def search(q: str):
    try:
        results = await catalog.call("search", q, filter="songs", limit=10)
    except catalog.CatalogError as exc:
        raise HTTPException(exc.status, f"search failed: {exc}")
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


# ------------------------------------------------------------------ artist catalogue
@router.get("/catalog/search")
async def catalog_search(q: str):
    """Artists matching a name — the way in when there is no link to paste."""
    try:
        return {"artists": await catalog.search_artists(q)}
    except catalog.CatalogError as exc:
        raise HTTPException(exc.status, str(exc))


@router.get("/catalog/artist")
async def catalog_artist(ref: str, refresh: bool = False):
    """An artist's whole discography. `ref` is a channel URL, an @handle, a
    channel id or an album link. Served from cache when it can be; tracklists
    not loaded yet arrive in the background, announced as catalog.progress."""
    try:
        view = await catalog.artist_view(ref, refresh=refresh)
    except catalog.CatalogError as exc:
        raise HTTPException(exc.status, str(exc))
    placeholders = ",".join("?" * len(ACTIVE_JOB_STATUSES))
    waiting = {r["source_url"] for r in await db.fetchall(
        f"SELECT source_url FROM jobs WHERE status IN ({placeholders})",
        tuple(ACTIVE_JOB_STATUSES))}
    for release in view["releases"]:
        release["queued"] = release["url"] in waiting
    return view


@router.get("/catalog/album/{browse_id}")
async def catalog_album(browse_id: str, refresh: bool = False):
    """One tracklist now, ahead of the prefetch — an album someone expanded."""
    try:
        return await catalog.album_view(browse_id, refresh=refresh)
    except catalog.CatalogError as exc:
        raise HTTPException(exc.status, str(exc))


@router.post("/catalog/queue", status_code=201)
async def catalog_queue(body: CatalogQueueIn):
    """Queue releases picked in the browser, one job each, in the order given.

    Tracks already in the library on that album are left out here (see
    catalog.plan_queue), so they neither download again nor pile up in the
    Skipped list. `force` keeps them, and the job then bypasses the dedup gate
    as well.

    A release already waiting in the queue with the same selection is not
    queued twice — picking an artist's albums again after adding one more is
    the normal way to use this, not a mistake."""
    if not body.releases:
        raise HTTPException(422, "nothing selected")
    return await _queue_releases(body.releases, force=body.force)


async def _queue_releases(releases: list[CatalogPickIn], *, force: bool,
                          force_ticked: bool = True) -> dict:
    placeholders = ",".join("?" * len(ACTIVE_JOB_STATUSES))
    existing = {(r["source_url"], r["track_filter"]) for r in await db.fetchall(
        f"SELECT source_url, track_filter FROM jobs WHERE status IN ({placeholders})",
        tuple(ACTIVE_JOB_STATUSES))}

    queued, skipped, picks = [], [], []
    for pick in releases:
        if not re.fullmatch(r"MPREb_[A-Za-z0-9_-]+", pick.browse_id):
            skipped.append({"browse_id": pick.browse_id, "reason": "not a YouTube Music release"})
        elif pick.video_ids is not None and not pick.video_ids:
            skipped.append({"browse_id": pick.browse_id, "reason": "no tracks selected"})
        else:
            picks.append(pick)
    plans = await catalog.plan_queue([p.model_dump() for p in picks],
                                     include_owned=force, force_ticked=force_ticked)

    for pick, plan in zip(picks, plans):
        if plan["error"]:
            skipped.append({"browse_id": pick.browse_id, "reason": plan["error"]})
            continue
        if not plan["tracks"]:
            skipped.append({"browse_id": pick.browse_id,
                            "reason": "already in your library" if plan["left_out"]
                            else "no playable tracks", "left_out": plan["left_out"]})
            continue
        url = f"https://music.youtube.com/browse/{pick.browse_id}"
        keep, forced = plan["video_ids"], plan.get("force_ids") or []
        if forced:
            # Owned tracks ticked on purpose: download them past the dedup gate.
            track_filter = json.dumps({"keep": sorted(keep) if keep is not None else None,
                                       "force": sorted(forced)})
        else:
            track_filter = json.dumps(sorted(keep)) if keep is not None else None
        if (url, track_filter) in existing:
            skipped.append({"browse_id": pick.browse_id, "reason": "already in the queue"})
            continue
        job_id = await worker.enqueue(url, force=force, title=pick.title,
                                      artist=pick.artist, track_filter=track_filter)
        existing.add((url, track_filter))
        queued.append({"browse_id": pick.browse_id, "job_id": job_id,
                       "tracks": plan["tracks"], "left_out": plan["left_out"],
                       "forced": len(forced)})
    return {"queued": queued, "skipped": skipped,
            "left_out": sum(p["left_out"] for p in plans if not p["error"])}


async def _active_urls() -> set[str]:
    placeholders = ",".join("?" * len(ACTIVE_JOB_STATUSES))
    return {r["source_url"] for r in await db.fetchall(
        f"SELECT source_url FROM jobs WHERE status IN ({placeholders})",
        tuple(ACTIVE_JOB_STATUSES))}


# ------------------------------------------------------------------ discover
class SongIn(BaseModel):
    video_id: str
    title: str
    artist: str | None = None
    album: str | None = None
    album_id: str | None = None


class SongsQueueIn(BaseModel):
    songs: list[SongIn]
    force: bool = False


@router.get("/discover/status")
async def discover_status():
    return await discover.status()


@router.post("/discover/scan")
async def discover_scan():
    """Look for due work now rather than at the next idle check."""
    discover.scanner.wake()
    return await discover.status()


@router.get("/discover/releases")
async def discover_releases(years: int = Query(2, ge=1, le=10), source: str | None = None,
                            include_owned: bool = False):
    releases = await discover.new_releases(years=years, source=source,
                                           include_owned=include_owned)
    waiting = await _active_urls()
    for release in releases:
        release["queued"] = release["url"] in waiting
    return {"releases": releases, "status": await discover.status()}


@router.post("/discover/releases/{browse_id}/dismiss")
async def discover_dismiss_release(browse_id: str):
    await discover.dismiss_release(browse_id)
    return {"ok": True}


@router.get("/discover/artists")
async def discover_artists(limit: int = Query(40, le=200)):
    return {"artists": await discover.suggested_artists(limit)}


@router.post("/discover/artists/{channel_id}/dismiss")
async def discover_dismiss_artist(channel_id: str, name: str = ""):
    await discover.dismiss_artist(channel_id, name)
    return {"ok": True}


@router.get("/discover/charts")
async def discover_charts(country: str | None = None):
    try:
        return await discover.charts(country)
    except catalog.CatalogError as exc:
        raise HTTPException(exc.status, str(exc))


@router.get("/discover/genres")
async def discover_genres():
    try:
        return await discover.genres()
    except catalog.CatalogError as exc:
        raise HTTPException(exc.status, str(exc))


@router.get("/discover/genres/{title}")
async def discover_genre(title: str, refresh: bool = False):
    try:
        doc = await discover.genre(title, refresh=refresh)
    except catalog.CatalogError as exc:
        raise HTTPException(exc.status, str(exc))
    waiting = await _active_urls()
    for album in doc["albums"]:
        album["queued"] = f"https://music.youtube.com/browse/{album['browse_id']}" in waiting
    return doc


@router.post("/discover/queue-songs", status_code=201)
async def discover_queue_songs(body: SongsQueueIn):
    """Queue chart songs — each through its album where it can be found
    there (album audio and tags), as the bare video where it cannot."""
    if not body.songs:
        raise HTTPException(422, "nothing selected")
    picks, loose = await discover.plan_songs([s.model_dump() for s in body.songs])
    result = await _queue_releases([CatalogPickIn(**p) for p in picks], force=body.force,
                                   force_ticked=False) if picks else {
        "queued": [], "skipped": [], "left_out": 0}
    videos = []
    for song in loose:
        job_id = await worker.enqueue(
            f"https://music.youtube.com/watch?v={song['video_id']}", force=body.force,
            title=song["title"], artist=song["artist"])
        videos.append({"video_id": song["video_id"], "job_id": job_id})
    return {**result, "videos": videos}


# ------------------------------------------------------------------ library
@router.get("/library/overview")
async def library_overview():
    return await library.overview()


@router.post("/library/scan")
async def library_scan(full: bool = False):
    """Kick off a background rescan. Returns immediately — a cold full scan of
    this library reads tags off 60k files over CIFS and takes minutes."""
    if library.scan_state.running:
        raise HTTPException(409, "a scan is already running")

    async def run():
        last = 0.0

        def progress(done, total):
            # The scan runs for minutes; the UI needs a pulse, not 60k of them.
            nonlocal last
            now = asyncio.get_running_loop().time()
            if now - last > 2:
                last = now
                bus.publish("library.scan", library.scan_state.snapshot())

        try:
            await library.scan(full=full, progress_cb=progress)
        except Exception as exc:
            log.warning("library scan ended with an error: %s", exc)
        bus.publish("library.scan", library.scan_state.snapshot())

    asyncio.create_task(run())
    return {"started": True, "full": full}


@router.get("/library/scan")
async def library_scan_status():
    return library.scan_state.snapshot()


@router.get("/library/duplicates/{kind}")
async def library_duplicates(kind: str, limit: int = Query(100, le=500),
                             offset: int = 0, confident_only: bool = False,
                             include_covered: bool = False):
    if kind not in ("albums", "tracks", "artists"):
        raise HTTPException(422, "kind must be albums | tracks | artists")
    if library.scan_state.running:
        raise HTTPException(409, "wait for the library scan to finish")
    return await library.duplicates(kind, limit=limit, offset=offset,
                                    confident_only=confident_only,
                                    include_covered=include_covered)


@router.post("/library/duplicates/albums/plan")
async def library_album_plan(body: AlbumPlanIn):
    """Recompute the keep/remove split for a chosen keeper."""
    try:
        return await library.plan_album_merge(body.dirpaths, body.keepers)
    except library.LibraryError as exc:
        raise HTTPException(422, str(exc))


@router.get("/library/artists")
async def library_artists(q: str | None = None, sort: str = "name",
                          limit: int = Query(500, le=2000), offset: int = 0):
    return await library.list_artists(q=q, sort=sort, limit=limit, offset=offset)


@router.get("/library/albums")
async def library_albums(artist_dir: str):
    try:
        return await library.list_albums(artist_dir)
    except library.LibraryError as exc:
        raise HTTPException(404, str(exc))


@router.get("/library/tracks")
async def library_album_tracks(dirpath: str):
    try:
        return await library.list_album_tracks(dirpath)
    except library.LibraryError as exc:
        raise HTTPException(404, str(exc))


@router.post("/library/trash")
async def library_trash(body: TrashIn):
    """Remove library files, and remember what was deliberately kept.

    Keeping two copies of an album is a judgement about those two copies, so
    it is recorded here rather than left for the user to make again on the
    next scan."""
    if not body.paths and not body.keep_together:
        raise HTTPException(422, "nothing to do")
    result = {"trashed": 0, "freed_bytes": 0, "emptied_folders": [], "failures": []}
    try:
        if body.paths:
            result = await library.trash_paths(body.paths, body.reason)
        if body.keep_together and len(body.keep_together) > 1:
            result["kept_together"] = await library.add_exclusions(
                body.scope, body.keep_together,
                body.reason or "kept side by side")
    except library.LibraryError as exc:
        raise HTTPException(409, str(exc))
    return result


@router.post("/library/exclusions")
async def library_add_exclusion(body: ExclusionIn):
    """Mark items as not duplicates of each other, permanently."""
    if body.scope not in ("album", "track", "artist"):
        raise HTTPException(422, "scope must be album | track | artist")
    try:
        pairs = await library.add_exclusions(body.scope, body.keys, body.reason)
    except library.LibraryError as exc:
        raise HTTPException(422, str(exc))
    return {"pairs": pairs}


@router.get("/library/exclusions")
async def library_list_exclusions():
    return await library.list_exclusions()


@router.delete("/library/exclusions")
async def library_delete_exclusions(body: ExclusionDeleteIn):
    """Undo a decision, so the group can be reported again."""
    return {"removed": await library.remove_exclusions(body.ids)}


@router.get("/library/trash")
async def library_trash_list(include_restored: bool = False):
    return await library.list_trash(include_restored=include_restored)


@router.post("/library/trash/{batch_id}/restore")
async def library_trash_restore(batch_id: str):
    try:
        return await library.restore_batch(batch_id)
    except library.LibraryError as exc:
        raise HTTPException(409, str(exc))


@router.post("/library/trash/purge")
async def library_trash_purge(body: PurgeIn):
    if body.batch_id is None and body.older_than_days is None:
        raise HTTPException(
            422, "pass batch_id, or older_than_days=0 to purge everything")
    return await library.purge_trash(batch_id=body.batch_id,
                                     older_than_days=body.older_than_days)


@router.post("/library/artists/merge")
async def library_merge_artists(body: MergeArtistsIn):
    try:
        return await library.merge_artist_folders(
            body.sources, body.target, rewrite_tags=body.rewrite_tags)
    except library.LibraryError as exc:
        raise HTTPException(409, str(exc))


@router.post("/library/plex/empty-trash")
async def library_plex_empty_trash():
    """Plex keeps deleted tracks as ghosts until its own trash is emptied."""
    return {"ok": await plex.empty_trash()}


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
