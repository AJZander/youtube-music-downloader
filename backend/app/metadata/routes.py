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


class DownloadIn(KeyIn):
    force: bool = False     # Retry: also what an earlier download fetched or skipped


class MatchIn(BaseModel):
    key: str
    release_mbid: str


class IgnoreIn(BaseModel):
    key: str
    ignored: bool = True


class SelectIn(BaseModel):
    """Which issues a bulk action takes: these ids, or every open issue of a
    kind (for one artist, matching a search or a filter), less `exclude`.
    `state`: the list they were picked from (ISSUE_STATES), so "select all"
    takes what that list shows, as far as the action can take it."""
    ids: list[int] | None = None
    kind: str | None = None
    artist_dir: str | None = None
    q: str | None = None
    filter: str | None = None
    exclude: list[int] | None = None
    state: str | None = None


class ApplyIn(SelectIn):
    only_auto: bool = True


class DecideIn(SelectIn):
    decision: str


class IdsIn(SelectIn):
    pass


class NoReleaseIn(BaseModel):
    key: str
    no_release: bool = True


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


async def _album_key(ref: str) -> str:
    """The key of the album `ref` names (its unit id, or a key as old links
    have it). Nothing is done to an album merged into another on a link to
    the one it was: its page follows the redirect instead."""
    found = await units.resolve(ref)
    if found is None:
        return ref
    if found.get("redirect"):
        raise HTTPException(409, f"this album was merged into {found['redirect']['key']}")
    return found["key"]


@router.get("/album")
async def album(key: str):
    out = await views.album(key)
    if out is None:
        raise HTTPException(404, f"no album {key!r}")
    return out


@router.post("/album/recheck")
async def album_recheck(body: KeyIn):
    key = await _album_key(body.key)
    await runner.check_unit(key)
    out = await views.album(key)
    if out is None:
        raise HTTPException(404, "the album is gone")
    return out


@router.post("/album/match")
async def album_match(body: MatchIn):
    if not mb.enabled():
        raise HTTPException(409, "MusicBrainz lookups are off (set MUSICBRAINZ_CONTACT)")
    key = await _album_key(body.key)
    await db.execute("UPDATE md_albums SET manual = 1, release_mbid = ? WHERE key = ?",
                     (body.release_mbid, key))
    await runner.check_unit(key, manual_release=body.release_mbid)
    return await views.album(key)


@router.post("/album/no-release")
async def album_no_release(body: NoReleaseIn):
    """"MusicBrainz has no release for this" (or, undone, look it up again)."""
    key = await _album_key(body.key)
    await db.execute("UPDATE md_albums SET no_release = ?, manual = CASE WHEN ? THEN 0 "
                     "ELSE manual END, next_check = 0 WHERE key = ?",
                     (int(body.no_release), int(body.no_release), key))
    await runner.check_unit(key)
    return await views.album(key)


@router.post("/album/ignore")
async def album_ignore(body: IgnoreIn):
    key = await _album_key(body.key)
    await db.execute("UPDATE md_albums SET ignored = ?, next_check = 0 WHERE key = ?",
                     (int(body.ignored), key))
    await runner.check_unit(key)
    return await views.album(key)


@router.get("/album/candidates")
async def album_candidates(key: str, q: str | None = None):
    rows = await units.load_unit(await _album_key(key))
    if not rows:
        raise HTTPException(404, "no such album")
    if not mb.enabled():
        raise HTTPException(409, "MusicBrainz lookups are off (set MUSICBRAINZ_CONTACT)")
    try:
        return {"items": await search_candidates(rows, q)}
    except mb.MusicBrainzError as exc:
        raise HTTPException(502, str(exc))


@router.post("/album/download-missing")
async def album_download_missing(body: DownloadIn):
    """Queue the album's missing tracks (found on YouTube Music) as one job."""
    from . import missing
    key = await _album_key(body.key)
    record = await db.fetchone("SELECT release_mbid, status FROM md_albums WHERE key = ?",
                               (key,))
    if not record or record["status"] != "resolved" or not record["release_mbid"]:
        raise HTTPException(409, "the album isn't identified yet")
    try:
        result = await missing.queue_download(record["release_mbid"], force=body.force)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc))
    await runner.publish_issues()
    return {**result, "album": await views.album(key)}


