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
import time
import unicodedata
from collections import Counter
from pathlib import Path

from ..config import settings
from ..db import db, utcnow
from ..engine import tagio
from ..library import strict_key
from ..plex import is_matched
from . import musicbrainz as mb
from .plexsync import album_share, foreign_files, jlist, shares_album
from .units import folder_of, in_box_set, unit_folder

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
    "move_conflict": {"label": "Move blocked", "severity": "warning", "applyable": True,
                      "action": "Merge copies",
                      "description": "The destination already holds another copy of the album. "
                                     "Merging moves in the tracks it lacks; where a track is in "
                                     "both, the better file stays and the other goes to Trash."},
    "album_copies": {"label": "Album held more than once", "severity": "warning",
                     "applyable": True, "action": "Merge copies",
                     "description": "One album (a CD, a vinyl rip, a remaster, a deluxe edition…) in "
                                    "several folders. Merging makes it one album in the most "
                                    "complete edition's folder, keeping the best file of each "
                                    "recording; the other copies go to Trash. A different "
                                    "recording (a new remix, live, a demo) is not a copy and stays. "
                                    "Box sets keep their own copies."},
    "bad_file": {"label": "Damaged file", "severity": "warning", "applyable": True,
                 "action": "Move to Trash",
                 "description": "Files that can't be read (a zeroed or cut-off download). Nothing "
                                "can tag them or show them in Plex. Trashing one lets "
                                "\"Download missing\" fetch a good copy."},
    "plex_shared": {"label": "Plex album shared", "severity": "warning", "applyable": True,
                    "action": "Restore in Plex",
                    "description": "Plex files these tracks in one album with other albums' tracks, "
                                   "and an earlier fix matched or tagged that album as this one. "
                                   "Applying puts back what Plex had."},
    "plex_foreign": {"label": "Plex album mixes folders", "severity": "warning",
                     "applyable": False, "action": None,
                     "description": "Plex shows these tracks in one album with another album "
                                    "folder's tracks (two editions tagged alike, or a track Plex "
                                    "first saw elsewhere), or the album was matched to different "
                                    "releases in turn. Until it holds this album's tracks only, "
                                    "nothing album-level is changed in Plex: no match, genres, "
                                    "moods or cover. Don't delete tracks in Plex to fix it."},
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
              "move_conflict", "plex_shared", "plex_foreign", "bad_file",
              "genres", "moods", "poster", "review_match", "unresolved", "folder_artist",
              "extra_tracks")
ARTIST_KINDS = ("artist_unmatched", "artist_genres", "artist_moods", "artist_poster",
                "artist_unknown")
