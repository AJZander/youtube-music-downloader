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

from ..db import db, utcnow
from ..engine.matcher import normalize
from ..library import album_identity, strict_key
from ..models import ACTIVE_JOB_STATUSES
from ..ownership import title_keys, version_markers
from . import musicbrainz as mb
from .issues import planned, save

log = logging.getLogger("mdl.missing")

_VIDEO = re.compile(r"dvd|blu-?ray|vhs|video|hd-?dvd|umd", re.I)
TOLERANCE = 5.0


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
    for unit in units:
        desired = json.loads(unit["desired"])
        paths += list(desired["tracks"])
        for track in desired["tracks"].values():
            if track.get("matched") and track.get("releasetrack_mbid"):
                have.add(track["releasetrack_mbid"])
    audio_discs = {m.get("position") or 1 for m in rel.get("media") or []
                   if not _VIDEO.search(m.get("format") or "")}
    tracks = [t for t in mb.release_tracks(rel) if t["disc"] in audio_discs]
    for position, t in enumerate(tracks, start=1):
        t["position"] = position     # running number across discs, as YouTube Music counts
    missing = [{"disc": t["disc"], "number": t["number"], "position": t["position"],
                "title": t["title"],
                "length": t["length"], "recording_mbid": t["recording_id"],
                "releasetrack_mbid": t["track_id"],
                "artist": mb.credit_string(t["credits"]) or None}
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
            "artist_dirs": sorted({u["artist_dir"] for u in units if u["artist_dir"]})}


async def _album_download(paths: list[str]) -> bool:
    """Whether any of these files came from downloading the whole album."""
    for i in range(0, len(paths), 400):
        chunk = paths[i:i + 400]
        if await db.fetchone(
                f"SELECT 1 FROM tracks t JOIN jobs j ON j.id = t.job_id "
                f"WHERE t.library_path IN ({','.join('?' * len(chunk))}) "
                f"AND j.source_url LIKE '%browse/MPREb_%' LIMIT 1", tuple(chunk)):
            return True
    return False


async def refresh(release_mbid: str | None) -> None:
    """Re-file (or close) the missing-tracks issue of one release."""
    if not release_mbid:
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


async def _file(info: dict, download: dict | None) -> None:
    n = len(info["missing"])
    summary = (f"{n} of {info['total']} tracks missing"
               + (" — download queued" if download and download.get("active", True) else ""))
    detail = {**info, "download": download}
    await save([planned("missing_tracks", info["release_mbid"], summary, detail,
                        artist_dir=info["artist_dirs"][0] if info["artist_dirs"] else None,
                        album_key=info["units"][0],
                        refs=[(k.split("/", 1)[0], k) for k in info["units"]])],
               scope_kinds=("missing_tracks",), close_others=False)


async def _job_active(job_id: str | None) -> bool:
    if not job_id:
        return False
    row = await db.fetchone("SELECT status FROM jobs WHERE id = ?", (job_id,))
    return bool(row) and row["status"] in ACTIVE_JOB_STATUSES