@router.get("/album/seed")
async def album_seed(key: str):
    rows = await units.load_unit(await _album_key(key))
    if not rows:
        raise HTTPException(404, "no such album")
    return seed.seed_fields(rows)


# ------------------------------------------------------------------ issues
@router.get("/attention")
async def attention(artist_dir: str | None = None):
    return await views.attention(artist_dir)


# A list's states. "actionable" is what needs the user (Review lists it, and
# its select-all is what Apply takes); "tried" is the Tried bucket.
ISSUE_STATES = {"open": views.OPEN_STATES, "actionable": issues_mod.ATTENTION_STATES,
                "all": ("open", "failed", "queued", "tried", "applied", "dismissed", "superseded",
                        "cleared"),
                "queued": ("queued",), "dismissed": ("dismissed",), "applied": ("applied",),
                "failed": ("failed",), "tried": ("tried",), "superseded": ("superseded",),
                "cleared": ("cleared",)}
# what each bulk action takes (of the list's states, these)
APPLY_TAKES = ("open", "failed")
DISMISS_TAKES = ("open", "failed", "tried")


@router.get("/issues")
async def list_issues(kind: str | None = None, artist_dir: str | None = None,
                      album_key: str | None = None, state: str = "open",
                      q: str | None = None, filter: str | None = None,
                      limit: int = Query(100, le=1000), offset: int = 0):
    total, items = await views.issues_for(
        artist_dir=artist_dir, album_keys=[album_key] if album_key else None,
        kinds=[kind] if kind else None, states=ISSUE_STATES.get(state, views.OPEN_STATES),
        q=q, filter_=filter, limit=limit, offset=offset)
    return {"total": total, "items": items}


async def _selected(body: SelectIn, takes=APPLY_TAKES) -> list[dict]:
    """The issues a bulk action takes (see SelectIn): of the list's states,
    those the action `takes`."""
    if body.ids:
        ids = list(dict.fromkeys(body.ids))
        rows = []
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            rows += [issues_mod.issue_out(r) for r in await db.fetchall(
                f"SELECT * FROM md_issues WHERE id IN ({','.join('?' * len(chunk))})",
                tuple(chunk))]
    elif body.kind:
        listed = ISSUE_STATES.get(body.state, takes) if body.state else takes
        states = tuple(s for s in listed if s in takes)
        rows = []
        if states:
            _, rows = await views.issues_for(artist_dir=body.artist_dir, kinds=[body.kind],
                                             states=states, q=body.q, filter_=body.filter,
                                             limit=100000)
    else:
        raise HTTPException(422, "pass ids, or a kind")
    skip = set(body.exclude or ())
    return [r for r in rows if r["id"] not in skip]


@router.post("/issues/apply")
async def apply_issues(body: ApplyIn):
    if not body.ids and body.kind and not issues_mod.KINDS.get(body.kind, {}).get("applyable"):
        raise HTTPException(422, f"{body.kind} issues can't be applied automatically")
    items = await _selected(body)
    ids = [i["id"] for i in items if i["auto"] or body.ids or not body.only_auto]
    return {"queued": await runner.request_apply(ids)}


@router.post("/issues/decide")
async def decide_issues(body: DecideIn):
    """Accept the best match, or say MusicBrainz has no release, for each
    selected album. Runs in the background, like fixes."""
    if body.decision not in runner.DECISIONS:
        raise HTTPException(422, f"unknown decision {body.decision!r}")
    items = await _selected(body)
    return {"queued": await runner.request_decide([i["id"] for i in items], body.decision)}


@router.post("/issues/triage-matches")
async def triage_matches(body: SelectIn):
    """Check match, settled where the files settle it: accepted where every
    file matched a track and the artist is ours; "not on MusicBrainz" where
    the best match is someone else's song of the same name. The rest stay."""
    scope = body.model_dump()
    sure = await _selected(SelectIn(**{**scope, "kind": "review_match", "filter": "sure"}))
    namesake = await _selected(SelectIn(**{**scope, "kind": "review_match", "filter": "namesake"}))
    accepted = await runner.request_decide([i["id"] for i in sure], "accept_match")
    marked = await runner.request_decide([i["id"] for i in namesake], "no_release")
    return {"accepted": accepted, "no_release": marked}