DUP_KINDS = ("dup_album", "dup_track", "split_artist")
# The order a unit's fixes are applied in: files first (Plex reads them),
# then Plex catches up, then Plex's own fields.
APPLY_ORDER = ("bad_file", "missing_tracks", "tags", "track_order", "move", "move_conflict",
               "album_copies",
               "not_in_plex", "plex_ghost",
               "unmatched", "wrong_match", "plex_shared",
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
            # dismissed stays dismissed; queued stays queued (it is about to
            # run); failed stays failed while the fix planned is the same one,
            # so auto-apply doesn't retry it every pass (Apply still does)
            state = existing["state"]
            if state in ("dismissed", "queued") or (state == "failed"
                                                    and existing["detail"] == detail):
                new_state = state
            else:
                new_state = "open"
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


def same_tags(have, want) -> bool:
    """Whether Plex holds exactly these tags, in whatever order it lists them."""
    return sorted(set(_norm_list(have))) == sorted(set(_norm_list(want)))


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
                    *, plex_synced: bool, ours_locked, no_release: bool = False,
                    merge_keeper: bool = False) -> list[dict]:
    """`no_release`: the user says MusicBrainz has no release for these files,
    so nothing asks them to identify it; the files keep their own tags.
    `merge_keeper`: a merge of copies made this folder the album's lately
    (see merged_into), so its files stay in it."""
    artist_dir = rows[0].get("artist_dir") if rows else None
    out: list[dict] = []
    # A file nothing can open can't be tagged, numbered or shown in Plex: it
    # is its own issue, and the rest of the album is planned without it.
    bad = [r for r in rows if r.get("unreadable")]
    rows = [r for r in rows if not r.get("unreadable")]

    def add(kind, summary, detail=None, *, ref=None, auto=False, severity=None):
        out.append(planned(kind, ref or key, summary, detail, auto=auto, severity=severity,
                           artist_dir=artist_dir, album_key=key))

    plex_tracks = (plex or {}).get("tracks") or {}
    plex_album = (plex or {}).get("album")
    if bad:
        add("bad_file", f"{len(bad)} damaged file{'s' if len(bad) != 1 else ''} can't be read",
            {"paths": [r["path"] for r in bad][:50], "count": len(bad)})

    # -- files Plex hasn't got
    if plex_synced:
        missing = [r["path"] for r in rows if r["path"] not in plex_tracks]
        if missing:
            add("not_in_plex", f"{len(missing)} of {len(rows)} files are not in Plex",
                {"paths": missing[:50], "count": len(missing)}, auto=True)

    status = res.status if res else "pending"
    if desired is None:
        if no_release:
            pass
        elif status == "review" and res is not None:
            # what the triage needs: did every file match, and is it our artist
            from .resolver import _artist_similarity
            artist = (_artist_similarity(rows, res.release, res.track_map)
                      if res.release is not None else 0.0)
            add("review_match", f"Best match scores {round(res.score * 100)}%",
                {"score": res.score, "candidates": res.candidates[:3], "files": len(rows),
                 "matched": len(res.track_map), "artist": artist})
        elif status == "unresolved":
            add("unresolved", res.error or "No MusicBrainz release matches these files",
                {"error": res.error if res else None, "can_seed": True})
        if status in ("review", "unresolved") and not no_release:
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

    # -- layout (a box set's copy of an album stays in the box set)
    if settings.metadata_move_files and desired.get("folder") and rows \
            and not await in_box_set(key, rows):
        beside = await single_beside_album(key, desired)
        if beside:
            retarget(desired, beside)
        moves = planned_moves(rows, desired)
        found = await conflict_of(key, rows, desired, moves) if moves else None
        if found and found["kind"] == "other" and await other_album(desired, found):
            # Another album already has the name (a single named like its
            # album, another edition): this one gets a folder of its own.
            alt = await own_folder(desired, found)
            if alt:
                retarget(desired, alt)
                moves = planned_moves(rows, desired)
                found = await conflict_of(key, rows, desired, moves) if moves else None
        if moves and merge_keeper and desired["folder"].casefold() != folder_of(rows[0]).casefold():
            # Moving out of the folder a merge just filled would undo the
            # merge, which would then be planned again (renames in it are fine).
            moves = []
        if moves:
            conflict = found["message"] if found else None
            detail = {"from": folder_of(rows[0]), "to": desired["folder"], "files": moves,
                      "conflict": conflict, "conflict_kind": found["kind"] if found else None}
            if conflict:
                add("move_conflict", conflict, detail)
            else:
                same_dir = all(m["from"].rsplit("/", 1)[0] == m["to"].rsplit("/", 1)[0]
                               for m in moves)
                add("move", (f"Rename {len(moves)} files" if same_dir
                             else f"Move to {desired['folder']}"), detail, auto=auto)

    # -- Plex
    shared = foreign = None
    if plex_album and settings.metadata_write_plex:
        shared = shares_album(plex, rows, desired.get("releasegroup_mbid"))
        foreign = foreign_files(plex, key)
    if shared or foreign:
        # Plex files these tracks with other albums' (an untitled album it
        # made before their tags were fixed, and never regroups) or with
        # another folder's (two editions tagged alike): the album is not
        # ours to match, tag or give a cover, or every album in it would
        # overwrite the last. Our tracks still get their genres, and what
        # we did to an untitled album before we knew is put back.
        rk = plex_album["rating_key"]
        if foreign:
            detail = await foreign_detail(plex_album, foreign)
            folders = detail["folders"]
            add("plex_foreign", f"Plex shows {plex_album.get('title') or 'an untitled album'} "
                f"mixed with {len(foreign)} tracks of {folders[0]}"
                + (f" and {len(folders) - 1} more folders" if len(folders) > 1 else "")
                + ": no album edits in Plex until it is one album", detail)
        # put back only an album no identified album holds most of: one
        # that does puts the album right itself on its own next check
        if shared and await changed_by_us(rk) and \
                not album_share(plex, rows, desired.get("releasegroup_mbid"))["owner_tagged"]:
            add("plex_shared", f"Plex files these tracks in {plex_album.get('title') or 'an untitled album'}"
                f" with other albums': restore it", {"rating_key": rk, "title": plex_album.get("title")},
                auto=True)
        want_genres = desired.get("genres") or []
        stale = [t["rating_key"] for t in plex_tracks.values()
                 if not same_tags(jlist(t.get("genres")), want_genres)]
        if want_genres and stale:
            add("genres", f"Plex: {len(stale)} tracks → {', '.join(want_genres)}",
                {"rating_key": rk, "album": False, "from": [], "to": want_genres,
                 "tracks": stale, "stale_tracks": len(stale)}, auto=True)
    elif plex_album and settings.metadata_write_plex:
        guid = plex_album.get("guid") or ""
        plex_mbid = plex_album.get("mbid")
        rk = plex_album["rating_key"]
        match = None        # (kind, summary, detail, auto) of a match to plan
        # mbid://<our release>: Plex took our match but has no catalogue
        # entry for it to become plex://, and asking again changes nothing
        if not is_matched(guid) and plex_mbid != desired["release_mbid"] \
                and guid != f"mbid://{desired['release_mbid']}":
            match = ("unmatched", f"Plex has no match for {plex_album.get('title')}",
                     {"rating_key": rk, "guid": guid, "release_mbid": desired["release_mbid"],
                      "title": desired["album"]}, auto)
        elif is_matched(guid) and plex_mbid and plex_mbid != desired["release_mbid"]:
            other_rg = None
            try:
                other = await mb.release(plex_mbid)
                other_rg = (other or {}).get("release-group", {}).get("id")
            except mb.MusicBrainzError:
                other = None
            if other is not None and other_rg != desired.get("releasegroup_mbid"):
                match = ("wrong_match",
                         f"Plex matched it to {other.get('title')} ({(other.get('date') or '')[:4]})",
                         {"rating_key": rk, "guid": guid, "plex_mbid": plex_mbid,
                          "plex_title": other.get("title"), "release_mbid": desired["release_mbid"],
                          "title": desired["album"]},
                         auto and (res.score >= 0.9))
        flips = await match_flipflop(rk) if match else []
        if flips:
            # matched as one release and then another lately: each unit
            # that takes the album for its own undoes the last one's match
            units_ = list(dict.fromkeys(m["album_key"] for m in flips if m["album_key"]))
            add("plex_foreign", f"Plex album {plex_album.get('title') or rk} was matched to "
                f"{len({m['guid'] for m in flips})} different releases within "
                f"{FLIPFLOP_WINDOW // 86400} days, by {' and '.join(units_) or 'us'}: "
                f"not matched again",
                {"rating_key": rk, "title": plex_album.get("title"), "reason": "flip_flop",
                 "units": units_, "folders": list(dict.fromkeys(unit_folder(k) for k in units_)),
                 "guids": list(dict.fromkeys(m["guid"] for m in flips))})
        elif match:
            add(match[0], match[1], match[2], auto=match[3])
        locked = set(jlist(plex_album.get("locked")))
        want_genres = desired.get("genres") or []
        if want_genres and not ("genre" in locked and not await ours_locked(rk, "genre")):
            have = jlist(plex_album.get("genres"))
            track_keys = [t["rating_key"] for t in plex_tracks.values()
                          if t.get("parent_key") == rk]
            stale_tracks = [t["rating_key"] for t in plex_tracks.values()
                            if t.get("parent_key") == rk
                            and not same_tags(jlist(t.get("genres")), want_genres)]
            if not same_tags(have, want_genres) or stale_tracks:
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

    damaged = {r["path"] for r in bad}
    extras = [p for p, t in desired["tracks"].items()
              if not t.get("matched") and p not in damaged]
    if extras:
        add("extra_tracks", f"{len(extras)} files are not on {desired['album']}",
            {"paths": extras[:50]})
    return out


