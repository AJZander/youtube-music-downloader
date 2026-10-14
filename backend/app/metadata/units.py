"""Album units: the thing the metadata manager checks, one at a time.

A unit is normally an album folder, with its disc subfolders ("Album/CD1",
"Album/Disc 2 - Live") counted in, so a multi-disc set is identified as the
one release it is. A folder whose files carry two or more
different album tags (a "Singles" folder, a loose artist folder) is split
into one unit per album tag, keyed `<dirpath>|<album key>`, so each can be
identified as the release it is. Untagged files in such a folder join its
largest group.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from ..db import db
from ..library import album_key, strict_key

UNIT_COLUMNS = (
    "path, title, artist, album, album_artist, track_number, disc_number, year, format, "
    "bitrate_kbps, sample_rate, bit_depth, duration_sec, filesize_bytes, mtime, dirpath, "
    "artist_dir, provider_track_id, genre, isrc, mb_albumid, mb_releasegroupid, "
    "mb_albumartistid, mb_artistid, mb_trackid, mb_releasetrackid, compilation, "
    "releasetype, label, originaldate, junk, unreadable")


_DISC_DIR = re.compile(r"^(?:cd|disc|disk)\s*\d+\b", re.I)


def unit_dir(dirpath: str) -> str:
    """The album folder a file folder belongs to: a disc subfolder's parent
    (never an artist folder: `Artist/CD1` stays its own)."""
    parent, _, leaf = (dirpath or "").rpartition("/")
    return parent if "/" in parent and _DISC_DIR.match(leaf) else dirpath


def folder_of(row: dict) -> str:
    return unit_dir(row.get("dirpath")
                    or (row["path"].rsplit("/", 1)[0] if "/" in row["path"] else ""))


# What a folder's files may disagree on and still be one album: an edition
# or disc suffix ("Against All Oddz" / "Against All Oddz (Collector's
# Edition)", "Album (Disc 2)").
_SUFFIX = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$|\s*[-–—:,]?\s*\b(?:disc|cd|disk)\s*\d+\s*$", re.I)


def split_key(album: str | None, hint: str | None) -> str:
    name = album or ""
    for _ in range(3):
        stripped = _SUFFIX.sub("", name).strip()
        if stripped == name or not stripped:
            break
        name = stripped
    return album_key(name, hint)


def split_folder(rows: list[dict]) -> dict[str, list[dict]]:
    """unit key -> rows, for the rows of one folder."""
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


async def sync_registry() -> dict:
    """Make md_albums list exactly the units on disk: new units become due,
    units whose files changed become due again, vanished ones are dropped
    (their issues closed). Returns counts."""
    units = await all_units()
    known = {r["key"]: dict(r) for r in await db.fetchall(
        "SELECT key, file_sig, artist_dir FROM md_albums")}
    new_rows, changed = [], []
    for key, rows in units.items():
        sig = signature(rows)
        if key not in known:
            artist_dir = rows[0].get("artist_dir") or ""
            album_tag = next((r.get("album") for r in rows if r.get("album")), None)
            new_rows.append((key, unit_folder(key), artist_dir, album_tag))
        elif known[key]["file_sig"] != sig:
            changed.append(key)
    if new_rows:
        await db.executemany(
            "INSERT INTO md_albums (key, dirpath, artist_dir, album_tag, next_check) "
            "VALUES (?,?,?,?,0)", new_rows)
    for i in range(0, len(changed), 400):
        chunk = changed[i:i + 400]
        await db.execute(
            f"UPDATE md_albums SET next_check = 0 WHERE key IN ({','.join('?' * len(chunk))})",
            tuple(chunk))
    gone = [k for k in known if k not in units]
    for i in range(0, len(gone), 400):
        chunk = gone[i:i + 400]
        marks = ','.join('?' * len(chunk))
        await db.execute(f"DELETE FROM md_albums WHERE key IN ({marks})", tuple(chunk))
        await db.execute(
            f"UPDATE md_issues SET state = 'applied', updated_at = datetime('now') "
            f"WHERE state IN ('open', 'queued') AND album_key IN ({marks}) "
            f"AND kind NOT IN ('dup_album', 'dup_track', 'split_artist')", tuple(chunk))
    # an album's issues outlive it when it moves mid-apply under a new key
    from .issues import UNIT_KINDS
    await db.execute(
        f"UPDATE md_issues SET state = 'applied', updated_at = datetime('now') "
        f"WHERE state IN ('open', 'queued') AND album_key IS NOT NULL "
        f"AND kind IN ({','.join('?' * len(UNIT_KINDS))}) "
        f"AND album_key NOT IN (SELECT key FROM md_albums)", UNIT_KINDS)
    return {"units": len(units), "new": len(new_rows), "changed": len(changed),
            "gone": len(gone)}


def artist_key(name: str) -> str:
    return strict_key(name)
