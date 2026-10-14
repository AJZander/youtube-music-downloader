"""Read models for the unified Library page: the artist list, an artist, an
album in three columns (files | desired | Plex), the attention sidebar and
the change history."""
from __future__ import annotations

import json
import time
from collections import Counter
from urllib.parse import quote

from .. import library
from ..db import db
from ..library import AlbumInstance, LOSSLESS_FORMATS, strict_key
from ..plex import is_matched, plex
from . import issues as issues_mod
from . import missing as missing_tracks
from . import units
from .plexsync import album_for_paths, jlist, last_sync
from .runner import runner

OPEN_STATES = ("open", "failed", "queued")
_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


def _iso(ts: float | None) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else None


# ------------------------------------------------------------------ overview
async def overview() -> dict:
    base = await library.overview()
    one = lambda sql, params=(): db.fetchone(sql, params)  # noqa: E731
    synced = await last_sync()
    md = await one("SELECT COUNT(*) AS total, SUM(checked_at IS NOT NULL) AS checked, "
                   "SUM(status = 'resolved') AS resolved FROM md_albums")
    px = {r["type"]: dict(r) for r in await db.fetchall(
        "SELECT type, COUNT(*) AS n, SUM(guid NOT LIKE 'plex://%') AS unmatched, "
        "SUM(genres IS NOT NULL) AS with_genre, SUM(moods IS NOT NULL) AS with_mood "
        "FROM plex_items GROUP BY type")}
    files_mb = await one("SELECT COUNT(*) AS n FROM library_index WHERE mb_albumid IS NOT NULL")
    not_in_plex = None
    if synced:
        row = await one("SELECT COUNT(*) AS n FROM library_index l WHERE NOT EXISTS "
                        "(SELECT 1 FROM plex_items p WHERE p.type = 'track' AND p.path = l.path)")
        not_in_plex = row["n"]
    coverage = {
        "albums_total": md["total"], "albums_checked": md["checked"] or 0,
        "albums_resolved": md["resolved"] or 0,
        "plex_artists": (px.get("artist") or {}).get("n"),
        "plex_unmatched_artists": (px.get("artist") or {}).get("unmatched"),
        "plex_albums": (px.get("album") or {}).get("n"),
        "plex_unmatched_albums": (px.get("album") or {}).get("unmatched"),
        "plex_tracks": (px.get("track") or {}).get("n"),
        "files_not_in_plex": not_in_plex,
        "files_with_mbids": files_mb["n"],
        "albums_with_genre": (px.get("album") or {}).get("with_genre"),
        "albums_with_mood": (px.get("album") or {}).get("with_mood"),
    } if synced or md["total"] else {k: None for k in (
        "albums_total", "albums_checked", "albums_resolved", "plex_artists",
        "plex_unmatched_artists", "plex_albums", "plex_unmatched_albums", "plex_tracks",
        "files_not_in_plex", "files_with_mbids", "albums_with_genre", "albums_with_mood")}
    rows = await db.fetchall("SELECT kind, COUNT(*) AS n FROM md_issues "
                             "WHERE state IN ('open', 'failed', 'queued') GROUP BY kind")
    by_kind = {r["kind"]: r["n"] for r in rows}
    prefs_row = await one("SELECT value FROM md_state WHERE key = 'plex_prefs'")
    prefs = json.loads(prefs_row["value"]) if prefs_row and prefs_row["value"] else {}
    return {
        **base, "coverage": coverage,
        "issues": {"open": sum(by_kind.values()), "by_kind": by_kind},
        "metadata": await runner.status(),
        "plex": {"reachable": await plex.reachable(), "last_sync": _iso(synced),
                 "settings": {k: prefs.get(k) for k in ("respectTags", "genres", "albumPosters")}},
    }


# ------------------------------------------------------------------ issues
async def _issue_counts_by_artist() -> dict[str, Counter]:
    rows = await db.fetchall(
        "SELECT r.artist_dir, i.kind, COUNT(DISTINCT i.id) AS n FROM md_issues i "
        "JOIN md_issue_refs r ON r.issue_id = i.id WHERE i.state IN ('open', 'failed', 'queued') "
        "AND r.artist_dir IS NOT NULL GROUP BY r.artist_dir, i.kind")
    out: dict[str, Counter] = {}
    for r in rows:
        out.setdefault(r["artist_dir"], Counter())[r["kind"]] += r["n"]
    return out