async def foreign_detail(plex_album: dict, foreign: list[dict]) -> dict:
    """plex_foreign's detail for an album holding other units' files: the
    folders, and the units (album keys) they are, where the registry says."""
    folders = list(dict.fromkeys(f["folder"] for f in foreign))
    known: dict[str, list[str]] = {}
    for i in range(0, len(folders), 400):
        chunk = folders[i:i + 400]
        for r in await db.fetchall(
                f"SELECT key, dirpath FROM md_albums WHERE dirpath IN ({','.join('?' * len(chunk))})",
                tuple(chunk)):
            known.setdefault(r["dirpath"], []).append(r["key"])
    units_: list[str] = []
    for f in foreign:
        # a folder split by album tag: the unit the file's album tag puts it in
        keys = known.get(f["folder"]) or []
        split = f"{f['folder']}|{f['split']}"
        units_.append(split if split in keys else keys[0] if len(keys) == 1 else f["folder"])
    return {"rating_key": plex_album["rating_key"], "title": plex_album.get("title"),
            "reason": "shared", "folders": folders, "units": list(dict.fromkeys(units_)),
            "tracks": len(foreign)}


# Matches of one Plex item to different releases within this long mean two
# units take it for theirs, each undoing the other's match.
FLIPFLOP_WINDOW = 14 * 86400


