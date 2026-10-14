"""Issues: every way the files or Plex disagree with the desired state, and
every judgement the user has to make.

An issue has a stable key (kind + what it is about), so a re-check updates
it in place instead of piling up copies; a problem that goes away is closed
as `applied`; one the user dismissed stays dismissed ("not a problem" is a
decision, like the duplicate exclusions).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import unicodedata
from collections import Counter
from pathlib import Path

from ..config import settings
from ..db import db, utcnow
from ..engine import tagio
from ..library import strict_key
from ..plex import is_matched
from . import musicbrainz as mb
from .plexsync import jlist
from .units import folder_of

log = logging.getLogger("mdl.issues")

KINDS: dict[str, dict] = {
    "not_in_plex": {"label": "Not in Plex", "severity": "warning", "applyable": True,
                    "action": "Scan in Plex",
                    "description": "Files Plex has not picked up. A partial scan of the folder fixes it."},
    "plex_ghost": {"label": "Plex entry without a file", "severity": "info", "applyable": True,
                   "action": "Clean up Plex",
                   "description": "Plex lists tracks whose files are gone."},
    "unmatched": {"label": "Unmatched in Plex", "severity": "warning", "applyable": True,
                  "action": "Match in Plex",
                  "description": "Plex has no match for the album; it is matched to the identified MusicBrainz release."},
    "wrong_match": {"label": "Wrong Plex match", "severity": "warning", "applyable": True,
                    "action": "Rematch in Plex",
                    "description": "Plex matched the album to a different release than the files are."},
    "tags": {"label": "Tags to fix", "severity": "info", "applyable": True,
             "action": "Write tags",
             "description": "File tags differ from the identified release, or carry junk."},
    "track_order": {"label": "Track order", "severity": "warning", "applyable": True,
                    "action": "Fix numbering",
                    "description": "Track or disc numbers differ from the release, in the files or in Plex."},
    "missing_tracks": {"label": "Missing tracks", "severity": "warning", "applyable": True,
                       "action": "Download missing",
                       "description": "The identified release has tracks the library doesn't. "
                                      "Downloading finds the album on YouTube Music and queues just those."},
    "move": {"label": "Rename / move", "severity": "info", "applyable": True,
             "action": "Move files",
             "description": "Files are not at Artist/(Year) Album/NN - Title."},
    "move_conflict": {"label": "Move blocked", "severity": "warning", "applyable": False,
                      "action": None,
                      "description": "The destination already holds other files. Usually a duplicate album."},
    "genres": {"label": "Genres", "severity": "info", "applyable": True,
               "action": "Set genres",
               "description": "Plex's genres differ from the desired genres."},
    "moods": {"label": "Moods", "severity": "info", "applyable": True, "action": "Set moods",
              "description": "Plex has no moods for the album."},
    "poster": {"label": "Missing cover art", "severity": "info", "applyable": True,
               "action": "Add cover", "description": "Plex has no poster for the album."},
    "review_match": {"label": "Check match", "severity": "warning", "applyable": False,
                     "action": None,
                     "description": "A likely release was found but not with enough confidence to act on."},
    "unresolved": {"label": "Not identified", "severity": "warning", "applyable": False,
                   "action": None,
                   "description": "No MusicBrainz release matches these files."},
    "folder_artist": {"label": "Folder ≠ tag artist", "severity": "warning", "applyable": False,
                      "action": None,
                      "description": "The folder and the album artist tag name different artists."},
    "extra_tracks": {"label": "Tracks not on release", "severity": "info", "applyable": False,
                     "action": None,
                     "description": "Some files did not match a track of the identified release."},
    "dup_album": {"label": "Duplicate album", "severity": "warning", "applyable": False,
                  "action": None, "description": "The same album is in the library more than once."},
    "dup_track": {"label": "Duplicate track", "severity": "warning", "applyable": False,
                  "action": None, "description": "One album's track is held twice."},
    "split_artist": {"label": "Split artist", "severity": "warning", "applyable": False,
                     "action": None,
                     "description": "One artist under several folder spellings."},
    "artist_unmatched": {"label": "Artist unmatched in Plex", "severity": "warning",
                         "applyable": True, "action": "Match artist",
                         "description": "Plex has no match for the artist."},
    "artist_genres": {"label": "Artist genres", "severity": "info", "applyable": True,
                      "action": "Set genres",
                      "description": "Plex's artist genres differ from the desired genres."},
    "artist_moods": {"label": "Artist moods", "severity": "info", "applyable": True,
                     "action": "Set moods", "description": "Plex has no moods for the artist."},
    "artist_poster": {"label": "Missing artist image", "severity": "info", "applyable": True,
                      "action": "Add image", "description": "Plex has no image for the artist."},
    "artist_unknown": {"label": "Artist not on MusicBrainz", "severity": "info",
                       "applyable": False, "action": None,
                       "description": "MusicBrainz has no such artist. Adding it lets Plex match it too."},
}
UNIT_KINDS = ("not_in_plex", "unmatched", "wrong_match", "tags", "track_order", "move",
              "move_conflict",
              "genres", "moods", "poster", "review_match", "unresolved", "folder_artist",
              "extra_tracks")
ARTIST_KINDS = ("artist_unmatched", "artist_genres", "artist_moods", "artist_poster",
                "artist_unknown")
DUP_KINDS = ("dup_album", "dup_track", "split_artist")
# The order a unit's fixes are applied in: files first (Plex reads them),
# then Plex catches up, then Plex's own fields.
APPLY_ORDER = ("missing_tracks", "tags", "track_order", "move", "not_in_plex", "plex_ghost",
               "unmatched", "wrong_match",
               "genres", "moods", "poster", "artist_unmatched", "artist_genres",
               "artist_moods", "artist_poster")


def issue_out(row) -> dict:
    d = dict(row)
    meta = KINDS.get(d["kind"], {})
    d["detail"] = json.loads(d["detail"]) if d.get("detail") else {}
    d["auto"] = bool(d.get("auto"))
    d["label"] = meta.get("label", d["kind"])
    d["applyable"] = bool(meta.get("applyable"))
    d["action_label"] = meta.get("action")
    return d


def planned(kind: str, ref: str, summary: str, detail: dict | None = None, *,
            entity: str = "album", auto: bool = False, severity: str | None = None,
            artist_dir: str | None = None, album_key: str | None = None,
            refs: list[tuple[str | None, str | None]] | None = None) -> dict:
    return {"kind": kind, "key": f"{kind}:{ref}", "ref": ref, "entity": entity,
            "summary": summary, "detail": detail or {}, "auto": auto,
            "severity": severity or KINDS[kind]["severity"],
            "artist_dir": artist_dir, "album_key": album_key, "refs": refs or []}


async def save(items: list[dict], *, scope_kinds: tuple[str, ...], album_key: str | None = None,
               artist_dir: str | None = None, all_scope: bool = False,
               close_others: bool = True) -> None:
    """Upsert `items` and close every other open issue of `scope_kinds` in the
    same scope (this album, this artist, or everywhere)."""
    now = utcnow()
    keys = {i["key"] for i in items}
    for item in items:
        existing = await db.fetchone("SELECT id, state, detail FROM md_issues WHERE key = ?",
                                     (item["key"],))
        detail = json.dumps(item["detail"], default=str)
        if existing is None:
            await db.execute(
                "INSERT INTO md_issues (key, kind, entity, ref, artist_dir, album_key, severity, "
                "summary, detail, auto, state, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?, 'open', ?, ?)",
                (item["key"], item["kind"], item["entity"], item["ref"], item["artist_dir"],
                 item["album_key"], item["severity"], item["summary"], detail,
                 int(item["auto"]), now, now))
            row = await db.fetchone("SELECT id FROM md_issues WHERE key = ?", (item["key"],))
            issue_id = row["id"]
        else:
            issue_id = existing["id"]
            # dismissed stays dismissed; queued stays queued (it is about to run)
            state = existing["state"]
            new_state = state if state in ("dismissed", "queued") else "open"
            await db.execute(
                "UPDATE md_issues SET entity = ?, ref = ?, artist_dir = ?, album_key = ?, "
                "severity = ?, summary = ?, detail = ?, auto = ?, state = ?, "
                "error = CASE WHEN ? = 'open' THEN NULL ELSE error END, updated_at = ? "
                "WHERE id = ?",
                (item["entity"], item["ref"], item["artist_dir"], item["album_key"],
                 item["severity"], item["summary"], detail, int(item["auto"]), new_state,
                 new_state, now, issue_id))
        await db.execute("DELETE FROM md_issue_refs WHERE issue_id = ?", (issue_id,))
        refs = item["refs"] or [(item["artist_dir"], item["album_key"])]
        await db.executemany(
            "INSERT INTO md_issue_refs (issue_id, artist_dir, album_key) VALUES (?,?,?)",
            [(issue_id, a, k) for a, k in refs])
    if not close_others:
        return
    # close what is no longer a problem
    marks = ",".join("?" * len(scope_kinds))
    where = f"state IN ('open', 'failed') AND kind IN ({marks})"
    params: list = list(scope_kinds)
    if not all_scope:
        if album_key is not None:
            where += " AND album_key = ?"
            params.append(album_key)
        elif artist_dir is not None:
            where += " AND artist_dir = ?"
            params.append(artist_dir)
    stale = [r["id"] for r in await db.fetchall(
        f"SELECT id, key FROM md_issues WHERE {where}", tuple(params)) if r["key"] not in keys]
    for i in range(0, len(stale), 400):
        chunk = stale[i:i + 400]
        await db.execute(
            f"UPDATE md_issues SET state = 'applied', updated_at = ? "
            f"WHERE id IN ({','.join('?' * len(chunk))})", (now, *chunk))


# ------------------------------------------------------------------ comparing
def _norm_list(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [v for v in values.split("; ")]
    return [str(v).strip().casefold() for v in values if str(v).strip()]


def current_fields(row: dict) -> dict[str, list[str]]:
    """What the index knows a file's tags say, in tagio field names."""
    def one(v):
        return [str(v)] if v not in (None, "") else []
    return {
        "title": one(row.get("title")), "artist": one(row.get("artist")),
        "album": one(row.get("album")), "albumartist": one(row.get("album_artist")),
        "tracknumber": one(row.get("track_number")),
        "discnumber": one(row.get("disc_number") or 1),
        "date": one((row.get("year") or "")[:4]),
        "genre": [g for g in (row.get("genre") or "").split("; ") if g],
        "isrc": one(row.get("isrc")), "label": one(row.get("label")),
        "releasetype": [t for t in (row.get("releasetype") or "").split("; ") if t],
        "compilation": ["1"] if row.get("compilation") else [],
        "mb_albumid": one(row.get("mb_albumid")),
        "mb_releasegroupid": one(row.get("mb_releasegroupid")),
        "mb_albumartistid": one(row.get("mb_albumartistid")),
        "mb_trackid": one(row.get("mb_trackid")),
        "mb_releasetrackid": one(row.get("mb_releasetrackid")),
    }


