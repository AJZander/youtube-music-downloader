"""Every path change, in one place.

A file in the library is named by several tables: the index
(library_index), the downloads that made it (tracks.library_path) and the
"not duplicates" decisions (library_exclusions); and through its folder by
the album's record (md_albums) and issues, a Download missing queued into it
(jobs.target_dir), the holds and throttles kept per folder (md_state) and
the Plex mirror. Each mover used to update its own subset of them, and the
rest went stale. Whatever moves files now calls relocate() once they are
where they are going; Trash calls forget(), and Restore unforget().

The share ignores case (`SAM SMITH/` is `Sam Smith/`), so paths are compared
by identity.path_key: library_index.path_key carries it, and db.connect()
registers the same function in SQL as path_key(). A folder the library
already has, in any case, is spelled the library's way (on_disk).

Only the database changes here: the files have moved already. Each call is
one transaction (db.transaction), with what it reads from the files read
before it starts."""
from __future__ import annotations

import asyncio
import os
import stat
from collections import Counter, defaultdict
from pathlib import Path

from .config import settings
from .db import db
from .identity import path_key, unit_dir

# md_state key: bumped whenever the index's paths change, for what caches
# the library's shape (P2-D3)
GENERATION = "library_generation"


def place(path: str) -> tuple[str, str]:
    """(dirpath, artist_dir) of a library path, as the scan records them."""
    parts = Path(path).parts
    return (str(Path(path).parent), parts[0]) if len(parts) > 1 else ("", "")


def _parent(path: str) -> str:
    return path.rpartition("/")[0]


def _artist(key: str) -> str:
    return key.split("|", 1)[0].split("/", 1)[0]


async def _run(func, *args):
    return await asyncio.get_running_loop().run_in_executor(None, func, *args)


async def bump() -> None:
    await db.execute("INSERT INTO md_state (key, value) VALUES (?, '1') ON CONFLICT(key) "
                     "DO UPDATE SET value = CAST(value AS INTEGER) + 1", (GENERATION,))


async def relocate(pairs, *, units: dict[str, str] | None = None,
                   reread: bool = False) -> dict[str, str | None]:
    """These files moved (old -> new library paths): everything that names
    them, or a folder they left, names where they are now.

    The index rows move with what they carry (tags, video id, unit id), and
    so do the downloads that made the files (however they spelt the path)
    and the decisions about them. A folder left with nothing indexed in it
    hands on what names it: the records of the albums that were there go
    where their files went (apply.rekey_unit: a record already there takes
    them in), and its "not duplicates" decisions, issues, Download-missing
    jobs, holds and Plex rows follow it.

    `units`: albums the mover moved whole, {old key: new key}; their records
    follow them even from a folder that still holds other files. `reread`:
    the moved files' tags changed just before they moved; read them now.
    Returns {old key: unit id} for each album record that moved."""
    pairs = [(old, new) for old, new in dict(pairs).items() if old != new]
    units = units or {}
    if not pairs and not units:
        return {}
    read = await read_files([new for _, new in pairs]) if pairs and reread else []
    async with db.transaction():
        if pairs:
            # a row already at a new path is for a file that wasn't there (the
            # move found the name free): it gives way
            await db.executemany(
                "UPDATE OR REPLACE library_index SET path = ?, dirpath = ?, artist_dir = ?, "
                "path_key = ? WHERE path = ?",
                [(new, *place(new), path_key(new), old) for old, new in pairs])
            if read:
                await _index(read)
            await _follow_tracks(pairs)
            for old, new in pairs:
                await rekey_exclusions("track", old, new)
        moved = await _follow_folders(pairs, units)
        await bump()
    return moved


