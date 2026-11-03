"""Who is writing into an album's folder, and which albums are downloading.

The importer, the metadata manager's file fixes, Trash, Restore, an undo
and an artist merge all change files in album folders, and the library
scan reads them. Each writer holds the folders it changes (hold), one lock
per album folder (a disc subfolder is its album's), so two of them never
change one folder at once: an import waits for a move of its album to end,
and then follows the album (the job's folder moved with it). The scan
doesn't wait: what was held while it ran, it leaves for the next one.

An album a download is going into is "settling" (settling): the metadata
manager doesn't move, merge or re-tag it until the download ends (review
2026-11, §2)."""
from __future__ import annotations

import asyncio
import contextlib
from collections import Counter

from .db import db
from .identity import path_key, unit_dir
from .models import ACTIVE_JOB_STATUSES, TrackStatus

_locks: dict[str, asyncio.Lock] = {}
_owner: dict[str, asyncio.Task] = {}
_users: Counter = Counter()      # holders and waiters, so an unused lock is dropped
_let_go: dict[str, int] = {}     # folder -> the tick its last holder let go at
_tick = 0


def key(folder: str) -> str:
    """The lock a folder is under: its album folder's, in any spelling."""
    return path_key(unit_dir(folder or ""))


class Busy(Exception):
    """A folder another writer has, wanted out of turn (see hold)."""


@contextlib.asynccontextmanager
async def hold(*folders: str):
    """Hold these album folders (taken in one order, so two holders can't
    wait on each other). A task already holding one holds it again at once:
    a merge's Trash runs inside the merge.

    A task already holding folders that wants more waits only for those
    after them in that order. One before them it takes at once if no one
    has it or waits for it, else it raises Busy (and takes none): the one
    who has it may be waiting for a folder this task holds (an apply
    holding its album, filing downloads from a folder a Trash of both
    has)."""
    me = asyncio.current_task()
    mine = {k for k, t in _owner.items() if t is me}
    named = {key(f): f for f in folders if f}
    wanted = sorted(set(named) - mine)
    out_of_turn = [k for k in wanted if mine and k < max(mine) and _users[k]]
    if out_of_turn:
        raise Busy(f"{named[out_of_turn[0]]} is being changed by something else; "
                   f"try again once that is done")
    taken: list[str] = []
    try:
        for k in wanted:
            _users[k] += 1
            try:
                await _locks.setdefault(k, asyncio.Lock()).acquire()
            except BaseException:
                _drop(k)
                raise
            _owner[k] = me
            taken.append(k)
        yield
    finally:
        global _tick
        for k in reversed(taken):
            _tick += 1
            _let_go[k] = _tick
            del _owner[k]
            _locks[k].release()
            _drop(k)


def _drop(k: str) -> None:
    _users[k] -= 1
    if _users[k] <= 0:
        del _users[k]
        lock = _locks.get(k)
        if lock is not None and not lock.locked():
            del _locks[k]


def busy(folder: str) -> bool:
    """Whether another task holds this folder now."""
    owner = _owner.get(key(folder))
    return owner is not None and owner is not asyncio.current_task()


def tick() -> int:
    """Now, as touched_since counts."""
    return _tick


def touched_since(folder: str, since: int) -> bool:
    """Whether this folder is held now (by any task), or was let go after
    `since` (a tick()): what a scan read of it since may be half a change."""
    k = key(folder)
    return k in _owner or _let_go.get(k, -1) > since


# ------------------------------------------------------------------ settling
# a job's folder, and the files its tracks were imported to (looked up by
# job: a join reads every track, 40 ms on a copy of the live data against 2)
_SETTLING = (
    "SELECT target_dir AS path, 1 AS folder FROM jobs WHERE target_dir IS NOT NULL AND {jobs} "
    "UNION SELECT library_path, 0 FROM tracks WHERE job_id IN (SELECT id FROM jobs WHERE {jobs}) "
    "AND +status = ? AND library_path IS NOT NULL")


async def settling(job_id: str | None = None) -> dict[str, str]:
    """The album folders downloads are going into: each queued, paused or
    running job's folder (a Download missing's), and the folders of the
    tracks it has imported so far. {key(folder): album folder}. Read fresh
    each time: one small query. `job_id`: that job's, whatever its status
    (it just ended)."""
    if job_id is None:
        statuses = tuple(str(s) for s in ACTIVE_JOB_STATUSES)
        sql = _SETTLING.format(jobs=f"status IN ({','.join('?' * len(statuses))})")
        params = (*statuses, *statuses, str(TrackStatus.COMPLETED))
    else:
        sql = _SETTLING.format(jobs="id = ?")
        params = (job_id, job_id, str(TrackStatus.COMPLETED))
    out: dict[str, str] = {}
    for r in await db.fetchall(sql, params):
        folder = unit_dir(r["path"] if r["folder"] else r["path"].rpartition("/")[0])
        if folder:
            out.setdefault(key(folder), folder)
    return out


async def settling_into(*folders: str) -> str | None:
    """The first of these album folders a download is going into, as spelled
    here, or None."""
    wanted = [f for f in folders if f]
    if not wanted:
        return None
    now = await settling()
    return next((f for f in wanted if key(f) in now), None)