@router.post("/issues/dismiss")
async def dismiss_issues(body: IdsIn):
    rows = await _selected(body, takes=DISMISS_TAKES) if (body.ids or body.kind) else []
    if not rows:
        return {"dismissed": 0}
    for issue in rows:
        if issue["kind"] in issues_mod.DUP_KINDS:
            try:
                await dupes.dismiss(issue)
            except Exception as exc:
                log.info("exclusion for %s failed: %s", issue["key"], exc)
    ids = [r["id"] for r in rows]
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        await db.execute(
            f"UPDATE md_issues SET state = 'dismissed', updated_at = ? "
            f"WHERE id IN ({','.join('?' * len(chunk))})", (utcnow(), *chunk))
    await runner.publish_issues()
    return {"dismissed": len(rows)}


class ScopeIn(BaseModel):
    kind: str | None = None
    artist_dir: str | None = None


@router.post("/issues/dismiss-failed")
async def dismiss_failed(body: ScopeIn):
    """Every failed fix of a kind (or all kinds), dismissed in one go."""
    _, items = await views.issues_for(artist_dir=body.artist_dir,
                                      kinds=[body.kind] if body.kind else None,
                                      states=("failed",), limit=100000)
    ids = [i["id"] for i in items]
    return await dismiss_issues(IdsIn(ids=ids)) if ids else {"dismissed": 0}


@router.post("/issues/reopen")
async def reopen_issues(body: IdsIn):
    if not body.ids:
        return {"reopened": 0}
    body.ids = list(dict.fromkeys(body.ids))[:5000]
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
    # the batch's albums, and those an undone merge made their own again
    # (the batch names only the album they had joined)
    keys = list(dict.fromkeys([r["album_key"] for r in rows if r["album_key"]]
                              + (result.get("restored_keys") or [])))
    unit_ids = []
    for key in keys:
        try:
            await runner.check_unit(key)
        except Exception:
            log.info("re-check after undo failed for %s", key)
        unit_ids.append(await units.unit_id_of(key))
    from ..events import bus
    bus.publish("library.changed", {"album_keys": keys, "unit_ids": [u for u in unit_ids if u],
                                    "artist_dirs": list(dict.fromkeys(
                                        [r["artist_dir"] for r in rows if r["artist_dir"]]
                                        + [k.split("/", 1)[0] for k in keys]))})
    return result


# ------------------------------------------------------------------ overrides
async def _override_ref(entity: str, ref: str) -> tuple[str, str]:
    """(what an override is stored under, the album key or artist folder to
    check again). An album's is its unit id, so it follows the album."""
    if entity != "album":
        return ref, ref
    key = await _album_key(ref)
    uid = await units.unit_id_of(key)
    if uid is None:
        raise HTTPException(404, f"no album {ref!r}")
    return uid, key


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
    ref, target = await _override_ref(body.entity, body.ref)
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
        (body.entity, ref, body.field, json.dumps(value), utcnow()))
    return await _override_target(body.entity, target)


@router.delete("/overrides")
async def delete_override(body: OverrideIn):
    ref, target = await _override_ref(body.entity, body.ref)
    await db.execute("DELETE FROM md_overrides WHERE entity = ? AND ref = ? AND field = ?",
                     (body.entity, ref, body.field))
    return await _override_target(body.entity, target)


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
        # a unit id (or a key); a merged-away album shows the one it joined
        where = await units.resolve(key) or {}
        found = await art.album_art(where.get("key") or (where.get("redirect") or {}).get("key")
                                    or key)
    elif artist_dir:
        found = await art.artist_art(artist_dir)
    else:
        raise HTTPException(422, "pass key or artist_dir")
    if not found:
        return Response(status_code=404, headers={"Cache-Control": "max-age=3600"})
    data, mime = found
    return Response(content=data, media_type=mime, headers={"Cache-Control": "max-age=86400"})