COMPARED = ("title", "artist", "album", "albumartist", "tracknumber", "discnumber", "date",
            "genre", "isrc", "label", "releasetype", "compilation", "mb_albumid",
            "mb_releasegroupid", "mb_albumartistid", "mb_trackid", "mb_releasetrackid")


def tag_diff(row: dict, wanted: dict[str, list[str]]) -> dict[str, tuple]:
    have = current_fields(row)
    diff = {}
    for field in COMPARED:
        if field not in wanted:
            continue
        want = wanted[field]
        if field == "date":
            want = [w[:4] for w in want]
        if field == "discnumber" and not want:
            continue
        if _norm_list(have.get(field)) != _norm_list(want):
            diff[field] = (have.get(field) or [], want)
    return diff


def junk_of(row: dict) -> list[str]:
    try:
        return json.loads(row.get("junk") or "[]")
    except ValueError:
        return []


def junk_fix_fields(row: dict) -> dict[str, list[str]]:
    """Without an identified release, the only safe tag fix is the junk:
    split joined genres, drop junk genre values (the rest is stripped by the
    writer itself)."""
    genres = [g for g in (row.get("genre") or "").split("; ") if g]
    cleaned = tagio.split_genres(genres)
    return {"genre": cleaned} if cleaned != genres else {}


