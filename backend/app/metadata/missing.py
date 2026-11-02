"""Missing tracks: what an identified release has that the library doesn't,
and getting them.

Counted per *release*, across every folder identified as it — a two-disc
album filed as `CD1` and `CD2` is one album, and neither disc is "missing"
the other. Video media (a bonus DVD) are not tracks you can download.

Only an album is "missing" tracks: when the library holds at least half of
the release, or the files came from downloading the whole album. One song
off a 13-track album is a song, not an album with 12 gaps; the album view
still shows the rest, and "Download the rest" works the same.

Downloading is never automatic: the issue says what is missing, and
"Download missing" finds the album on YouTube Music and queues exactly those
tracks, as one job, through the normal downloader (paced, deduplicated,
validated). When they land, the album's next check files them in.
"""
from __future__ import annotations

import json
import logging
import re

from .. import identity
from ..db import db, utcnow
from ..engine.matcher import normalize
from ..identity import album_identity, strict_key
from ..models import ACTIVE_JOB_STATUSES, TrackStatus
from . import musicbrainz as mb
from .issues import planned, save

log = logging.getLogger("mdl.missing")

_VIDEO = re.compile(r"dvd|blu-?ray|vhs|video|hd-?dvd|umd", re.I)


class Busy(RuntimeError):
    """YouTube Music can't be asked right now (this hour's browsing budget is
    spent, or YouTube asked us to slow down): no answer about the album."""


def _asking_failed(exc, what: str) -> RuntimeError:
    from .. import catalog
    if isinstance(exc, catalog.CatalogError) and exc.status == 503:
        return Busy(str(exc))
    return RuntimeError(f"{what}: {exc}")


async def _units(release_mbid: str) -> list[dict]:
    return [dict(r) for r in await db.fetchall(
        "SELECT key, artist_dir, desired FROM md_albums WHERE release_mbid = ? "
        "AND status = 'resolved' AND ignored = 0 AND desired IS NOT NULL", (release_mbid,))]


async def compute(release_mbid: str) -> dict | None:
    units = await _units(release_mbid)
    if not units:
        return None
    try:
        rel = await mb.release(release_mbid)
    except mb.MusicBrainzError:
        return None
    if not rel:
        return None
    have: set[str] = set()
    paths: list[str] = []
    desired = None
    held_by: dict[str, int] = {}
    for unit in units:
        desired = json.loads(unit["desired"])
        paths += list(desired["tracks"])
        held_by[unit["key"]] = 0
        for track in desired["tracks"].values():
            if track.get("matched") and track.get("releasetrack_mbid"):
                have.add(track["releasetrack_mbid"])
                held_by[unit["key"]] += 1
    audio_discs = {m.get("position") or 1 for m in rel.get("media") or []
                   if not _VIDEO.search(m.get("format") or "")}
    tracks = [t for t in mb.release_tracks(rel) if t["disc"] in audio_discs]
    for position, t in enumerate(tracks, start=1):
        t["position"] = position     # running number across discs, as YouTube Music counts
    # what a track is without saying so: on a live release, or its "Live at
    # the Crocodile Cafe" disc, YouTube Music's "Song (Live)" is the track
    types = (rel.get("release-group") or {}).get("secondary-types") or []
    missing = [{"disc": t["disc"], "number": t["number"], "position": t["position"],
                "title": t["title"],
                "length": t["length"], "recording_mbid": t["recording_id"],
                "releasetrack_mbid": t["track_id"],
                "artist": mb.credit_string(t["credits"]) or None,
                "context": sorted(identity.release_context(rel.get("title"), types,
                                                           t.get("medium_title")))}
               for t in tracks if t["track_id"] not in have]
    held = len(tracks) - len(missing)
    return {"release_mbid": release_mbid, "album": (desired or {}).get("album") or rel.get("title"),
            "artist": (desired or {}).get("albumartist"),
            "year": (desired or {}).get("original_year"),
            "total": len(tracks), "have": held, "missing": missing,
            "partial": bool(missing) and 2 * held < len(tracks)
            and not await _album_download(paths),
            "multi_disc": len(audio_discs) > 1,
            "units": sorted(u["key"] for u in units),
            # where downloaded tracks go: the folder holding most of it
            "main_unit": max(sorted(held_by), key=lambda k: held_by[k]),
            "artist_dirs": sorted({u["artist_dir"] for u in units if u["artist_dir"]})}