async def _follow_tracks(pairs: list[tuple[str, str]]) -> None:
    """The downloads that made these files, however the path was spelt
    when they were recorded."""
    to = {path_key(old): new for old, new in pairs}
    updates = []
    for i in range(0, len(pairs), 400):
        olds = [old for old, _ in pairs[i:i + 400]]
        marks = ",".join("?" * len(olds))
        for r in await db.fetchall(
                f"SELECT id, library_path FROM tracks WHERE library_path IN ({marks}) "
                f"OR path_key(library_path) IN ({marks})",
                (*olds, *(path_key(o) for o in olds))):
            updates.append((to[path_key(r["library_path"])], r["id"]))
    if updates:
        await db.executemany(TRACK_PATH, updates)


# by name, so a test can ask SQLite how it is answered (by the primary key;
# the lookup above matches any spelling, path_key(), which no index serves)
TRACK_PATH = "UPDATE tracks SET library_path = ? WHERE id = ?"


async def rekey_exclusions(scope: str, old: str, new: str) -> None:
    """"Not duplicates" decisions follow a renamed file or folder, each pair
    in the order every lookup makes it (library.pair_key). One the new name
    already has (merged copies) is kept once."""
    from .library import pair_key
    if old == new:
        return
    for r in await db.fetchall("SELECT id, key_a, key_b FROM library_exclusions "
                               "WHERE scope = ? AND (key_a = ? OR key_b = ?)", (scope, old, old)):
        a, b = pair_key(*(new if k == old else k for k in (r["key_a"], r["key_b"])))
        await db.execute("UPDATE OR IGNORE library_exclusions SET key_a = ?, key_b = ? "
                         "WHERE id = ?", (a, b, r["id"]))
    await db.execute("DELETE FROM library_exclusions WHERE scope = ? AND (key_a = ? OR key_b = ?)",
                     (scope, old, old))


async def _holds_files(folder: str) -> bool:
    """Whether the index has a file in this folder (spelled so) or below it."""
    return await db.fetchone("SELECT 1 FROM library_index WHERE path >= ? AND path < ? LIMIT 1",
                             (folder + "/", folder + "0")) is not None


async def _landing(key: str, unit_id: str | None,
                   moved: list[str]) -> tuple[str | None, list[str]]:
    """Where an album's files are now: the unit holding most of the files
    last seen in it, and those files. None stamped yet (the registry hasn't
    numbered them), the files just moved out of its folder that no album
    has numbered, if it was the folder's one album. An album with none of
    its files left (Trash took them) hands nothing to the one beside it: the
    registry drops its record."""
    from .metadata import units
    pool = [r["path"] for r in await db.fetchall(
        "SELECT path FROM library_index WHERE unit_id = ? AND dirpath IS NOT NULL",
        (unit_id,))] if unit_id else []
    if not pool and key == units.unit_folder(key):
        for i in range(0, len(moved), 400):
            chunk = moved[i:i + 400]
            pool += [r["path"] for r in await db.fetchall(
                f"SELECT path FROM library_index WHERE unit_id IS NULL "
                f"AND path IN ({','.join('?' * len(chunk))})", tuple(chunk))]
    if not pool:
        return None, []
    wanted = set(pool)
    rows = await units.rows_for_folders(sorted({unit_dir(_parent(p)) for p in pool}))
    counts = {key: sum(1 for r in members if r["path"] in wanted)
              for key, members in units.build_units(rows).items()}
    best = max(sorted(counts.items()), key=lambda kv: kv[1], default=(None, 0))
    return (best[0] if best[1] else None), pool