# ------------------------------------------------------------------ download
async def _browse_from_history(release_mbid: str) -> str | None:
    """The YouTube Music release we downloaded these files from, if we did."""
    rows = await db.fetchall(
        "SELECT j.source_url FROM library_index l JOIN tracks t ON t.library_path = l.path "
        "JOIN jobs j ON j.id = t.job_id WHERE l.mb_albumid = ? AND j.source_url LIKE "
        "'%browse/MPREb_%' LIMIT 5", (release_mbid,))
    for r in rows:
        m = re.search(r"browse/(MPREb_[A-Za-z0-9_-]+)", r["source_url"] or "")
        if m:
            return m.group(1)
    return None


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def _match(missing: list[dict], ytm_tracks: list[dict]) -> tuple[list[str], list[dict]]:
    """Video ids on the YouTube Music album for the missing tracks.

    Same title (and version) within 5 seconds first; failing that, the same
    place on the album and the same length ("All Bout U" is "All About U"),
    or a near-identical title and the same length."""
    found, not_found, used = [], [], set()
    playable = [t for t in ytm_tracks if t.get("video_id") and t.get("available")]
    for want in missing:
        keys = title_keys(want["title"])
        marks = version_markers(want["title"])
        best = None

        def gap_of(t):
            if t.get("duration_sec") and want.get("length"):
                return abs(t["duration_sec"] - want["length"])
            return None

        for t in playable:
            if t["video_id"] in used:
                continue
            gap = gap_of(t)
            if gap is not None and gap > TOLERANCE:
                continue
            if keys & title_keys(t.get("title")) and version_markers(t.get("title")) == marks:
                rank = 0
            elif gap is not None and want.get("position") and t.get("track_number") == want["position"]:
                rank = 1
            elif gap is not None and _similar(want["title"], t.get("title") or "") >= 0.75:
                rank = 2
            else:
                continue
            score = (rank, gap or 0.0)
            if best is None or score < best[0]:
                best = (score, t)
        if best:
            used.add(best[1]["video_id"])
            found.append(best[1]["video_id"])
        else:
            not_found.append(want)
    return found, not_found


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
            raise RuntimeError(f"YouTube Music search failed: {exc}") from exc
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
            log.info("album %s: %s", bid, exc)
            continue
        found, _ = _match(info["missing"], album.get("tracks") or [])
        if len(found) > best[2]:
            best = (bid, album, len(found))
    return best[0], best[1]


async def queue_download(release_mbid: str) -> dict:
    from ..worker import worker
    info = await compute(release_mbid)
    if not info or not info["missing"]:
        return {"queued": 0, "message": "nothing is missing"}
    browse_id, album = await find_on_youtube(info)
    if not browse_id or not album:
        raise RuntimeError(f"couldn't find {info['album']} on YouTube Music")
    video_ids, not_found = _match(info["missing"], album.get("tracks") or [])
    if not video_ids:
        raise RuntimeError(f"none of the missing tracks are on the YouTube Music release "
                           f"of {album.get('title')}")
    url = f"https://music.youtube.com/browse/{browse_id}"
    track_filter = json.dumps(sorted(video_ids))
    active = ",".join("?" * len(ACTIVE_JOB_STATUSES))
    dup = await db.fetchone(
        f"SELECT id FROM jobs WHERE source_url = ? AND track_filter = ? AND status IN ({active})",
        (url, track_filter, *ACTIVE_JOB_STATUSES))
    job_id = dup["id"] if dup else await worker.enqueue(
        url, title=info["album"], artist=info["artist"], track_filter=track_filter)
    download = {"job_id": job_id, "browse_id": browse_id, "queued_at": utcnow(),
                "tracks": len(video_ids), "not_found": [t["title"] for t in not_found],
                "active": True}
    row = await db.fetchone("SELECT id, detail FROM md_issues WHERE key = ?",
                            (f"missing_tracks:{release_mbid}",))
    if row is None:
        # the rest of an album we only had a few songs of: asking for them
        # makes it one to keep track of
        await _file(info, download)
        row = await db.fetchone("SELECT id, detail FROM md_issues WHERE key = ?",
                                (f"missing_tracks:{release_mbid}",))
    if row:
        detail = json.loads(row["detail"] or "{}")
        detail["download"] = download
        n = len(info["missing"])
        await db.execute(
            "UPDATE md_issues SET detail = ?, summary = ?, state = 'open', error = ?, "
            "updated_at = ? WHERE id = ?",
            (json.dumps(detail), f"{n} of {info['total']} tracks missing — download queued",
             (f"not on YouTube Music: {', '.join(download['not_found'][:5])}"
              if not_found else None), utcnow(), row["id"]))
    return {"queued": len(video_ids), "job_id": job_id, "browse_id": browse_id,
            "not_found": download["not_found"], "already_queued": bool(dup)}
