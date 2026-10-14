"""HTTP API of the unified Library page (artists, albums, issues, history,
the metadata run)."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from ..db import db, utcnow
from . import art, dupes, issues as issues_mod, musicbrainz as mb, seed, units, views
from .apply import revert
from .resolver import search_candidates
from .runner import runner
from .vocab import display_genre, vocab

log = logging.getLogger("mdl.routes")
router = APIRouter(prefix="/api/v1/library")


class RunIn(BaseModel):
    full: bool = True
    report_only: bool = True


class AutoApplyIn(BaseModel):
    enabled: bool


class KeyIn(BaseModel):
    key: str


class MatchIn(BaseModel):
    key: str
    release_mbid: str


class IgnoreIn(BaseModel):
    key: str
    ignored: bool = True


class ApplyIn(BaseModel):
    ids: list[int] | None = None
    kind: str | None = None
    artist_dir: str | None = None
    only_auto: bool = True


class IdsIn(BaseModel):
    ids: list[int]


class OverrideIn(BaseModel):
    entity: str
    ref: str
    field: str
    value: object | None = None


OVERRIDE_FIELDS = {"album": {"genres", "moods", "album", "album_artist", "year"},
                   "artist": {"genres", "moods"}}


# ------------------------------------------------------------------ overview / run
@router.get("/overview")
async def overview():
    return await views.overview()


@router.get("/metadata/status")
async def metadata_status():
    return await runner.status()


@router.post("/metadata/run")
async def metadata_run(body: RunIn):
    await runner.request_run(body.full, body.report_only)
    return await runner.status()


@router.post("/metadata/pause")
async def metadata_pause():
    runner.pause()
    await runner.publish(force=True)
    return await runner.status()


@router.post("/metadata/resume")
async def metadata_resume():
    runner.resume()
    await runner.publish(force=True)
    return await runner.status()


@router.post("/metadata/cancel")
async def metadata_cancel():
    runner.cancel()
    return await runner.status()


@router.post("/metadata/auto-apply")
async def metadata_auto_apply(body: AutoApplyIn):
    await runner.set_auto_apply(body.enabled)
    await runner.publish(force=True)
    return await runner.status()


# ------------------------------------------------------------------ browse
@router.get("/artists")
async def artists(q: str | None = None, sort: str = "name", filter: str = "all",
                  limit: int = Query(5000, le=10000), offset: int = 0):
    return await views.artists(q=q, sort=sort, filter_=filter, limit=limit, offset=offset)


@router.get("/artist")
async def artist(artist_dir: str):
    out = await views.artist(artist_dir)
    if out is None:
        raise HTTPException(404, f"no indexed files under {artist_dir!r}")
    return out


@router.get("/album")
async def album(key: str):
    out = await views.album(key)
    if out is None:
        raise HTTPException(404, f"no album {key!r}")
    return out


@router.post("/album/recheck")
async def album_recheck(body: KeyIn):
    await runner.check_unit(body.key)
    out = await views.album(body.key)
    if out is None:
        raise HTTPException(404, "the album is gone")
    return out


@router.post("/album/match")
async def album_match(body: MatchIn):
    if not mb.enabled():
        raise HTTPException(409, "MusicBrainz lookups are off (set MUSICBRAINZ_CONTACT)")
    await db.execute("UPDATE md_albums SET manual = 1, release_mbid = ? WHERE key = ?",
                     (body.release_mbid, body.key))
    await runner.check_unit(body.key, manual_release=body.release_mbid)
    return await views.album(body.key)


@router.post("/album/ignore")
async def album_ignore(body: IgnoreIn):
    await db.execute("UPDATE md_albums SET ignored = ?, next_check = 0 WHERE key = ?",
                     (int(body.ignored), body.key))
    await runner.check_unit(body.key)
    return await views.album(body.key)


@router.get("/album/candidates")
async def album_candidates(key: str, q: str | None = None):
    rows = await units.load_unit(key)
    if not rows:
        raise HTTPException(404, "no such album")
    if not mb.enabled():
        raise HTTPException(409, "MusicBrainz lookups are off (set MUSICBRAINZ_CONTACT)")
    try:
        return {"items": await search_candidates(rows, q)}
    except mb.MusicBrainzError as exc:
        raise HTTPException(502, str(exc))


@router.post("/album/download-missing")
async def album_download_missing(body: KeyIn):
    """Queue the album's missing tracks (found on YouTube Music) as one job."""
    from . import missing
    record = await db.fetchone("SELECT release_mbid, status FROM md_albums WHERE key = ?",
                               (body.key,))
    if not record or record["status"] != "resolved" or not record["release_mbid"]:
        raise HTTPException(409, "the album isn't identified yet")
    try:
        result = await missing.queue_download(record["release_mbid"])
    except RuntimeError as exc:
        raise HTTPException(404, str(exc))
    await runner.publish_issues()
    return {**result, "album": await views.album(body.key)}


@router.get("/album/seed")
async def album_seed(key: str):
    rows = await units.load_unit(key)
    if not rows:
        raise HTTPException(404, "no such album")
    return seed.seed_fields(rows)


# ------------------------------------------------------------------ issues
@router.get("/attention")
async def attention(artist_dir: str | None = None):
    return await views.attention(artist_dir)


@router.get("/issues")
async def list_issues(kind: str | None = None, artist_dir: str | None = None,
                      album_key: str | None = None, state: str = "open",
                      limit: int = Query(100, le=1000), offset: int = 0):
    states = {"open": views.OPEN_STATES, "all": ("open", "failed", "queued", "applied", "dismissed"),
              "dismissed": ("dismissed",), "applied": ("applied",), "failed": ("failed",)}
    total, items = await views.issues_for(
        artist_dir=artist_dir, album_keys=[album_key] if album_key else None,
        kinds=[kind] if kind else None, states=states.get(state, views.OPEN_STATES),
        limit=limit, offset=offset)
    return {"total": total, "items": items}