async def _follow_folders(pairs: list[tuple[str, str]], moving: dict[str, str]) -> dict:
    from .metadata import apply, units
    # each folder files left, and the album folder above a disc subfolder:
    # where most of their files went
    votes: dict[str, Counter] = defaultdict(Counter)
    landed: dict[str, list[str]] = defaultdict(list)
    for old, new in pairs:
        po, pn = _parent(old), _parent(new)
        if not po:
            continue
        uo = unit_dir(po)
        votes[uo][unit_dir(pn)] += 1
        landed[uo].append(new)
        if po != uo:
            votes[po][pn] += 1
    moved: dict[str, str | None] = {}
    for old_key, new_key in moving.items():
        paths = landed.get(units.unit_folder(old_key)) or [new for _, new in pairs]
        moved[old_key] = await apply.rekey_unit(old_key, new_key, _artist(new_key), paths)
    emptied: dict[str, str] = {}
    for folder, counter in sorted(votes.items()):
        to = max(sorted(counter.items()), key=lambda kv: kv[1])[0]
        if to != folder and not await _holds_files(folder):
            emptied[folder] = to
    for folder, to in emptied.items():
        for r in await db.fetchall(
                "SELECT key, unit_id FROM md_albums WHERE key = ? OR (key >= ? AND key < ?)",
                (folder, folder + "|", folder + "}")):
            if r["key"] in moving:
                continue
            target, pool = await _landing(r["key"], r["unit_id"], landed.get(folder) or [])
            if target and target != r["key"]:
                moved[r["key"]] = await apply.rekey_unit(r["key"], target, _artist(target), pool)
        await rekey_exclusions("album", folder, to)
        await units.repoint(folder, to, _artist(to))
        await _follow_state(folder, to)
        # Plex's paths are Plex's to say: read them again
        await db.execute("UPDATE plex_items SET detail_at = 0 WHERE folder = ? "
                         "OR (folder >= ? AND folder < ?)", (folder, folder + "/", folder + "0"))
    if emptied:
        await _follow_jobs(emptied)
    return moved


async def _follow_state(folder: str, to: str) -> None:
    """The hold a merge of copies put on the folder, and its re-add throttle."""
    from .metadata.issues import MERGE_KEEPER
    for old, new in ((MERGE_KEEPER + folder.casefold(), MERGE_KEEPER + to.casefold()),
                     (f"plex_readd:{folder}", f"plex_readd:{to}")):
        if old != new:
            await db.execute("UPDATE OR IGNORE md_state SET key = ? WHERE key = ?", (new, old))
            await db.execute("DELETE FROM md_state WHERE key = ?", (old,))


def renamer(folders: dict[str, str]):
    """path -> the path with the folder it is in (or under) renamed as
    `folders` says, or None if it is in none of them. Whole names compare,
    in any case: `Album` is never `Album 2`."""
    keys = {path_key(f): (f.count("/") + 1, to) for f, to in folders.items()}

    def rename(path: str) -> str | None:
        parts = path.split("/")
        for n in range(len(parts), 0, -1):
            hit = keys.get(path_key("/".join(parts[:n])))
            if hit and hit[0] == n:
                return "/".join([hit[1], *parts[n:]])
        return None
    return rename


async def _follow_jobs(folders: dict[str, str]) -> None:
    """A Download missing's folder, queued or done."""
    rename = renamer(folders)
    updates = []
    for r in await db.fetchall("SELECT id, target_dir FROM jobs WHERE target_dir IS NOT NULL"):
        new = rename(r["target_dir"])
        if new and new != r["target_dir"]:
            updates.append((new, r["id"]))
    if updates:
        await db.executemany("UPDATE jobs SET target_dir = ? WHERE id = ?", updates)


async def forget(paths, folders=()) -> None:
    """These files went to Trash, and these folders with all they held: out
    of the index. Whatever else names them still does (a download's
    library_path is the file's history, and a path in Trash is not lost),
    so a restore finds them named where they were."""
    paths = list(paths)
    async with db.transaction():
        for i in range(0, len(paths), 400):
            chunk = paths[i:i + 400]
            await db.execute(f"DELETE FROM library_index WHERE path IN "
                             f"({','.join('?' * len(chunk))})", tuple(chunk))
        for folder in folders:
            await db.execute("DELETE FROM library_index WHERE path >= ? AND path < ?",
                             (folder + "/", folder + "0"))
        await bump()


