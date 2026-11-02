"""Album units: the thing the metadata manager checks, one at a time.

A unit is normally an album folder, with its disc subfolders ("Album/CD1",
"Album/Disc 2 - Live") counted in, so a multi-disc set is identified as the
one release it is. A folder whose files carry two or more
different album tags (a "Singles" folder, a loose artist folder) is split
into one unit per album tag, keyed `<dirpath>|<album key>`, so each can be
identified as the release it is. Untagged files in such a folder join its
largest group.

A unit's key is where it is; its unit_id is who it is. Issues, overrides
and pauses hang off the unit_id, so they follow the album when a move, a
merge or a retag changes its key (md_albums.unit_id, and on each file
library_index.unit_id: the unit it was last seen in).
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections import Counter, defaultdict

from ..db import db, utcnow
from ..identity import split_key, strict_key, unit_dir  # noqa: F401

UNIT_COLUMNS = (
    "path, title, artist, album, album_artist, track_number, disc_number, year, format, "
    "bitrate_kbps, sample_rate, bit_depth, duration_sec, filesize_bytes, mtime, dirpath, "
    "artist_dir, provider_track_id, genre, isrc, mb_albumid, mb_releasegroupid, "
    "mb_albumartistid, mb_artistid, mb_trackid, mb_releasetrackid, compilation, "
    "releasetype, label, originaldate, junk, unreadable")


def folder_of(row: dict) -> str:
    return unit_dir(row.get("dirpath")
                    or (row["path"].rsplit("/", 1)[0] if "/" in row["path"] else ""))


def split_folder(rows: list[dict]) -> dict[str, list[dict]]:
    """unit key -> rows, for the rows of one folder. Files whose album tags
    differ only by an edition or disc suffix are one album (identity.split_key);
    a live album, an instrumental edition, the remixes or a re-recording
    beside its original are two."""
    if not rows:
        return {}
    folder = folder_of(rows[0])
    groups: dict[str, list[dict]] = defaultdict(list)
    untagged: list[dict] = []
    for row in rows:
        k = split_key(row.get("album"), row.get("album_artist") or row.get("artist_dir"))
        if k:
            groups[k].append(row)
        else:
            untagged.append(row)
    if len(groups) <= 1:
        return {folder: rows}
    largest = max(groups, key=lambda k: len(groups[k]))
    groups[largest].extend(untagged)
    return {f"{folder}|{k}": members for k, members in groups.items()}


def build_units(rows: list[dict]) -> dict[str, list[dict]]:
    by_folder: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_folder[folder_of(row)].append(row)
    units: dict[str, list[dict]] = {}
    for members in by_folder.values():
        units.update(split_folder(members))
    return units


def unit_folder(key: str) -> str:
    return key.split("|", 1)[0]


_YEAR_NAME = re.compile(r"^\s*[\(\[]?(?:19|20)\d{2}[\)\]]?\s*[-–—.]?\s+\S")


async def in_box_set(key: str, rows: list[dict]) -> bool:
    """Whether this unit is one album of a box set, which keeps its own copy:
    it is never moved out to the album's folder.

    Either its discs sit in disc subfolders of a folder holding other albums
    too (`The Ultimate Collection Box, 20CD/CD 06 - News Of The World`, split
    by album tag), or its folder sits inside a dated collection's that holds
    other albums (`(2010) The Original Mono Recordings/1964 - Another Side Of
    Bob Dylan`). A dated folder of one album's discs (`2011 Remixes (3CD)/
    ... (CD3)`) is that album, not a set."""
    if not rows:
        return False
    folder = unit_folder(key)
    if "|" in key and all((r.get("dirpath") or "") != folder for r in rows):
        return True
    artist_dir = rows[0].get("artist_dir") or ""
    parent = folder.rpartition("/")[0]
    if not parent or parent == artist_dir or not _YEAR_NAME.match(parent.rsplit("/", 1)[-1]):
        return False
    albums = {split_key(r["album"], r["album_artist"] or artist_dir)
              for r in await db.fetchall(
                  "SELECT DISTINCT album, album_artist FROM library_index "
                  "WHERE dirpath >= ? AND dirpath < ? AND album IS NOT NULL AND album != ''",
                  (parent + "/", parent + "0"))}
    return len(albums - {""}) > 1


def signature(rows: list[dict]) -> str:
    h = hashlib.blake2b(digest_size=12)
    for row in sorted(rows, key=lambda r: r["path"]):
        h.update(f"{row['path']}\0{row.get('filesize_bytes')}\0{row.get('mtime')}\n".encode())
    return h.hexdigest()


async def rows_for_folders(folders: list[str]) -> list[dict]:
    """The rows of these album folders, disc subfolders included."""
    out: list[dict] = []
    wanted = set(folders)
    for i in range(0, len(folders), 100):
        chunk = folders[i:i + 100]
        # a folder's own files, and its subfolders' ("/" sorts just before "0")
        where = " OR ".join(["(dirpath = ? OR (dirpath >= ? AND dirpath < ?))"] * len(chunk))
        params = [v for f in chunk for v in (f, f + "/", f + "0")]
        out.extend(dict(r) for r in await db.fetchall(
            f"SELECT {UNIT_COLUMNS} FROM library_index WHERE {where}", tuple(params)))
    return [r for r in out if folder_of(r) in wanted]


async def load_unit(key: str) -> list[dict]:
    """The current rows of one unit (empty if it no longer exists)."""
    folder = unit_folder(key)
    rows = await rows_for_folders([folder])
    if folder == "" or not rows:
        return []
    return split_folder(rows).get(key, [])


async def units_for_artist(artist_dir: str) -> dict[str, list[dict]]:
    rows = [dict(r) for r in await db.fetchall(
        f"SELECT {UNIT_COLUMNS} FROM library_index WHERE artist_dir = ?", (artist_dir,))]
    return build_units(rows)


async def all_units() -> dict[str, list[dict]]:
    rows = [dict(r) for r in await db.fetchall(
        f"SELECT {UNIT_COLUMNS} FROM library_index WHERE dirpath IS NOT NULL")]
    return build_units(rows)


def new_unit_id() -> str:
    return secrets.token_hex(8)


def _landings(units: dict[str, list[dict]], stamps: dict[str, str],
              wanted: set[str]) -> dict[str, tuple[str, int]]:
    """unit id -> (the key of the unit now holding most of the files stamped
    with it, how many), for the unit ids in `wanted`."""
    counts: dict[str, Counter] = {}
    for key, rows in units.items():
        for r in rows:
            uid = stamps.get(r["path"])
            if uid and uid in wanted:
                counts.setdefault(uid, Counter())[key] += 1
    return {uid: max(c.items(), key=lambda kv: (kv[1], kv[0])) for uid, c in counts.items()}


async def successor(unit_id: str) -> str | None:
    """The key of the unit now holding most of the files last seen in this
    one, or None (they are gone, or were never seen)."""
    stamped = [dict(r) for r in await db.fetchall(
        "SELECT path, dirpath FROM library_index WHERE unit_id = ? AND dirpath IS NOT NULL",
        (unit_id,))]
    if not stamped:
        return None
    rows = await rows_for_folders(sorted({folder_of(r) for r in stamped}))
    landed = _landings(build_units(rows), {r["path"]: unit_id for r in stamped}, {unit_id})
    return landed[unit_id][0] if unit_id in landed else None


async def stamp(unit_id: str | None, paths) -> None:
    """These files are the unit's now."""
    if unit_id:
        await db.executemany("UPDATE library_index SET unit_id = ? WHERE path = ?",
                             [(unit_id, p) for p in paths])


async def unit_id_of(key: str | None) -> str | None:
    row = await db.fetchone("SELECT unit_id FROM md_albums WHERE key = ?", (key,)) if key else None
    return row["unit_id"] if row else None


async def repoint(old_key: str, new_key: str, artist_dir: str | None) -> None:
    """What names an album by its key (its issues, their listings, the
    journal) names it by its new one."""
    for table in ("md_changes", "md_issues", "md_issue_refs"):
        await db.execute(f"UPDATE {table} SET album_key = ?, artist_dir = ? WHERE album_key = ?",
                         (new_key, artist_dir, old_key))


async def rekey(old_key: str, new_key: str, *, artist_dir: str | None) -> None:
    """A unit's record follows it to a new key, decisions and all."""
    await db.execute("UPDATE md_albums SET key = ?, dirpath = ?, artist_dir = ?, next_check = 0 "
                     "WHERE key = ?", (new_key, unit_folder(new_key), artist_dir, old_key))
    await repoint(old_key, new_key, artist_dir)


async def _tagged_groups(key: str, *, besides: str | None = None) -> set[str]:
    """The release groups the unit's files say they are, if they say; not
    counting the files of unit `besides` (an album moving in: its files are
    there already, and would speak for their own group)."""
    theirs = {r["path"] for r in await db.fetchall(
        "SELECT path FROM library_index WHERE unit_id = ?", (besides,))} if besides else set()
    return {r["mb_releasegroupid"] for r in await load_unit(key)
            if r.get("mb_releasegroupid") and r["path"] not in theirs}


def _until(value: str | None) -> float:
    try:
        return float(json.loads(value)["until"]) if value else 0.0
    except (ValueError, KeyError, TypeError):
        return 0.0


async def merge_into(v_uid: str, k_uid: str, *, reason: str) -> None:
    """Album v is album k now (its files went there): one record, holding
    the decisions of both that don't conflict. Every rule keeps files: what
    either side ignored or dismissed stays so, a pinned release goes with
    its files only to an album of its release group (or one not known to be
    any), and k's own overrides win. v leaves an alias, so a link to it
    finds k."""
    from .apply import AUTO_PAUSED
    from .issues import UNIT_KINDS
    if not v_uid or not k_uid or v_uid == k_uid:
        return
    v = await db.fetchone("SELECT * FROM md_albums WHERE unit_id = ?", (v_uid,))
    k = await db.fetchone("SELECT * FROM md_albums WHERE unit_id = ?", (k_uid,))
    if v is None or k is None:
        return
    v, k = dict(v), dict(k)
    manual, release, no_release = k["manual"], k["release_mbid"], k["no_release"]
    if not k["manual"] and v["manual"] and v["release_mbid"]:
        # k's group, else every group its own files name: one other is enough
        # to keep the pin behind
        groups = ({k["releasegroup_mbid"]} if k["releasegroup_mbid"]
                  else await _tagged_groups(k["key"], besides=v_uid))
        if groups <= {v["releasegroup_mbid"]}:
            manual, release, no_release = 1, v["release_mbid"], 0
    if not manual and v["no_release"]:
        no_release = 1
    await db.execute("UPDATE md_albums SET ignored = ?, manual = ?, release_mbid = ?, "
                     "no_release = ?, next_check = 0 WHERE unit_id = ?",
                     (int(bool(k["ignored"] or v["ignored"])), manual, release, no_release, k_uid))
    # overrides: k's field where both have one
    await db.execute("UPDATE OR IGNORE md_overrides SET ref = ? WHERE entity = 'album' AND ref = ?",
                     (k_uid, v_uid))
    await db.execute("DELETE FROM md_overrides WHERE entity = 'album' AND ref = ?", (v_uid,))
    # a pause: whichever lasts longer
    paused = {r["key"]: r["value"] for r in await db.fetchall(
        "SELECT key, value FROM md_state WHERE key IN (?, ?)",
        (AUTO_PAUSED + v_uid, AUTO_PAUSED + k_uid))}
    if AUTO_PAUSED + v_uid in paused:
        if _until(paused[AUTO_PAUSED + v_uid]) > _until(paused.get(AUTO_PAUSED + k_uid)):
            await db.execute("INSERT INTO md_state (key, value) VALUES (?, ?) ON CONFLICT(key) "
                             "DO UPDATE SET value = excluded.value",
                             (AUTO_PAUSED + k_uid, paused[AUTO_PAUSED + v_uid]))
        await db.execute("DELETE FROM md_state WHERE key = ?", (AUTO_PAUSED + v_uid,))
    # issues: v's become k's; where k has its own, a dismissal on either
    # side stands and v's is closed
    await repoint(v["key"], k["key"], k["artist_dir"])
    now = utcnow()
    of_unit = f"SELECT id, kind, state FROM md_issues WHERE key IN ({','.join('?' * len(UNIT_KINDS))})"
    mine = {r["kind"]: dict(r) for r in await db.fetchall(
        of_unit, tuple(f"{kind}:{k_uid}" for kind in UNIT_KINDS))}
    for r in await db.fetchall(of_unit, tuple(f"{kind}:{v_uid}" for kind in UNIT_KINDS)):
        theirs = mine.get(r["kind"])
        if theirs is None:
            await db.execute("UPDATE md_issues SET key = ?, ref = ? WHERE id = ?",
                             (f"{r['kind']}:{k_uid}", k_uid, r["id"]))
            continue
        if r["state"] == "dismissed" and theirs["state"] != "dismissed":
            await db.execute("UPDATE md_issues SET state = 'dismissed', updated_at = ? "
                             "WHERE id = ?", (now, theirs["id"]))
        await db.execute("UPDATE md_issues SET state = 'applied', error = ?, updated_at = ? "
                         "WHERE id = ?", (f"merged into {k['key']}", now, r["id"]))
    await db.execute(
        "INSERT INTO md_unit_aliases (unit_id, successor, old_key, reason, created_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(unit_id) DO UPDATE SET successor = excluded.successor, "
        "old_key = excluded.old_key, reason = excluded.reason, created_at = excluded.created_at",
        (v_uid, k_uid, v["key"], reason, now))
    await db.execute("UPDATE md_unit_aliases SET successor = ? WHERE successor = ?", (k_uid, v_uid))
    await db.execute("UPDATE library_index SET unit_id = ? WHERE unit_id = ?", (k_uid, v_uid))
    await db.execute("DELETE FROM md_albums WHERE unit_id = ?", (v_uid,))


async def follow(key: str) -> str | None:
    """A unit whose files have left `key` follows them to the unit holding
    most of them: its record takes that key, or merges into the album
    there. Returns where it went, or None."""
    row = await db.fetchone("SELECT unit_id FROM md_albums WHERE key = ?", (key,))
    to = await successor(row["unit_id"]) if row and row["unit_id"] else None
    if to is None or to == key:
        return None
    there = await unit_id_of(to)
    if there:
        await merge_into(row["unit_id"], there, reason=f"its files are in {to}")
    else:
        rows = await load_unit(to)
        await rekey(key, to, artist_dir=(rows[0].get("artist_dir") if rows else None))
    return to


async def resolve(ref: str | None) -> dict | None:
    """What an album reference names now: {"key", "unit_id"} for a live
    album (named by its unit id, or by its key as old links have it; a key
    holding files the registry hasn't numbered yet has unit_id None);
    {"redirect": {"unit_id", "key", "artist_dir"}} for one merged into
    another (named by its old id, or a key it had and no files hold now);
    else None."""
    if not ref:
        return None
    row = await db.fetchone("SELECT key, unit_id FROM md_albums WHERE unit_id = ?", (ref,)) or \
        await db.fetchone("SELECT key, unit_id FROM md_albums WHERE key = ?", (ref,))
    if row:
        return {"key": row["key"], "unit_id": row["unit_id"]}
    alias = await db.fetchone("SELECT successor FROM md_unit_aliases WHERE unit_id = ?", (ref,))
    if alias is None:
        # files back at a merged-away key (an undone merge, a split piece
        # made again) are that album, not the one it joined
        if await load_unit(ref):
            return {"key": ref, "unit_id": None}
        alias = await db.fetchone("SELECT successor FROM md_unit_aliases WHERE old_key = ? "
                                  "ORDER BY created_at DESC, rowid DESC LIMIT 1", (ref,))
    for _ in range(10):
        if alias is None:
            return None
        row = await db.fetchone("SELECT key, unit_id, artist_dir FROM md_albums WHERE unit_id = ?",
                                (alias["successor"],))
        if row:
            return {"redirect": {"unit_id": row["unit_id"], "key": row["key"],
                                 "artist_dir": row["artist_dir"]}}
        alias = await db.fetchone("SELECT successor FROM md_unit_aliases WHERE unit_id = ?",
                                  (alias["successor"],))
    return None


async def sync_registry() -> dict:
    """Make md_albums list exactly the units on disk: new units become due,
    units whose files changed become due again. A unit whose key is gone
    follows its files to the unit now holding most of them: it takes that
    unit's new key (a folder renamed or split by album tag), or merges into
    the album already there. One whose files are gone is dropped, and its
    issues closed. Every file is then stamped with its unit. Returns counts."""
    units = await all_units()
    known = {r["key"]: dict(r) for r in await db.fetchall(
        "SELECT key, unit_id, file_sig, artist_dir FROM md_albums")}
    stamps = {r["path"]: r["unit_id"] for r in await db.fetchall(
        "SELECT path, unit_id FROM library_index WHERE unit_id IS NOT NULL")}
    gone = [k for k in known if k not in units]
    landed = _landings(units, stamps, {known[k]["unit_id"] for k in gone if known[k]["unit_id"]})
    # the unit landing most of its files on a key takes it; a second one
    # landing there merges into it
    claimed: dict[str, str] = {}
    followed: set[str] = set()
    successors = merged = 0
    for k in sorted(gone, key=lambda k: (-landed.get(known[k]["unit_id"], ("", 0))[1], k)):
        uid = known[k]["unit_id"]
        if uid not in landed:
            continue
        to = landed[uid][0]
        followed.add(k)
        if to in known or to in claimed:
            await merge_into(uid, known[to]["unit_id"] if to in known else claimed[to],
                             reason=f"its files are in {to}")
            merged += 1
        else:
            await rekey(k, to, artist_dir=units[to][0].get("artist_dir") or "")
            claimed[to] = uid
            successors += 1
    ids = {**{k: r["unit_id"] for k, r in known.items() if k in units}, **claimed}
    new_rows, changed = [], []
    for key, rows in units.items():
        sig = signature(rows)
        if key in known:
            if known[key]["file_sig"] != sig:
                changed.append(key)
        elif key not in claimed:
            ids[key] = new_unit_id()
            artist_dir = rows[0].get("artist_dir") or ""
            album_tag = next((r.get("album") for r in rows if r.get("album")), None)
            new_rows.append((key, ids[key], unit_folder(key), artist_dir, album_tag))
    if new_rows:
        await db.executemany(
            "INSERT INTO md_albums (key, unit_id, dirpath, artist_dir, album_tag, next_check) "
            "VALUES (?,?,?,?,?,0)", new_rows)
    for i in range(0, len(changed), 400):
        chunk = changed[i:i + 400]
        await db.execute(
            f"UPDATE md_albums SET next_check = 0 WHERE key IN ({','.join('?' * len(chunk))})",
            tuple(chunk))
    gone = [k for k in gone if k not in followed]
    for i in range(0, len(gone), 400):
        chunk = gone[i:i + 400]
        marks = ','.join('?' * len(chunk))
        await db.execute(f"DELETE FROM md_albums WHERE key IN ({marks})", tuple(chunk))
        await db.execute(
            f"UPDATE md_issues SET state = 'applied', error = NULL, updated_at = datetime('now') "
            f"WHERE state IN ('open', 'queued', 'failed') AND album_key IN ({marks}) "
            f"AND kind NOT IN ('dup_album', 'dup_track', 'split_artist')", tuple(chunk))
    # an album's issues outlive it when it moves mid-apply under a new key
    from .issues import UNIT_KINDS
    await db.execute(
        f"UPDATE md_issues SET state = 'applied', error = NULL, updated_at = datetime('now') "
        f"WHERE state IN ('open', 'queued', 'failed') AND album_key IS NOT NULL "
        f"AND kind IN ({','.join('?' * len(UNIT_KINDS))}) "
        f"AND album_key NOT IN (SELECT key FROM md_albums)", UNIT_KINDS)
    # each file carries the unit it is in, for the pass that finds it moved
    restamp = [(ids[key], r["path"]) for key, rows in units.items() if ids.get(key)
               for r in rows if stamps.get(r["path"]) != ids[key]]
    if restamp:
        await db.executemany("UPDATE library_index SET unit_id = ? WHERE path = ?", restamp)
    return {"units": len(units), "new": len(new_rows), "changed": len(changed),
            "gone": len(gone), "successors": successors, "merged": merged,
            "stamped": len(restamp)}


def artist_key(name: str) -> str:
    return strict_key(name)