# ------------------------------------------------------------------ planning
async def plan_unit(key: str, rows: list[dict], desired: dict | None, res, plex: dict | None,
                    *, plex_synced: bool, ours_locked) -> list[dict]:
    artist_dir = rows[0].get("artist_dir") if rows else None
    out: list[dict] = []

    def add(kind, summary, detail=None, *, ref=None, auto=False, severity=None):
        out.append(planned(kind, ref or key, summary, detail, auto=auto, severity=severity,
                           artist_dir=artist_dir, album_key=key))

    plex_tracks = (plex or {}).get("tracks") or {}
    plex_album = (plex or {}).get("album")

    # -- files Plex hasn't got
    if plex_synced:
        missing = [r["path"] for r in rows if r["path"] not in plex_tracks]
        if missing:
            add("not_in_plex", f"{len(missing)} of {len(rows)} files are not in Plex",
                {"paths": missing[:50], "count": len(missing)}, auto=True)

    status = res.status if res else "pending"
    if desired is None:
        if status == "review" and res is not None:
            add("review_match", f"Best match scores {round(res.score * 100)}%",
                {"score": res.score, "candidates": res.candidates[:3]})
        elif status == "unresolved":
            add("unresolved", res.error or "No MusicBrainz release matches these files",
                {"error": res.error if res else None, "can_seed": True})
        if status in ("review", "unresolved"):
            tags = Counter(r.get("album_artist") for r in rows if r.get("album_artist"))
            if tags and artist_dir:
                tag = tags.most_common(1)[0][0]
                if strict_key(tag) != strict_key(artist_dir) and strict_key(tag) not in (
                        "variousartists", "va"):
                    add("folder_artist", f"Folder says {artist_dir}, tags say {tag}",
                        {"folder": artist_dir, "tag": tag})
        # junk can be cleaned without knowing the release
        junky = [r for r in rows if junk_of(r) or junk_fix_fields(r)]
        if junky and settings.metadata_write_files:
            add("tags", f"Junk tags on {len(junky)} files",
                {"files": len(junky), "junk_only": True,
                 "fields": {"junk": len(junky)},
                 "examples": [{"path": r["path"], "field": "junk", "from": junk_of(r), "to": []}
                              for r in junky[:10]]}, auto=True)
        return out

    auto = status == "resolved"
    # -- numbering, in the files and in Plex
    misnumbered, plex_order = [], []
    for row in rows:
        want = desired["tracks"].get(row["path"]) or {}
        if not want.get("matched"):
            continue
        n, disc = want.get("tracknumber"), want.get("discnumber") or 1
        have_n, have_disc = row.get("track_number"), row.get("disc_number") or 1
        if have_n != n or have_disc != disc:
            misnumbered.append({"path": row["path"], "from": f"{have_disc}-{have_n or '?'}",
                                "to": f"{disc}-{n}"})
            continue
        pt = plex_tracks.get(row["path"])
        if pt and pt.get("idx") is not None and (
                pt.get("idx") != n or (pt.get("parent_idx") or 1) != disc):
            plex_order.append({"path": row["path"], "rating_key": pt["rating_key"],
                               "from": f"{pt.get('parent_idx') or 1}-{pt.get('idx')}",
                               "to": f"{disc}-{n}", "index": n, "disc": disc})
    if misnumbered or plex_order:
        parts = []
        unnumbered = sum(1 for m in misnumbered if m["from"].endswith("?"))
        if unnumbered:
            parts.append(f"{unnumbered} files have no track number")
        if len(misnumbered) > unnumbered:
            parts.append(f"{len(misnumbered) - unnumbered} files numbered wrong")
        if plex_order:
            parts.append(f"{len(plex_order)} tracks out of order in Plex")
        add("track_order", "; ".join(parts),
            {"files": misnumbered, "plex": plex_order}, auto=auto)

    # -- file tags (numbering is the track_order issue's)
    if settings.metadata_write_files:
        fields: Counter = Counter()
        examples = []
        files = 0
        for row in rows:
            want = (desired["tracks"].get(row["path"]) or {}).get("tags") or {}
            diff = tag_diff(row, {k: v for k, v in want.items()
                                  if k not in ("tracknumber", "discnumber")})
            junk = junk_of(row)
            if junk:
                diff["junk"] = (junk, [])
            if diff:
                files += 1
                for field, (old, new) in diff.items():
                    fields[field] += 1
                    if len(examples) < 14:
                        examples.append({"path": row["path"], "field": field,
                                         "from": old, "to": new})
        if files:
            names = ", ".join(f for f, _ in fields.most_common(4))
            add("tags", f"{files} of {len(rows)} files: {names}"
                + ("…" if len(fields) > 4 else ""),
                {"files": files, "fields": dict(fields), "examples": examples}, auto=auto)

    # -- layout
    if settings.metadata_move_files and desired.get("folder"):
        moves = [{"from": r["path"], "to": desired["tracks"][r["path"]]["target"]}
                 for r in rows if desired["tracks"].get(r["path"], {}).get("target")
                 and desired["tracks"][r["path"]]["target"] != r["path"]]
        if moves:
            conflict = await move_conflict(key, rows, desired, moves)
            detail = {"from": folder_of(rows[0]), "to": desired["folder"], "files": moves,
                      "conflict": conflict}
            if conflict:
                add("move_conflict", conflict, detail)
            else:
                same_dir = all(m["from"].rsplit("/", 1)[0] == m["to"].rsplit("/", 1)[0]
                               for m in moves)
                add("move", (f"Rename {len(moves)} files" if same_dir
                             else f"Move to {desired['folder']}"), detail, auto=auto)

    # -- Plex
    if plex_album and settings.metadata_write_plex:
        guid = plex_album.get("guid") or ""
        plex_mbid = plex_album.get("mbid")
        rk = plex_album["rating_key"]
        if not is_matched(guid) and plex_mbid != desired["release_mbid"]:
            add("unmatched", f"Plex has no match for {plex_album.get('title')}",
                {"rating_key": rk, "guid": guid, "release_mbid": desired["release_mbid"],
                 "title": desired["album"]}, auto=auto)
        elif is_matched(guid) and plex_mbid and plex_mbid != desired["release_mbid"]:
            other_rg = None
            try:
                other = await mb.release(plex_mbid)
                other_rg = (other or {}).get("release-group", {}).get("id")
            except mb.MusicBrainzError:
                other = None
            if other is not None and other_rg != desired.get("releasegroup_mbid"):
                add("wrong_match",
                    f"Plex matched it to {other.get('title')} ({(other.get('date') or '')[:4]})",
                    {"rating_key": rk, "guid": guid, "plex_mbid": plex_mbid,
                     "plex_title": other.get("title"), "release_mbid": desired["release_mbid"],
                     "title": desired["album"]},
                    auto=auto and (res.score >= 0.9))
        locked = set(jlist(plex_album.get("locked")))
        want_genres = desired.get("genres") or []
        if want_genres and not ("genre" in locked and not await ours_locked(rk, "genre")):
            have = jlist(plex_album.get("genres"))
            track_keys = [t["rating_key"] for t in plex_tracks.values()
                          if t.get("parent_key") == rk]
            stale_tracks = [t["rating_key"] for t in plex_tracks.values()
                            if t.get("parent_key") == rk
                            and _norm_list(jlist(t.get("genres"))) != _norm_list(want_genres)]
            if _norm_list(have) != _norm_list(want_genres) or stale_tracks:
                add("genres", f"Plex: {', '.join(have) or 'none'} → {', '.join(want_genres)}",
                    {"rating_key": rk, "from": have, "to": want_genres,
                     "tracks": track_keys, "stale_tracks": len(stale_tracks)}, auto=True)
        if desired.get("moods") and desired.get("mood_source") != "plex" \
                and not jlist(plex_album.get("moods")):
            add("moods", f"Add moods: {', '.join(desired['moods'])}",
                {"rating_key": rk, "to": desired["moods"]}, auto=True)
        if not plex_album.get("thumb"):
            add("poster", "Plex has no cover for this album",
                {"rating_key": rk, "release_mbid": desired["release_mbid"],
                 "releasegroup_mbid": desired.get("releasegroup_mbid")}, auto=True)

    extras = [p for p, t in desired["tracks"].items() if not t.get("matched")]
    if extras:
        add("extra_tracks", f"{len(extras)} files are not on {desired['album']}",
            {"paths": extras[:50]})
    return out