async def unforget(paths) -> int:
    """Files (or folders of them) back from Trash: into the index at once,
    with the video id the download that made each recorded. Returns how
    many files were read."""
    from .library import AUDIO_SUFFIXES
    root = settings.library_dir

    def audio() -> list[str]:
        out = []
        for rel in paths:
            p = root / rel
            if p.is_dir():
                out += [str(f.relative_to(root)) for f in sorted(p.rglob("*"))
                        if f.is_file() and f.suffix.lower() in AUDIO_SUFFIXES
                        and not f.name.startswith(".")]
            elif p.suffix.lower() in AUDIO_SUFFIXES:
                out.append(rel)
        return out

    files = await _run(audio)
    read = await read_files(files)
    async with db.transaction():
        await _index(read)
        await _relink(files)
        await bump()
    return len(read)


async def _relink(paths: list[str]) -> None:
    """The video id of each file, from the download that made it."""
    found: dict[str, str] = {}
    for i in range(0, len(paths), 400):
        chunk = paths[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for r in await db.fetchall(
                f"SELECT library_path, provider_track_id FROM tracks WHERE status = 'completed' "
                f"AND provider_track_id IS NOT NULL AND (library_path IN ({marks}) "
                f"OR path_key(library_path) IN ({marks})) ORDER BY finished_at, created_at",
                (*chunk, *(path_key(p) for p in chunk))):
            found[path_key(r["library_path"])] = r["provider_track_id"]
    updates = [(found[path_key(p)], p) for p in paths if path_key(p) in found]
    if updates:
        await db.executemany("UPDATE library_index SET provider_track_id = ? "
                             "WHERE path = ? AND provider_track_id IS NULL", updates)


async def reread_files(paths: list[str]) -> int:
    """Read these files into the index as the scan does (one not there, or
    that the scan wouldn't index, is skipped: a folder, a cover, a dot
    name). Returns how many were read."""
    rows = await read_files(paths)
    await _index(rows)
    return len(rows)


async def _index(rows: list[tuple]) -> None:
    from . import library
    if rows:
        await db.executemany(library._UPSERT, rows)


async def read_files(paths: list[str]) -> list[tuple]:
    """The index rows of these files, read as the scan reads them (see
    reread_files); nothing is written."""
    from . import library
    root = settings.library_dir

    def read_all() -> list[tuple]:
        out = []
        for rel in paths:
            if Path(rel).suffix.lower() not in library.AUDIO_SUFFIXES \
                    or any(part.startswith(".") for part in Path(rel).parts):
                continue
            try:
                st = (root / rel).stat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            out.append(library._read_file(root, rel, st.st_size, st.st_mtime))
        return out

    return await _run(read_all)


async def on_disk(folder: str) -> str:
    """`folder` (library-relative) as the library already spells it, name
    by name: the index's spelling, else the share's (a folder the index
    doesn't know yet). Names compare whole, in any case and Unicode form,
    never by how a name starts: `(2014) In the Lonely Hour` is not `(2014)
    In the Lonely Hour (Deluxe)`. A name the library has nowhere is kept."""
    spelled: list[str] = []
    for name in (p for p in folder.split("/") if p):
        spelled.append(await _spelling(spelled, name) or name)
    return "/".join(spelled)


async def _spelling(above: list[str], name: str) -> str | None:
    key = path_key("/".join([*above, name]))
    row = await db.fetchone("SELECT path FROM library_index WHERE path_key >= ? AND path_key < ? "
                            "LIMIT 1", (key + "/", key + "0"))
    if row is not None:
        parts = row["path"].split("/")
        if len(parts) > len(above) and path_key(parts[len(above)]) == path_key(name):
            return parts[len(above)]
    parent = settings.library_dir.joinpath(*above)

    def listed() -> str | None:
        try:
            names = os.listdir(parent)
        except OSError:
            return None
        hits = sorted(n for n in names
                      if path_key(n) == path_key(name) and (parent / n).is_dir())
        return name if name in hits else (hits[0] if hits else None)

    return await _run(listed)