async def _album_download(paths: list[str]) -> bool:
    """Whether any of these files came from downloading the whole album
    (the download may have spelt the path in another case)."""
    for i in range(0, len(paths), 400):
        chunk = paths[i:i + 400]
        marks = ",".join("?" * len(chunk))
        if await db.fetchone(
                f"SELECT 1 FROM tracks t JOIN jobs j ON j.id = t.job_id "
                f"WHERE (t.library_path IN ({marks}) OR path_key(t.library_path) IN ({marks})) "
                f"AND j.source_url LIKE '%browse/MPREb_%' LIMIT 1",
                (*chunk, *(identity.path_key(p) for p in chunk))):
            return True
    return False


async def refresh(release_mbid: str | None) -> None:
    """Re-file (or close) the missing-tracks issue of one release. Not while
    a download is going into one of its albums (locks.settling): the issue
    stays as it is, and is counted again when the album is checked after."""
    from .. import locks
    from .units import unit_folder
    if not release_mbid:
        return
    if await locks.settling_into(*(unit_folder(u["key"]) for u in await _units(release_mbid))):
        return
    key = f"missing_tracks:{release_mbid}"
    info = await compute(release_mbid)
    existing = await db.fetchone("SELECT id, state, detail FROM md_issues WHERE key = ?", (key,))
    previous = json.loads(existing["detail"]) if existing and existing["detail"] else {}
    download = previous.get("download")
    # a few songs of a bigger album aren't an album with gaps — unless
    # someone asked for the rest
    if not info or not info["missing"] or (info["partial"] and not download):
        if existing and existing["state"] in ("open", "failed", "queued"):
            await db.execute("UPDATE md_issues SET state = 'applied', updated_at = ? WHERE id = ?",
                             (utcnow(), existing["id"]))
        return
    if download and not await _job_active(download.get("job_id")):
        download = {**download, "active": False}
    await _file(info, download)


def _summary(info: dict, download: dict | None) -> str:
    return (f"{len(info['missing'])} of {info['total']} tracks missing"
            + (" — download queued" if download and download.get("active", True) else ""))


async def _file(info: dict, download: dict | None) -> None:
    summary = _summary(info, download)
    detail = {**info, "download": download}
    await save([planned("missing_tracks", info["release_mbid"], summary, detail,
                        artist_dir=info["artist_dirs"][0] if info["artist_dirs"] else None,
                        album_key=info["units"][0],
                        refs=[(k.split("/", 1)[0], k) for k in info["units"]])],
               scope_kinds=("missing_tracks",), close_others=False)


async def want_download(release_mbid: str) -> None:
    """Remember that the user asked for this release's missing tracks, so a
    later pass asks YouTube Music again (it couldn't be asked this time)."""
    row = await db.fetchone("SELECT id, detail FROM md_issues WHERE key = ?",
                            (f"missing_tracks:{release_mbid}",))
    if row is None:
        return
    detail = json.loads(row["detail"] or "{}")
    download = detail.get("download") or {}
    if download.get("job_id") and download.get("active", True):
        return
    detail["download"] = {**download, "wanted": True, "wanted_at": utcnow()}
    await db.execute("UPDATE md_issues SET detail = ?, error = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(detail), "waiting for YouTube Music's hourly budget",
                      utcnow(), row["id"]))


# Downloads asked for while YouTube Music couldn't be asked, retried a few a
# pass: each costs a few of the hour's lookups (CATALOG_REQUESTS_PER_HOUR,
# 600), and browsing needs the rest. Two passes an hour, 30 albums each.
WANTED_PER_PASS = 30


async def wanted_downloads(limit: int = WANTED_PER_PASS) -> list[int]:
    """Missing-tracks issues whose download was asked for and not yet
    queued, oldest first."""
    rows = await db.fetchall(
        "SELECT id FROM md_issues WHERE kind = 'missing_tracks' AND state IN ('open', 'failed') "
        "AND (json_extract(detail, '$.download.wanted') = 1 "
        "OR error LIKE '%browsing budget%' OR error LIKE '%asked us to slow down%') "
        "ORDER BY updated_at LIMIT ?", (limit,))
    return [r["id"] for r in rows]


async def _job_active(job_id: str | None) -> bool:
    if not job_id:
        return False
    row = await db.fetchone("SELECT status FROM jobs WHERE id = ?", (job_id,))
    return bool(row) and row["status"] in ACTIVE_JOB_STATUSES