async def match_flipflop(rating_key: str) -> list[dict]:
    """The matches journalled on this Plex item within the window
    ([{"guid", "album_key", "at"}]) when they name two or more releases;
    otherwise none."""
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - FLIPFLOP_WINDOW))
    matches = []
    for r in await db.fetchall(
            "SELECT album_key, new_value, applied_at FROM md_changes WHERE target = 'plex' "
            "AND field = 'match' AND ref = ? AND reverted_at IS NULL AND applied_at >= ? "
            "ORDER BY id", (rating_key, since)):
        try:
            guid = (json.loads(r["new_value"]) or {}).get("guid")
        except (TypeError, ValueError, AttributeError):
            continue
        if guid:
            matches.append({"guid": guid, "album_key": r["album_key"], "at": r["applied_at"]})
    return matches if len({m["guid"] for m in matches}) > 1 else []


SHARED_FIELDS = ("match", "genre", "mood")


async def changed_by_us(rating_key: str) -> bool:
    """Whether we matched or tagged this Plex album and have not put it back."""
    marks = ",".join("?" * len(SHARED_FIELDS))
    return await db.fetchone(
        "SELECT 1 FROM md_changes WHERE target = 'plex' AND ref = ? AND reverted_at IS NULL "
        f"AND field IN ({marks}) LIMIT 1", (rating_key, *SHARED_FIELDS)) is not None


def same_file(a: Path, b: Path) -> bool:
    """Whether two paths name one file. The library share ignores case but
    hands out a different inode number for each spelling of a name, so
    samefile alone misses a case-only rename."""
    try:
        if os.path.samefile(a, b):
            return True
        if str(a).casefold() != str(b).casefold():
            return False
        # a case-sensitive filesystem lists both spellings; this share, one
        if a.name != b.name:
            names = os.listdir(b.parent)
            if a.name in names and b.name in names:
                return False
    except OSError:
        return False
    return a.parent == b.parent or same_file(a.parent, b.parent)


def planned_moves(rows: list[dict], desired: dict) -> list[dict]:
    """The files of `rows` that aren't where `desired` puts them."""
    out = []
    for r in rows:
        target = (desired["tracks"].get(r["path"]) or {}).get("target")
        if target and target != r["path"]:
            out.append({"from": r["path"], "to": target})
    return out


def retarget(desired: dict, folder: str) -> None:
    """Put the album in `folder` instead, each file keeping its name."""
    desired["folder"] = folder
    for t in desired["tracks"].values():
        if t.get("target"):
            t["target"] = f"{folder}/{t['target'].rsplit('/', 1)[-1]}"


async def move_conflict(key: str, rows: list[dict], desired: dict, moves: list[dict]) -> str | None:
    """Why the files can't move where they belong, or None."""
    found = await conflict_of(key, rows, desired, moves)
    return found["message"] if found else None