async def issues_for(*, artist_dir: str | None = None, album_keys: list[str] | None = None,
                     kinds: list[str] | None = None, states=OPEN_STATES, entity: str | None = None,
                     limit: int = 500, offset: int = 0) -> tuple[int, list[dict]]:
    where = [f"i.state IN ({','.join('?' * len(states))})"]
    params: list = list(states)
    joins = ""
    if artist_dir is not None or album_keys:
        joins = "JOIN md_issue_refs r ON r.issue_id = i.id"
        if artist_dir is not None:
            where.append("r.artist_dir = ?")
            params.append(artist_dir)
        if album_keys:
            where.append(f"r.album_key IN ({','.join('?' * len(album_keys))})")
            params.extend(album_keys)
    if kinds:
        where.append(f"i.kind IN ({','.join('?' * len(kinds))})")
        params.extend(kinds)
    if entity:
        where.append("i.entity = ?")
        params.append(entity)
    sql_where = " AND ".join(where)
    total = await db.fetchone(
        f"SELECT COUNT(DISTINCT i.id) AS n FROM md_issues i {joins} WHERE {sql_where}",
        tuple(params))
    rows = await db.fetchall(
        f"SELECT DISTINCT i.* FROM md_issues i {joins} WHERE {sql_where} "
        "ORDER BY CASE i.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, "
        "i.updated_at DESC LIMIT ? OFFSET ?", (*params, limit, offset))
    return total["n"], [issues_mod.issue_out(r) for r in rows]


async def attention(artist_dir: str | None = None) -> dict:
    joins, where, params = "", "i.state IN ('open', 'failed', 'queued')", []
    if artist_dir is not None:
        joins = "JOIN md_issue_refs r ON r.issue_id = i.id"
        where += " AND r.artist_dir = ?"
        params.append(artist_dir)
    rows = await db.fetchall(
        f"SELECT i.kind, COUNT(DISTINCT i.id) AS n, "
        f"COUNT(DISTINCT CASE WHEN i.auto = 1 AND i.state = 'open' THEN i.id END) AS auto_n, "
        f"COUNT(DISTINCT CASE WHEN i.state = 'failed' THEN i.id END) AS failed_n, "
        f"COUNT(DISTINCT CASE WHEN i.state = 'queued' THEN i.id END) AS queued_n "
        f"FROM md_issues i {joins} WHERE {where} GROUP BY i.kind", tuple(params))
    groups = []
    for r in rows:
        meta = issues_mod.KINDS.get(r["kind"], {})
        groups.append({"kind": r["kind"], "label": meta.get("label", r["kind"]),
                       "severity": meta.get("severity", "info"), "count": r["n"],
                       "auto_count": r["auto_n"], "failed_count": r["failed_n"],
                       "queued_count": r["queued_n"], "applyable": bool(meta.get("applyable")),
                       "action_label": meta.get("action"),
                       "description": meta.get("description")})
    groups.sort(key=lambda g: (_SEVERITY_RANK.get(g["severity"], 3), -g["count"]))
    return {"scope": artist_dir or "library", "total": sum(g["count"] for g in groups),
            "groups": groups}