# ------------------------------------------------------------------ download
async def _browse_from_history(release_mbid: str) -> str | None:
    """The YouTube Music release we downloaded these files from, if we did
    (the download may have spelt their paths in another case)."""
    paths = [r["path"] for r in await db.fetchall(
        "SELECT path FROM library_index WHERE mb_albumid = ?", (release_mbid,))]
    for i in range(0, len(paths), 400):
        chunk = paths[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for r in await db.fetchall(
                f"SELECT j.source_url FROM tracks t JOIN jobs j ON j.id = t.job_id "
                f"WHERE (t.library_path IN ({marks}) OR path_key(t.library_path) IN ({marks})) "
                f"AND j.source_url LIKE '%browse/MPREb_%' LIMIT 5",
                (*chunk, *(identity.path_key(p) for p in chunk))):
            m = re.search(r"browse/(MPREb_[A-Za-z0-9_-]+)", r["source_url"] or "")
            if m:
                return m.group(1)
    return None


def _match(missing: list[dict], ytm_tracks: list[dict]) -> tuple[list[str], list[dict]]:
    """Video ids on the YouTube Music album for the missing tracks, in their
    order, through the resolver's matcher (identity.assign): the same title
    and version within 5 seconds first; failing that, the same place on the
    album and length ("All Bout U" is "All About U"), or a near-identical
    title and length. The version agrees at every rank, so "Black Friday
    (Live)" in the studio song's place is not the studio song."""
    playable, seen = [], set()
    for t in ytm_tracks:
        if t.get("video_id") and t.get("available") and t["video_id"] not in seen:
            seen.add(t["video_id"])
            playable.append(t)
    wanted = [identity.side(w["title"], w.get("length"), number=w.get("position"),
                            context=w.get("context") or ())
              for w in missing]
    held = [identity.side(t.get("title"), t.get("duration_sec"), number=t.get("track_number"))
            for t in playable]
    picked = {j: playable[i]["video_id"] for j, i in identity.assign(wanted, held)}
    found = [picked[j] for j in range(len(missing)) if j in picked]
    return found, [w for j, w in enumerate(missing) if j not in picked]


async def find_on_youtube(info: dict) -> tuple[str | None, dict | None]:
    """The YouTube Music album holding most of the missing tracks."""
    from .. import catalog
    browse = await _browse_from_history(info["release_mbid"])
    candidates: list[str] = [browse] if browse else []
    if len(candidates) == 0:
        query = f"{info['artist'] or ''} {info['album']}".strip()
        try:
            results = await catalog.call("search", query, filter="albums", limit=8)
        except catalog.CatalogError as exc:
            raise _asking_failed(exc, "YouTube Music search failed") from exc
        wanted = album_identity(info["album"], {info["artist"] or ""})
        artist_key = strict_key(info["artist"] or "")
        scored = []
        for r in results or []:
            bid = r.get("browseId")
            if not bid:
                continue
            names = {strict_key(a.get("name") or "") for a in r.get("artists") or []}
            same_title = bool(album_identity(r.get("title"), {info["artist"] or ""}) & wanted)
            same_artist = not artist_key or artist_key in names or any(
                artist_key in n or n in artist_key for n in names if n)
            same_year = str(r.get("year") or "") == str(info.get("year") or "")
            if same_title and same_artist:
                scored.append((same_year, bid))
        candidates = [bid for _, bid in sorted(scored, reverse=True)][:3]
    best = (None, None, -1)
    for bid in candidates:
        try:
            album = await catalog.album(bid)
        except catalog.CatalogError as exc:
            if exc.status == 503:
                raise Busy(str(exc)) from exc     # not "not found": not asked
            log.info("album %s: %s", bid, exc)
            continue
        found, _ = _match(info["missing"], album.get("tracks") or [])
        if len(found) > best[2]:
            best = (bid, album, len(found))
    return best[0], best[1]


# A track an earlier download of the release delivered, or skipped because
# the library had it, isn't fetched again unless the user asks (Retry): asked
# again, it would be skipped again. Failed and cancelled ones are fetched.
_TRIED = (TrackStatus.COMPLETED, TrackStatus.SKIPPED_DUPLICATE)


async def _attempts(download: dict) -> list[dict]:
    """The downloads queued for this release so far, oldest first. One
    queued before they were listed counts as the first."""
    from ..worker import track_filter
    attempts = list(download.get("attempts") or [])
    legacy = download.get("job_id")
    if legacy and not any(a.get("job_id") == legacy for a in attempts):
        job = await db.fetchone("SELECT id, track_filter FROM jobs WHERE id = ?", (legacy,))
        attempts.insert(0, {"job_id": legacy, "queued_at": download.get("queued_at"),
                            "videos": sorted(track_filter(job) or ()) if job else []})
    return attempts


def _same_track(row, ytm: dict) -> bool:
    title = normalize(ytm.get("title") or "")
    return (bool(title) and normalize(row["title"] or "") == title
            and (not row["track_number"] or not ytm.get("track_number")
                 or row["track_number"] == ytm["track_number"]))


async def _tried(url: str, attempts: list[dict], ytm_tracks: list[dict]) -> dict[str, dict]:
    """Video ids an earlier download delivered or skipped, each with that
    download (as an attempt): of this release's downloads, and of any into
    an album folder from the same YouTube Music album (a second one used to
    replace the first's record). The worker may have swapped a track's id
    for the album-audio version's, so a track is also known by its title and
    number on the album."""
    from ..worker import track_filter
    ids = [a["job_id"] for a in attempts if a.get("job_id")]
    jobs = await db.fetchall(
        "SELECT id, track_filter, created_at FROM jobs WHERE "
        "(source_url = ? AND target_dir IS NOT NULL)"
        + (f" OR id IN ({','.join('?' * len(ids))})" if ids else ""), (url, *ids))
    on_album = {t["video_id"]: t for t in ytm_tracks if t.get("video_id")}
    tried: dict[str, dict] = {}
    asked: set[str] = set()
    for job in jobs:
        wanted = track_filter(job) or set()
        asked |= wanted
        attempt = {"job_id": job["id"], "queued_at": job["created_at"], "videos": sorted(wanted)}
        rows = await db.fetchall("SELECT provider_track_id, title, track_number, status "
                                 "FROM tracks WHERE job_id = ?", (job["id"],))
        done = [r for r in rows if r["status"] in _TRIED]
        if done and len(done) == len(rows) >= len(wanted):
            for video in wanted:     # every track of it came through
                tried.setdefault(video, attempt)
            continue
        for video in wanted - tried.keys():
            ytm = on_album.get(video)
            if any(r["provider_track_id"] == video or (ytm and _same_track(r, ytm))
                   for r in done):
                tried[video] = attempt
    # one removed from the download history since (its tracks with it): how
    # it went isn't known, so what it asked for counts as tried, unless a
    # download still on record asked for it too
    found = {job["id"] for job in jobs}
    for attempt in attempts:
        if attempt.get("job_id") and attempt["job_id"] not in found:
            for video in set(attempt.get("videos") or ()) - asked:
                tried.setdefault(video, attempt)
    return tried


async def _in_queue(url: str, videos: list[str], track_filter: str) -> dict[str, str]:
    """Of these videos, the ones a job from this YouTube Music album that is
    still queued or downloading will fetch, each with that job: a Download
    missing one (into an album folder), or any asking for exactly these."""
    from ..worker import track_filter as filter_of
    active = ",".join("?" * len(ACTIVE_JOB_STATUSES))
    jobs = await db.fetchall(
        f"SELECT id, track_filter FROM jobs WHERE source_url = ? AND status IN ({active}) "
        f"AND (target_dir IS NOT NULL OR track_filter = ?) ORDER BY created_at",
        (url, *ACTIVE_JOB_STATUSES, track_filter))
    held: dict[str, str] = {}
    for job in jobs:
        keep = filter_of(job)
        for video in videos:
            if keep is None or video in keep:
                held.setdefault(video, job["id"])
    return held


async def queue_download(release_mbid: str, skip_videos: set[str] | frozenset = frozenset(),
                         force: bool = False) -> dict:
    """Queue the release's missing tracks as one job, filed into the album's
    own folder. `skip_videos`: tracks already downloaded and just filed in
    (their files aren't matched to the release until its next check).
    Tracks an earlier download delivered or skipped are left out unless
    `force` (Retry); when that is all of them, nothing is queued. One a job
    still in the queue will fetch is never queued again, Retry or not."""
    from ..worker import worker
    from .units import unit_folder
    info = await compute(release_mbid)
    if not info or not info["missing"]:
        return {"queued": 0, "message": "nothing is missing"}
    browse_id, album = await find_on_youtube(info)
    if not browse_id or not album:
        raise RuntimeError(f"couldn't find {info['album']} on YouTube Music")
    video_ids, not_found = _match(info["missing"], album.get("tracks") or [])
    if skip_videos:
        video_ids = [v for v in video_ids if v not in skip_videos]
        if not video_ids:
            return {"queued": 0, "message": "the downloaded tracks were filed in"}
    if not video_ids:
        raise RuntimeError(f"none of the missing tracks are on the YouTube Music release "
                           f"of {album.get('title')}")
    url = f"https://music.youtube.com/browse/{browse_id}"
    key = f"missing_tracks:{release_mbid}"
    row = await db.fetchone("SELECT id, detail FROM md_issues WHERE key = ?", (key,))
    before = (json.loads(row["detail"] or "{}") if row else {}).get("download") or {}
    attempts = await _attempts(before)
    track_filter = json.dumps(sorted(video_ids))
    # a job still queued or downloading fetches its tracks once, Retry or not
    in_queue = await _in_queue(url, video_ids, track_filter)
    fresh = [v for v in video_ids if v not in in_queue]
    already = 0
    if fresh and not force:
        tried = await _tried(url, attempts, album.get("tracks") or [])
        already = sum(v in tried for v in fresh)
        # the downloads that fetched them count as this release's (one into
        # the same folder may have lost its record to a later one)
        for attempt in {tried[v]["job_id"]: tried[v] for v in fresh if v in tried}.values():
            if not any(a.get("job_id") == attempt["job_id"] for a in attempts):
                attempts.append(attempt)
        attempts.sort(key=lambda a: a.get("queued_at") or "")
        fresh = [v for v in fresh if v not in tried]
        if not fresh and not in_queue:
            if row:
                detail = json.loads(row["detail"] or "{}")
                # answered: not asked again on a later pass. Nothing it asks
                # for is in the queue now, though no check may have run since
                # the last download ended (nothing came in to set one off).
                download = {**{k: v for k, v in before.items()
                               if k not in ("wanted", "wanted_at")},
                            "attempts": attempts,
                            "active": await _job_active(before.get("job_id"))}
                detail["download"] = download
                await db.execute(
                    "UPDATE md_issues SET detail = ?, summary = ?, error = ?, updated_at = ? "
                    "WHERE id = ?",
                    (json.dumps(detail), _summary(info, download),
                     f"already tried: {already} track{'s were' if already != 1 else ' was'} "
                     f"downloaded or skipped by an earlier download; Retry from the album "
                     f"to fetch {'them' if already != 1 else 'it'} again", utcnow(), row["id"]))
            return {"queued": 0, "already_tried": already, "message": "already tried",
                    "browse_id": browse_id, "not_found": [t["title"] for t in not_found]}
    dup = None
    if not fresh:
        # all of them are coming: the issue's own download, if it is one
        jobs = list(in_queue.values())
        dup = before.get("job_id") if before.get("job_id") in jobs else jobs[0]
        video_ids = [v for v in video_ids if v in in_queue]
    elif len(fresh) < len(video_ids):
        video_ids = fresh
        track_filter = json.dumps(sorted(video_ids))
    job_id = dup or await worker.enqueue(
        url, title=info["album"], artist=info["artist"], track_filter=track_filter,
        target_dir=unit_folder(info["main_unit"]))
    queued_at = utcnow()
    if not any(a.get("job_id") == job_id for a in attempts):
        attempts.append({"job_id": job_id, "queued_at": queued_at, "videos": sorted(video_ids)})
    download = {"job_id": job_id, "browse_id": browse_id, "queued_at": queued_at,
                "tracks": len(video_ids), "not_found": [t["title"] for t in not_found],
                "active": True, "attempts": attempts}
    if row is None:
        # the rest of an album we only had a few songs of: asking for them
        # makes it one to keep track of
        await _file(info, download)
        row = await db.fetchone("SELECT id, detail FROM md_issues WHERE key = ?", (key,))
    if row:
        detail = json.loads(row["detail"] or "{}")
        detail["download"] = download
        await db.execute(
            "UPDATE md_issues SET detail = ?, summary = ?, state = 'open', error = ?, "
            "updated_at = ? WHERE id = ?",
            (json.dumps(detail), _summary(info, download),
             (f"not on YouTube Music: {', '.join(download['not_found'][:5])}"
              if not_found else None), utcnow(), row["id"]))
    return {"queued": len(video_ids), "job_id": job_id, "browse_id": browse_id,
            "not_found": download["not_found"], "already_queued": bool(dup),
            "already_tried": already}