async def conflict_of(key: str, rows: list[dict], desired: dict, moves: list[dict]) -> dict | None:
    """Why the files can't move where they belong: {"message", "kind",
    "occupants"}, or None. Kinds: `names` (two of these files would get one
    name), `copy` (the destination holds this release's tracks), `other`
    (it holds another release), `exists` (a file is in the way).

    Two units of one release (disc folders) may share a destination as long
    as they hold different tracks. Anything else already there is another
    copy — merging would bury a duplicate, so the merge decides, track by
    track, and only when asked."""
    targets = [m["to"] for m in moves]
    ours = {r["path"] for r in rows}
    # one of ours staying where it is holds a name another of ours would take
    staying = {p.casefold() for p in ours - {m["from"] for m in moves}}
    if len(set(t.casefold() for t in targets)) != len(targets) or \
            any(t.casefold() in staying for t in targets):
        return {"message": "Two files would get the same name (likely a duplicate track)",
                "kind": "names", "occupants": []}
    folder = desired["folder"]
    occupants = [dict(r) for r in await db.fetchall(
        "SELECT path, mb_albumid, mb_releasegroupid, mb_releasetrackid FROM library_index "
        "WHERE dirpath = ?", (folder,))]
    occupants = [o for o in occupants if o["path"] not in ours]
    if occupants:
        mine = {t.get("releasetrack_mbid") for t in desired["tracks"].values()
                if t.get("releasetrack_mbid")}
        for o in occupants:
            if o["mb_albumid"] != desired["release_mbid"]:
                return {"message": f"{folder} already holds other files", "kind": "other",
                        "occupants": occupants}
            if not o["mb_releasetrackid"] or o["mb_releasetrackid"] in mine:
                return {"message": f"{folder} already holds a copy of these tracks",
                        "kind": "copy", "occupants": occupants}
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
                src = next((m["from"] for m in moves if m["to"] == target), None)
                if src and same_file(root / src, p):
                    continue     # same file, case-only rename
                return f"{target} already exists"
        return None

    message = await asyncio.get_running_loop().run_in_executor(None, clash)
    return {"message": message, "kind": "exists", "occupants": []} if message else None


def _most(values):
    counted = Counter(v for v in values if v)
    return counted.most_common(1)[0][0] if counted else None


async def _occupying_release(folder: str, occupants: list[dict]) -> tuple[str | None, str | None]:
    """(release, release group) of the files already in `folder`: their tags,
    else what the folder's own album was identified as."""
    rel = _most(o.get("mb_albumid") for o in occupants)
    group = _most(o.get("mb_releasegroupid") for o in occupants)
    if not rel:
        row = await db.fetchone("SELECT release_mbid, releasegroup_mbid FROM md_albums "
                                "WHERE key = ? AND status = 'resolved'", (folder,))
        if row:
            rel, group = row["release_mbid"], row["releasegroup_mbid"]
    return rel, group


def release_edition(rel: dict | None) -> frozenset[str]:
    """The edition words of a MusicBrainz release: its title's qualifiers
    and its disambiguation ("1989 (Deluxe)", "deluxe edition")."""
    from ..library import edition_words
    if not rel:
        return frozenset()
    dis = rel.get("disambiguation") or ""
    return edition_words(rel.get("title"), f"({dis})" if dis else None)


def _kind(rel: dict | None) -> str:
    """A release's primary type, lowercased ("album", "single", "ep")."""
    return (((rel or {}).get("release-group") or {}).get("primary-type") or "album").lower()


async def _theirs(desired: dict, found: dict) -> tuple[dict | None, str | None]:
    """The MusicBrainz release the destination's files are, and its group."""
    rel, group = await _occupying_release(desired["folder"], found["occupants"])
    theirs = None
    if rel and rel != desired["release_mbid"]:
        try:
            theirs = await mb.release(rel)
        except mb.MusicBrainzError:
            theirs = None
    return theirs, group


async def other_album(desired: dict, found: dict) -> bool:
    """Whether the files at the destination are another album (a single
    named like its album, a deluxe edition beside the standard one) rather
    than another copy or pressing of this one. Two albums of one name and
    edition are copies, even under two release groups (MusicBrainz keeps
    some albums twice). Unknown files count as a copy: the merge leaves
    alone whatever it can't pair."""
    rel, group = await _occupying_release(desired["folder"], found["occupants"])
    if not rel and not group:
        return False
    if not rel or rel == desired["release_mbid"]:
        return bool(group and desired.get("releasegroup_mbid")
                    and group != desired["releasegroup_mbid"] and not _album_kind(desired))
    try:
        theirs, mine = await mb.release(rel), await mb.release(desired["release_mbid"])
    except mb.MusicBrainzError:
        return False
    if _kind(theirs) != _kind(mine):
        return True
    return release_edition(theirs) != release_edition(mine)


def _album_kind(desired: dict) -> bool:
    types = desired.get("releasetype") or []
    return not types or types[0] == "album"


async def _labelled(desired: dict, label: str) -> str | None:
    """desired's folder with `label` after the name, unless another release's
    files are already in it."""
    from ..paths import PathBuildError, sanitize_component
    from .desired import on_disk
    artist, _, album = desired["folder"].partition("/")
    try:
        folder = await on_disk(f"{artist}/{sanitize_component(f'{album} {label}')}")
    except PathBuildError:
        return None
    taken = await db.fetchone(
        "SELECT 1 FROM library_index WHERE dirpath = ? AND COALESCE(mb_albumid, '') != ? LIMIT 1",
        (folder, desired["release_mbid"]))
    return None if taken else folder