# ------------------------------------------------------------------ artists
async def artists(q: str | None = None, sort: str = "name", filter_: str = "all",
                  limit: int = 5000, offset: int = 0) -> dict:
    rows = [dict(r) for r in await db.fetchall(
        "SELECT artist_dir, COUNT(*) AS tracks, COUNT(DISTINCT dirpath) AS albums, "
        "COALESCE(SUM(filesize_bytes), 0) AS bytes FROM library_index "
        "WHERE artist_dir IS NOT NULL AND artist_dir != '' GROUP BY artist_dir")]
    # disc subfolders are part of their album, not albums of their own
    folders: dict[str, set[str]] = {}
    for r in await db.fetchall("SELECT DISTINCT artist_dir, dirpath FROM library_index "
                               "WHERE artist_dir IS NOT NULL AND artist_dir != ''"):
        folders.setdefault(r["artist_dir"], set()).add(units.unit_dir(r["dirpath"] or ""))
    for r in rows:
        r["albums"] = len(folders.get(r["artist_dir"], ())) or r["albums"]
    meta = {r["artist_dir"]: dict(r) for r in await db.fetchall(
        "SELECT m.artist_dir, m.name, m.status, m.plex_key, p.guid, p.thumb "
        "FROM md_artists m LEFT JOIN plex_items p ON p.rating_key = m.plex_key")}
    counts = await _issue_counts_by_artist()
    items = []
    for r in rows:
        m = meta.get(r["artist_dir"]) or {}
        c = counts.get(r["artist_dir"], Counter())
        items.append({
            "artist_dir": r["artist_dir"], "name": m.get("name") or r["artist_dir"],
            "tracks": r["tracks"], "albums": r["albums"], "bytes": r["bytes"],
            "issues": sum(c.values()),
            "dupes": sum(c[k] for k in issues_mod.DUP_KINDS),
            "unmatched": bool(m.get("plex_key")) and not is_matched(m.get("guid")),
            "unknown": m.get("status") == "unknown",
            "plex_key": m.get("plex_key"), "has_art": bool(m.get("thumb")),
        })
    if q:
        needle = strict_key(q)
        items = [i for i in items if needle in strict_key(i["artist_dir"])
                 or needle in strict_key(i["name"])]
    if filter_ == "issues":
        items = [i for i in items if i["issues"]]
    elif filter_ == "dupes":
        items = [i for i in items if i["dupes"]]
    elif filter_ == "unmatched":
        items = [i for i in items if i["unmatched"]]
    elif filter_ == "unknown":
        items = [i for i in items if i["unknown"]]
    keys = {"name": lambda i: strict_key(i["name"]) or strict_key(i["artist_dir"]),
            "tracks": lambda i: -i["tracks"], "albums": lambda i: -i["albums"],
            "size": lambda i: -i["bytes"], "issues": lambda i: (-i["issues"], strict_key(i["name"]))}
    items.sort(key=keys.get(sort, keys["name"]))
    return {"total": len(items), "items": items[offset:offset + limit]}


def _art_url(**params) -> str:
    return "/api/v1/library/art?" + "&".join(f"{k}={quote(str(v), safe='')}"
                                             for k, v in params.items())


async def _album_records(keys: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(keys), 400):
        chunk = keys[i:i + 400]
        for r in await db.fetchall(
                f"SELECT m.*, p.guid AS plex_guid FROM md_albums m LEFT JOIN plex_items p "
                f"ON p.rating_key = m.plex_key WHERE m.key IN ({','.join('?' * len(chunk))})",
                tuple(chunk)):
            out[r["key"]] = dict(r)
    return out