@router.post("/issues/apply")
async def apply_issues(body: ApplyIn):
    if body.ids:
        ids = body.ids
    elif body.kind:
        if not issues_mod.KINDS.get(body.kind, {}).get("applyable"):
            raise HTTPException(422, f"{body.kind} issues can't be applied automatically")
        _, items = await views.issues_for(artist_dir=body.artist_dir, kinds=[body.kind],
                                          states=("open", "failed"), limit=100000)
        ids = [i["id"] for i in items if i["auto"] or not body.only_auto]
    else:
        raise HTTPException(422, "pass ids, or a kind")
    return {"queued": await runner.request_apply(ids)}


@router.post("/issues/dismiss")
async def dismiss_issues(body: IdsIn):
    if not body.ids:
        return {"dismissed": 0}
    rows = [issues_mod.issue_out(r) for r in await db.fetchall(
        f"SELECT * FROM md_issues WHERE id IN ({','.join('?' * len(body.ids))})",
        tuple(body.ids))]
    for issue in rows:
        if issue["kind"] in issues_mod.DUP_KINDS:
            try:
                await dupes.dismiss(issue)
            except Exception as exc:
                log.info("exclusion for %s failed: %s", issue["key"], exc)
    await db.execute(
        f"UPDATE md_issues SET state = 'dismissed', updated_at = ? "
        f"WHERE id IN ({','.join('?' * len(body.ids))})", (utcnow(), *body.ids))
    await runner.publish_issues()
    return {"dismissed": len(rows)}


@router.post("/issues/reopen")
async def reopen_issues(body: IdsIn):
    if not body.ids:
        return {"reopened": 0}
    await db.execute(
        f"UPDATE md_issues SET state = 'open', error = NULL, updated_at = ? "
        f"WHERE id IN ({','.join('?' * len(body.ids))})", (utcnow(), *body.ids))
    await runner.publish_issues()
    return {"reopened": len(body.ids)}


# ------------------------------------------------------------------ history
@router.get("/changes")
async def list_changes(artist_dir: str | None = None, album_key: str | None = None,
                       limit: int = Query(50, le=500)):
    return {"items": await views.changes(album_key=album_key, artist_dir=artist_dir, limit=limit)}


@router.post("/changes/{batch_id}/revert")
async def revert_batch(batch_id: str):
    rows = await db.fetchall("SELECT DISTINCT album_key, artist_dir FROM md_changes "
                             "WHERE batch_id = ?", (batch_id,))
    result = await revert(batch_id)
    for r in rows:
        if r["album_key"]:
            try:
                await runner.check_unit(r["album_key"])
            except Exception:
                log.info("re-check after undo failed for %s", r["album_key"])
    from ..events import bus
    bus.publish("library.changed", {"album_keys": [r["album_key"] for r in rows if r["album_key"]],
                                    "artist_dirs": [r["artist_dir"] for r in rows if r["artist_dir"]]})
    return result


# ------------------------------------------------------------------ overrides
async def _override_target(entity: str, ref: str):
    if entity == "album":
        await runner.check_unit(ref)
        return await views.album(ref)
    await runner.check_artist(ref)
    return await views.artist(ref)


@router.put("/overrides")
async def put_override(body: OverrideIn):
    if body.field not in OVERRIDE_FIELDS.get(body.entity, set()):
        raise HTTPException(422, f"{body.entity} field {body.field!r} can't be pinned")
    value = body.value
    if body.field in ("genres", "moods"):
        if not isinstance(value, list):
            raise HTTPException(422, "genres and moods are lists")
        value = [str(v).strip() for v in value if str(v).strip()]
        if body.field == "genres":
            value = [display_genre(v) for v in value]
    await db.execute(
        "INSERT INTO md_overrides (entity, ref, field, value, created_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(entity, ref, field) DO UPDATE SET value = excluded.value",
        (body.entity, body.ref, body.field, json.dumps(value), utcnow()))
    return await _override_target(body.entity, body.ref)


@router.delete("/overrides")
async def delete_override(body: OverrideIn):
    await db.execute("DELETE FROM md_overrides WHERE entity = ? AND ref = ? AND field = ?",
                     (body.entity, body.ref, body.field))
    return await _override_target(body.entity, body.ref)


# ------------------------------------------------------------------ vocab / art
@router.get("/genres")
async def genres(q: str | None = None, limit: int = 50):
    names = await vocab.genre_list()
    if q:
        needle = q.strip().casefold()
        starts = [n for n in names if n.casefold().startswith(needle)]
        contains = [n for n in names if needle in n.casefold() and n not in starts]
        names = starts + contains
    return {"items": names[:limit]}


@router.get("/moods")
async def moods(q: str | None = None, limit: int = 50):
    names = sorted((await vocab.moods()).values())
    if q:
        needle = q.strip().casefold()
        names = [n for n in names if needle in n.casefold()]
    return {"items": names[:limit]}


@router.get("/art")
async def get_art(key: str | None = None, artist_dir: str | None = None):
    if key:
        found = await art.album_art(key)
    elif artist_dir:
        found = await art.artist_art(artist_dir)
    else:
        raise HTTPException(422, "pass key or artist_dir")
    if not found:
        return Response(status_code=404, headers={"Cache-Control": "max-age=3600"})
    data, mime = found
    return Response(content=data, media_type=mime, headers={"Cache-Control": "max-age=86400"})