async def move_conflict(key: str, rows: list[dict], desired: dict, moves: list[dict]) -> str | None:
    """Why the files can't move where they belong, or None.

    Two units of one release (disc folders) may share a destination as long
    as they hold different tracks. Anything else already there is another
    copy — merging would bury a duplicate, so the duplicate tools decide."""
    targets = [m["to"] for m in moves]
    if len(set(t.casefold() for t in targets)) != len(targets):
        return "Two files would get the same name (likely a duplicate track)"
    ours = {r["path"] for r in rows}
    folder = desired["folder"]
    occupants = [dict(r) for r in await db.fetchall(
        "SELECT path, mb_albumid, mb_releasetrackid FROM library_index WHERE dirpath = ?",
        (folder,))]
    occupants = [o for o in occupants if o["path"] not in ours]
    if occupants:
        mine = {t.get("releasetrack_mbid") for t in desired["tracks"].values()
                if t.get("releasetrack_mbid")}
        for o in occupants:
            if o["mb_albumid"] != desired["release_mbid"]:
                return f"{folder} already holds other files"
            if not o["mb_releasetrackid"] or o["mb_releasetrackid"] in mine:
                return f"{folder} already holds a copy of these tracks"
    root = settings.library_dir

    def clash() -> str | None:
        # A folder that doesn't exist holds nothing: one stat per folder, not
        # per file (each one is a round trip to the NAS).
        present = {d for d in {t.rsplit("/", 1)[0] for t in targets} if (root / d).is_dir()}
        for target in targets:
            if target.rsplit("/", 1)[0] not in present:
                continue
            p = root / target
            if p.exists() and target not in ours:
                try:
                    src = next(m["from"] for m in moves if m["to"] == target)
                    if os.path.samefile(root / src, p):
                        continue     # same file, case-only rename
                except (StopIteration, OSError):
                    pass
                return f"{target} already exists"
        return None

    return await asyncio.get_running_loop().run_in_executor(None, clash)