async def own_folder(desired: dict, found: dict) -> str | None:
    """A folder for an album whose name another album already has, or None
    (it stays a conflict). A single or EP takes its type ("[Single]"), an
    album beside a single waits for the single to step aside (which it is
    told to), and another edition takes its edition ("(Deluxe)")."""
    try:
        mine = await mb.release(desired["release_mbid"])
    except mb.MusicBrainzError:
        return None
    if not mine:
        return None
    theirs, _ = await _theirs(desired, found)
    if _kind(mine) != "album":
        return await _labelled(desired, f"[{_kind(mine).title() if _kind(mine) != 'ep' else 'EP'}]")
    if theirs is not None and _kind(theirs) != "album":
        # the single there gives the album its name back on its next check
        await db.execute("UPDATE md_albums SET next_check = 0 WHERE dirpath = ?",
                         (desired["folder"],))
        return None
    words = sorted(release_edition(mine))
    if words and words != sorted(release_edition(theirs)):
        return await _labelled(desired, "(" + " ".join(w.title() for w in words) + ")")
    return None


async def single_beside_album(key: str, desired: dict) -> str | None:
    """For a single (or EP) whose folder name an album of the same name
    wants, the folder it takes instead ("[Single]"): the album keeps the
    plain name, whichever arrived first."""
    if _album_kind(desired) or not desired.get("folder"):
        return None
    albums = await db.fetchall(
        "SELECT releasegroup_mbid, json_extract(desired, '$.releasetype[0]') AS kind "
        "FROM md_albums WHERE key != ? AND status = 'resolved' AND artist_dir = ? "
        "AND json_extract(desired, '$.folder') = ? COLLATE NOCASE",
        (key, desired["folder"].split("/", 1)[0], desired["folder"]))
    if not any((r["kind"] or "album") == "album"
               and r["releasegroup_mbid"] != desired.get("releasegroup_mbid") for r in albums):
        return None
    kind = desired["releasetype"][0]
    return await _labelled(desired, f"[{'EP' if kind == 'ep' else kind.title()}]")


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
    if desired.get("mbid") and not is_matched(guid) and plex_artist.get("mbid") != desired["mbid"] \
            and guid != f"mbid://{desired['mbid']}":
        add("artist_unmatched", f"Plex has no match for {plex_artist.get('title')}",
            {"rating_key": rk, "mbid": desired["mbid"], "name": desired["name"]}, auto=True)
    locked = set(jlist(plex_artist.get("locked")))
    want = desired.get("genres") or []
    have = jlist(plex_artist.get("genres"))
    if want and not same_tags(have, want) and not (
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


def plain_recordings(rel: dict | None) -> set[str]:
    """The recordings of a release that MusicBrainz knows in one version
    only. One it tells apart from others ("original mono studio mix", "2017
    stereo mix", "live") may be any of them in our files: when the release
    was picked among pressings the files can't tell apart (mono or stereo,
    the same tracklist), the recording is a guess, and no guess is a reason
    to call two files one."""
    out = set()
    for medium in (rel or {}).get("media") or []:
        for t in medium.get("tracks") or []:
            rec = t.get("recording") or {}
            if rec.get("id") and not (rec.get("disambiguation") or "").strip():
                out.add(rec["id"])
    return out


def matched_recordings(desired: dict) -> dict[str, list[str]]:
    """recording MBID -> the files of an identified album holding it."""
    out: dict[str, list[str]] = {}
    for path, t in (desired.get("tracks") or {}).items():
        if t.get("matched") and t.get("recording_mbid"):
            out.setdefault(t["recording_mbid"], []).append(path)
    return out


# How long a file's moves are remembered against moving it back and forth,
# and a merged album's folder is kept as it is.
OSCILLATION_WINDOW = 14 * 86400
# md_state key, + the folder casefolded: {"at", "release", "batch"} of the
# last merge of copies into it
MERGE_KEEPER = "merge_keeper:"


async def merged_into(folder: str) -> bool:
    """Whether a merge of copies made `folder` an album's within the window."""
    row = await db.fetchone("SELECT value FROM md_state WHERE key = ?",
                            (MERGE_KEEPER + folder.casefold(),))
    try:
        at = float(json.loads(row["value"])["at"]) if row and row["value"] else 0.0
    except (ValueError, KeyError, TypeError):
        return False
    return time.time() - at < OSCILLATION_WINDOW


async def plan_album_copies() -> list[dict]:
    """Albums held in more than one folder: units identified as releases of
    one release group that share a recording (a CD, a vinyl rip and a
    remaster of one album; a standard and a deluxe edition). Each set is
    one issue, kept by the copy whose release holds most of what the set
    has. A new remix shares no recording with the original mix, so it is
    another album; a box set keeps its own copies."""
    from collections import defaultdict
    from . import units as units_mod
    rows = await db.fetchall(
        "SELECT key, artist_dir, release_mbid, releasegroup_mbid, desired FROM md_albums "
        "WHERE status = 'resolved' AND ignored = 0 AND no_release = 0 "
        "AND releasegroup_mbid IS NOT NULL AND desired IS NOT NULL")
    by_group: dict[str, list] = defaultdict(list)
    for r in rows:
        by_group[r["releasegroup_mbid"]].append(r)
    out = []
    for group, members in by_group.items():
        if len(members) < 2:
            continue
        held = []
        for m in members:
            want = json.loads(m["desired"])
            try:
                plain = plain_recordings(await mb.release(m["release_mbid"]))
            except mb.MusicBrainzError:
                continue
            recs = set(matched_recordings(want)) & plain
            if not recs:
                continue
            unit_rows = await units_mod.load_unit(m["key"])
            if not unit_rows or await in_box_set(m["key"], unit_rows):
                continue
            held.append({"key": m["key"], "artist_dir": m["artist_dir"],
                         "release": m["release_mbid"], "recs": set(recs), "want": want,
                         "rows": unit_rows})
        for copies in _sharing(held):
            keep = await _keeper(copies)
            if keep is None:
                continue
            others = [c for c in copies if c is not keep]
            album = keep["want"].get("album") or keep["key"]
            shared = sum(len(c["recs"] & keep["recs"]) for c in others)
            out.append(planned(
                "album_copies", f"{group}:{min(c['key'] for c in copies)}",
                f"{album}: {len(copies)} copies — merge into {keep['want'].get('folder') or keep['key']}",
                {"keep": keep["key"], "copies": [c["key"] for c in others],
                 "folders": [c["key"] for c in copies], "album": album,
                 "release_mbid": keep["release"], "releasegroup_mbid": group,
                 "shared": shared, "to": keep["want"].get("folder")},
                artist_dir=keep["artist_dir"], album_key=keep["key"],
                refs=[(c["artist_dir"], c["key"]) for c in copies]))
    return out


def _sharing(units: list[dict]) -> list[list[dict]]:
    """Sets of units joined by a shared recording (transitively)."""
    parent = list(range(len(units)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    owner: dict[str, int] = {}
    for i, u in enumerate(units):
        for rec in u["recs"]:
            if rec in owner:
                parent[find(i)] = find(owner[rec])
            else:
                owner[rec] = i
    sets: dict[int, list[dict]] = {}
    for i, u in enumerate(units):
        sets.setdefault(find(i), []).append(u)
    return [s for s in sets.values() if len(s) > 1]


async def _keeper(copies: list[dict]) -> dict | None:
    """The copy the others merge into: the one whose release holds most of
    the recordings the set has (the most complete edition), then the one
    holding most files, then one already in its own folder."""
    union = set().union(*(c["recs"] for c in copies))
    best, best_rank = None, None
    for c in copies:
        try:
            rel = await mb.release(c["release"])
        except mb.MusicBrainzError:
            return None
        if not rel:
            return None
        on_release = {t["recording_id"] for t in mb.release_tracks(rel) if t.get("recording_id")}
        in_place = (c["want"].get("folder") or "") == unit_folder(c["key"])
        rank = (len(union & on_release), len(c["recs"]), in_place, c["key"])
        if best_rank is None or rank > best_rank:
            best, best_rank = c, rank
    return best


async def plan_ghosts() -> list[dict]:
    """Plex tracks whose files are gone (not merely unscanned by us)."""
    rows = await db.fetchall(
        "SELECT p.path, p.folder FROM plex_items p LEFT JOIN library_index l ON l.path = p.path "
        "WHERE p.type = 'track' AND p.path IS NOT NULL AND l.path IS NULL")
    candidates = [dict(r) for r in rows][:50000]
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