def _flags(issue_rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for i in sorted(issue_rows, key=lambda i: _SEVERITY_RANK.get(i["severity"], 3)):
        if i["kind"] in seen:
            continue
        seen.add(i["kind"])
        out.append({"kind": i["kind"], "severity": i["severity"], "label": i["label"]})
    return out


async def _album_summary(key: str, rows: list[dict], record: dict | None,
                         issue_rows: list[dict]) -> dict:
    folder = units.unit_folder(key)
    summary = AlbumInstance(folder, rows[0].get("artist_dir") or "", files=rows).summary()
    want = json.loads(record["desired"]) if record and record.get("desired") else None
    target = None
    if want and want.get("folder") and want["folder"] != folder:
        target = want["folder"]
    return {
        **summary, "key": key,
        "album": summary["album"] if "|" not in key or summary["album"] else key.split("|", 1)[1],
        "art_url": _art_url(key=key),
        "status": (record or {}).get("status") or "pending",
        "ignored": bool((record or {}).get("ignored")),
        "confidence": (record or {}).get("confidence"),
        "release_mbid": (record or {}).get("release_mbid"),
        "plex": ({"rating_key": record["plex_key"], "guid": record.get("plex_guid"),
                  "matched": is_matched(record.get("plex_guid"))}
                 if record and record.get("plex_key") else None),
        "flags": _flags(issue_rows), "issue_count": len(issue_rows),
        "target_dirpath": target,
    }


async def artist(artist_dir: str) -> dict | None:
    unit_rows = await units.units_for_artist(artist_dir)
    if not unit_rows:
        return None
    records = await _album_records(list(unit_rows))
    _, all_issues = await issues_for(artist_dir=artist_dir, limit=5000)
    by_album: dict[str, list[dict]] = {}
    for issue in all_issues:
        for target in await _issue_album_targets(issue):
            by_album.setdefault(target, []).append(issue)
    albums = []
    for key, rows in unit_rows.items():
        folder = units.unit_folder(key)
        related = by_album.get(key, []) + [i for i in by_album.get(folder, [])
                                           if folder != key and i not in by_album.get(key, [])]
        albums.append(await _album_summary(key, rows, records.get(key), related))
    albums.sort(key=lambda a: (a["year"] or "9999", (a["album"] or "").casefold()))
    md = await db.fetchone("SELECT * FROM md_artists WHERE artist_dir = ?", (artist_dir,))
    plex_artist = None
    if md and md["plex_key"]:
        row = await db.fetchone("SELECT * FROM plex_items WHERE rating_key = ?", (md["plex_key"],))
        if row:
            plex_artist = {
                "rating_key": row["rating_key"], "title": row["title"], "guid": row["guid"],
                "matched": is_matched(row["guid"]), "mbid": row["mbid"],
                "genres": jlist(row["genres"]), "styles": jlist(row["styles"]),
                "moods": jlist(row["moods"]), "countries": jlist(row["countries"]),
                "summary": row["summary"]}
    want = json.loads(md["desired"]) if md and md["desired"] else None
    tracks = sum(len(r) for r in unit_rows.values())
    total_bytes = sum(f.get("filesize_bytes") or 0 for r in unit_rows.values() for f in r)
    counts = Counter(i["kind"] for i in all_issues)
    return {
        "artist_dir": artist_dir, "name": (want or {}).get("name") or artist_dir,
        "tracks": tracks, "albums_count": len(unit_rows), "bytes": total_bytes,
        "art_url": _art_url(artist_dir=artist_dir),
        "plex": plex_artist,
        "desired": ({k: want.get(k) for k in ("name", "mbid", "genres", "moods", "genre_source",
                                                "mood_source", "status", "country")}
                    if want else None),
        "musicbrainz_url": (want or {}).get("musicbrainz_url"),
        "issues": [i for i in all_issues if i["entity"] == "artist"],
        "issue_counts": dict(counts),
        "albums": albums,
    }


async def _issue_album_targets(issue: dict) -> list[str]:
    targets = [issue["album_key"]] if issue.get("album_key") else []
    d = issue.get("detail") or {}
    targets += d.get("dirpaths") or []
    targets += d.get("units") or []
    if issue["kind"] == "dup_track":
        targets += sorted({p.rsplit("/", 1)[0] for p in d.get("paths") or []})
    return list(dict.fromkeys(targets))


# ------------------------------------------------------------------ album
def _dominant(rows: list[dict], key: str):
    values = [r.get(key) for r in rows if r.get(key)]
    return Counter(values).most_common(1)[0][0] if values else None


def _norm(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return sorted(str(v).strip().casefold() for v in value if str(v).strip())
    return [str(value).strip().casefold()] if str(value).strip() else []


async def album(key: str) -> dict | None:
    rows = await units.load_unit(key)
    if not rows:
        return None
    folder = units.unit_folder(key)
    record = (await _album_records([key])).get(key)
    want = json.loads(record["desired"]) if record and record.get("desired") else None
    plex_info = await album_for_paths([r["path"] for r in rows]) or {}
    plex_album = plex_info.get("album")
    plex_tracks = plex_info.get("tracks") or {}
    _, album_issues = await issues_for(album_keys=list(dict.fromkeys([key, folder])), limit=500)
    summary = await _album_summary(key, rows, record, album_issues)
    from .desired import overrides_for
    overrides = await overrides_for("album", key)

    file_genres = sorted({g for r in rows for g in (r.get("genre") or "").split("; ") if g})
    plex_title_artist = None
    if plex_album:
        plex_title_artist = plex_album.get("parent_title")

    def row(field, label, file, desired, plex_value, editable=False):
        return {"field": field, "label": label, "file": file, "desired": desired,
                "plex": plex_value,
                "differs_file": want is not None and desired is not None
                and _norm(file) != _norm(desired),
                "differs_plex": want is not None and desired is not None
                and plex_value is not None and _norm(plex_value) != _norm(desired),
                "overridden": field in overrides, "editable": editable}

    w = want or {}
    pa = plex_album or {}
    compare = [
        row("album", "Album", _dominant(rows, "album"), w.get("album"), pa.get("title"), True),
        row("album_artist", "Album artist", _dominant(rows, "album_artist"), w.get("albumartist"),
            plex_title_artist, True),
        row("year", "Year", _dominant(rows, "year"), w.get("original_year"),
            str(pa["year"]) if pa.get("year") else None, True),
        row("edition", "Edition", None, w.get("edition"), None),
        row("genres", "Genres", file_genres or None, w.get("genres"),
            jlist(pa.get("genres")) if plex_album else None, True),
        row("moods", "Moods", None, w.get("moods"), jlist(pa.get("moods")) if plex_album else None,
            True),
        row("label", "Label", _dominant(rows, "label"), w.get("label"), pa.get("studio")),
        row("releasetype", "Release type", _dominant(rows, "releasetype"),
            "; ".join(w.get("releasetype") or []) or None, None),
        row("release_mbid", "MusicBrainz release", _dominant(rows, "mb_albumid"),
            w.get("release_mbid"), pa.get("mbid")),
        row("folder", "Folder", folder, w.get("folder"), pa.get("folder")),
    ]
    compare.append({"field": "plex_match", "label": "Plex match", "file": None,
                    "desired": w.get("release_mbid"),
                    "plex": pa.get("guid"), "differs_file": False,
                    "differs_plex": bool(plex_album) and not is_matched(pa.get("guid"))
                    and pa.get("mbid") != w.get("release_mbid"),
                    "overridden": False, "editable": False})

    dup_track_paths = {p for i in album_issues if i["kind"] == "dup_track"
                       for p in (i["detail"].get("paths") or [])}
    tracks = []
    for r in sorted(rows, key=lambda r: (r.get("disc_number") or 1, r.get("track_number") or 999,
                                         r["path"])):
        dt = (w.get("tracks") or {}).get(r["path"])
        pt = plex_tracks.get(r["path"])
        flags = []
        if r["path"] in dup_track_paths:
            flags.append({"kind": "dup_track", "severity": "warning", "label": "Duplicate"})
        if pt is None and plex_tracks is not None and await last_sync():
            flags.append({"kind": "not_in_plex", "severity": "warning", "label": "Not in Plex"})
        if dt and not dt.get("matched"):
            flags.append({"kind": "extra_tracks", "severity": "info", "label": "Not on release"})
        tracks.append({
            "path": r["path"],
            "file": {"title": r.get("title"), "artist": r.get("artist"),
                     "track_number": r.get("track_number"), "disc_number": r.get("disc_number"),
                     "duration_sec": r.get("duration_sec"), "format": r.get("format"),
                     "bitrate_kbps": r.get("bitrate_kbps"),
                     "lossless": (r.get("format") or "") in LOSSLESS_FORMATS,
                     "filesize_bytes": r.get("filesize_bytes"),
                     "genre": [g for g in (r.get("genre") or "").split("; ") if g],
                     "isrc": r.get("isrc"), "has_mbids": bool(r.get("mb_albumid")),
                     "junk": issues_mod.junk_of(r)},
            "desired": ({"title": dt.get("title"), "artist": dt.get("artist"),
                         "track_number": dt.get("tracknumber"),
                         "disc_number": dt.get("discnumber"), "target_relpath": dt.get("target"),
                         "recording_mbid": dt.get("recording_mbid"),
                         "matched": dt.get("matched")} if dt else None),
            "plex": ({"rating_key": pt["rating_key"], "title": pt.get("title"),
                      "index": pt.get("idx"), "parent_index": pt.get("parent_idx"),
                      "matched": is_matched(pt.get("guid")),
                      "genres": jlist(pt.get("genres"))} if pt else None),
            "flags": flags,
        })

    analysis = await library._analyse()
    album_groups = [g for g in analysis["albums"]
                    if any(units.unit_dir(i["dirpath"]) == folder for i in g["instances"])]
    paths = {r["path"] for r in rows}
    track_groups = [g for g in analysis["tracks"]
                    if any(c["path"] in paths for c in g.get("copies") or [])]

    move = None
    for issue in album_issues:
        if issue["kind"] in ("move", "move_conflict"):
            d = issue["detail"]
            move = {"from": d.get("from"), "to": d.get("to"), "files": d.get("files") or [],
                    "conflict": d.get("conflict")}
            break
    candidates = json.loads(record["candidates"]) if record and record.get("candidates") else []
    status = (record or {}).get("status")
    missing = None
    if record and record.get("release_mbid") and status == "resolved":
        row = await db.fetchone("SELECT id, state, detail FROM md_issues WHERE key = ?",
                                (f"missing_tracks:{record['release_mbid']}",))
        if row and row["state"] in OPEN_STATES:
            d = json.loads(row["detail"] or "{}")
            missing = {"issue_id": row["id"], "total": d.get("total"), "have": d.get("have"),
                       "tracks": d.get("missing") or [], "download": d.get("download"),
                       "partial": False,
                       "other_folders": [u for u in d.get("units") or [] if u != key]}
        else:
            # a few songs of a bigger album: not an issue, but the rest of it
            # is worth showing
            d = await missing_tracks.compute(record["release_mbid"])
            if d and d["missing"] and d["partial"]:
                missing = {"issue_id": None, "total": d["total"], "have": d["have"],
                           "tracks": d["missing"], "download": None, "partial": True,
                           "other_folders": [u for u in d["units"] if u != key]}
    return {
        **summary,
        "compare": compare, "tracks": tracks, "issues": album_issues,
        "duplicates": {"album_groups": album_groups, "track_groups": track_groups},
        "candidates": candidates, "move": move, "missing": missing,
        "history": await changes(album_key=key),
        "desired": ({k: w.get(k) for k in ("album", "albumartist", "original_year", "edition",
                                           "genres", "genre_source", "moods", "mood_source",
                                           "label", "folder", "score", "source")}
                    if want else None),
        "error": (record or {}).get("error"),
        "checked_at": _iso((record or {}).get("checked_at")),
        "musicbrainz_url": (f"https://musicbrainz.org/release/{record['release_mbid']}"
                            if record and record.get("release_mbid") else None),
        "can_seed": status in ("unresolved", "review") or status is None,
    }


# ------------------------------------------------------------------ history
async def changes(*, album_key: str | None = None, artist_dir: str | None = None,
                  limit: int = 50) -> list[dict]:
    where, params = [], []
    if album_key:
        where.append("album_key = ?")
        params.append(album_key)
    if artist_dir:
        where.append("artist_dir = ?")
        params.append(artist_dir)
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    rows = await db.fetchall(
        f"SELECT batch_id, MIN(applied_at) AS applied_at, MAX(summary) AS summary, "
        f"MIN(target) AS target, COUNT(*) AS count, "
        f"SUM(reverted_at IS NOT NULL) AS reverted_n, MAX(album_key) AS album_key, "
        f"MAX(artist_dir) AS artist_dir, MAX(id) AS last_id FROM md_changes {sql_where} "
        f"GROUP BY batch_id ORDER BY last_id DESC LIMIT ?", (*params, limit))
    return [{"batch_id": r["batch_id"], "applied_at": r["applied_at"], "summary": r["summary"],
             "target": r["target"], "count": r["count"],
             "reverted": r["reverted_n"] == r["count"], "album_key": r["album_key"],
             "artist_dir": r["artist_dir"]} for r in rows]