async def plan_artist(artist_dir: str, desired: dict | None, plex_artist: dict | None,
                      thumb_url: str | None, *, ours_locked) -> list[dict]:
    out: list[dict] = []
    if desired is None:
        return out

    def add(kind, summary, detail=None, *, auto=False):
        out.append(planned(kind, artist_dir, summary, detail, entity="artist", auto=auto,
                           artist_dir=artist_dir, album_key=None))

    if desired.get("status") == "unknown":
        add("artist_unknown", f"{desired['name']} is not on MusicBrainz", {"can_seed": True})
    if not plex_artist or not settings.metadata_write_plex:
        return out
    rk = plex_artist["rating_key"]
    guid = plex_artist.get("guid") or ""
    if desired.get("mbid") and not is_matched(guid) and plex_artist.get("mbid") != desired["mbid"]:
        add("artist_unmatched", f"Plex has no match for {plex_artist.get('title')}",
            {"rating_key": rk, "mbid": desired["mbid"], "name": desired["name"]}, auto=True)
    locked = set(jlist(plex_artist.get("locked")))
    want = desired.get("genres") or []
    have = jlist(plex_artist.get("genres"))
    if want and _norm_list(have) != _norm_list(want) and not (
            "genre" in locked and not await ours_locked(rk, "genre")):
        add("artist_genres", f"Plex: {', '.join(have) or 'none'} → {', '.join(want)}",
            {"rating_key": rk, "from": have, "to": want}, auto=True)
    if desired.get("moods") and desired.get("mood_source") != "plex" \
            and not jlist(plex_artist.get("moods")):
        add("artist_moods", f"Add moods: {', '.join(desired['moods'])}",
            {"rating_key": rk, "to": desired["moods"]}, auto=True)
    if not plex_artist.get("thumb") and thumb_url:
        add("artist_poster", "Plex has no image for this artist",
            {"rating_key": rk, "url": thumb_url}, auto=True)
    return out


