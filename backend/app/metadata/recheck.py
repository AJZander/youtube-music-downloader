"""Needs attention brought up to date at once after the duplicates manager
changes the library: a Trash, a Restore, "not duplicates", an artist merge.

The cycle would get there (its duplicates pass, and each album once it is
due), but until then the issue just dealt with was still listed. So the
handler, once its action is done, has the duplicate findings made again
and the albums and artists in the folders it touched checked again, as the
album page's own actions check theirs.

It only plans. Nothing is applied here: an album left with fixes to make
stays due, so the next cycle queues them as it would have."""
from __future__ import annotations

import logging

from ..db import db
from ..events import bus
from . import dupes, issues, units

log = logging.getLogger("mdl.metadata")

# albums checked before the handler returns; the rest wait for the cycle,
# first in line
CAP = 50


async def after_library_change(folders, *, artist_dirs=(), cap: int = CAP) -> dict:
    """`folders` (album or artist folders) and `artist_dirs` just changed:
    the duplicate findings, then every album in or under the folders (those
    the registry has and those on disk now: one gone follows its files or
    closes), then the artists. Returns counts."""
    from .runner import runner
    out = {"duplicates": None, "checked": 0, "due": 0, "artists": 0}
    try:
        out["duplicates"] = await dupes.sync()
    except Exception:
        log.exception("duplicate sync after a library change failed")
    keys = await _albums_in(sorted({units.unit_dir(f) for f in folders if f}))
    checked, later, with_fixes, failed = keys[:cap], keys[cap:], [], 0
    for key in checked:
        try:
            result = await runner.check_unit(key)
        except Exception:
            log.exception("check failed for %s", key)
            later.append(key)
            failed += 1
            continue
        if result.get("auto_ids"):
            with_fixes.append(key)
    await _due(later, "next_check = 0, priority = 10")
    await _due(with_fixes, "next_check = 0")
    out["checked"], out["due"] = len(checked) - failed, len(later)
    if later:
        runner.wake()

    artists = sorted({a for a in artist_dirs if a})
    for artist_dir in artists:
        try:
            if await db.fetchone("SELECT 1 FROM library_index WHERE artist_dir = ? LIMIT 1",
                                 (artist_dir,)):
                if await runner.check_artist(artist_dir):
                    await db.execute("UPDATE md_artists SET next_check = 0 "
                                     "WHERE artist_dir = ?", (artist_dir,))
            else:
                # no files left under it (merged away, trashed): nothing to fix
                await issues.save([], scope_kinds=issues.ARTIST_KINDS, artist_dir=artist_dir)
        except Exception:
            log.exception("artist check failed for %s", artist_dir)
    out["artists"] = len(artists)

    await runner.publish_issues()
    marks = ",".join("?" * len(checked))
    unit_ids = [r["unit_id"] for r in await db.fetchall(
        f"SELECT unit_id FROM md_albums WHERE key IN ({marks})", tuple(checked))] if checked else []
    bus.publish("library.changed", {"album_keys": checked, "unit_ids": unit_ids,
                                    "artist_dirs": artists})
    return out


async def _due(keys: list[str], what: str) -> None:
    for i in range(0, len(keys), 400):
        chunk = keys[i:i + 400]
        await db.execute(f"UPDATE md_albums SET {what} WHERE key IN ({','.join('?' * len(chunk))})",
                         tuple(chunk))


async def _albums_in(folders: list[str]) -> list[str]:
    """The album keys in or under these folders: the registry's, and the
    units on disk there now. Those whose files changed since their check
    come first."""
    known: dict[str, str | None] = {}
    dirs: set[str] = set()
    for folder in folders:
        span = (folder, folder + "/", folder + "0")
        for r in await db.fetchall("SELECT key, file_sig FROM md_albums WHERE dirpath = ? "
                                   "OR (dirpath >= ? AND dirpath < ?)", span):
            known[r["key"]] = r["file_sig"]
        dirs |= {units.unit_dir(r["dirpath"]) for r in await db.fetchall(
            "SELECT DISTINCT dirpath FROM library_index WHERE dirpath = ? "
            "OR (dirpath >= ? AND dirpath < ?)", span)}
    on_disk = units.build_units(await units.rows_for_folders(sorted(dirs)))
    keys = set(known) | set(on_disk)
    return sorted(keys, key=lambda k: (k in on_disk and known.get(k) == units.signature(on_disk[k]),
                                       k))