async def plan_ghosts() -> list[dict]:
    """Plex tracks whose files are gone (not merely unscanned by us)."""
    rows = await db.fetchall(
        "SELECT p.path, p.folder FROM plex_items p LEFT JOIN library_index l ON l.path = p.path "
        "WHERE p.type = 'track' AND p.path IS NOT NULL AND l.path IS NULL")
    candidates = [dict(r) for r in rows][:5000]
    if candidates:
        # our file under another Unicode spelling of its name is no ghost
        ours = {unicodedata.normalize("NFC", r["path"])
                for r in await db.fetchall("SELECT path FROM library_index")}
        candidates = [r for r in candidates if unicodedata.normalize("NFC", r["path"]) not in ours]
    root = settings.library_dir

    def missing() -> list[dict]:
        return [r for r in candidates if not (root / r["path"]).exists()]

    gone = await asyncio.get_running_loop().run_in_executor(None, missing)
    by_folder: dict[str, list[str]] = {}
    for r in gone:
        by_folder.setdefault(r["folder"] or "", []).append(r["path"])
    out = []
    for folder, paths in by_folder.items():
        artist_dir = Path(folder).parts[0] if folder else None
        out.append(planned("plex_ghost", folder or "(library root)",
                           f"{len(paths)} Plex tracks in {folder or 'the library root'} have no file",
                           {"folder": folder, "paths": paths[:50], "count": len(paths)},
                           auto=True, artist_dir=artist_dir, album_key=folder or None))
    return out
