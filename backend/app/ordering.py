"""Download queue ordering.

Jobs used to run in strict creation order, so the only way to get something
downloaded sooner was to wait for everything ahead of it. `queue_position`
makes the order explicit and editable, the way a torrent client's queue is.

Positions are kept dense (1..N) over the *active* jobs and renumbered after
every change. The queue is tens of rows, so renumbering costs nothing and
sidesteps the drift that sparse gap-based schemes accumulate.

One thing the UI has to be honest about: the governor runs a single download
at a time and a track in flight is never preempted. Moving a job to the top
means it goes next, not that it interrupts the track currently downloading.
"""
import logging

from .db import db
from .models import ACTIVE_JOB_STATUSES, JobStatus, TrackStatus, row_to_job

log = logging.getLogger("mdl.ordering")

_ACTIVE = ",".join("?" * len(ACTIVE_JOB_STATUSES))


class QueueError(Exception):
    pass


async def active_jobs() -> list:
    return await db.fetchall(
        f"SELECT * FROM jobs WHERE status IN ({_ACTIVE}) "
        "ORDER BY queue_position, created_at", tuple(ACTIVE_JOB_STATUSES))


async def _renumber(ordered_ids: list[str]) -> None:
    for position, job_id in enumerate(ordered_ids, start=1):
        await db.execute("UPDATE jobs SET queue_position = ? WHERE id = ?",
                         (position, job_id))


async def move(job_id: str, action: str | None = None,
               to: int | None = None) -> list[str]:
    """Move one job within the queue. `action` is top/up/down/bottom;
    `to` is a 1-based absolute position."""
    jobs = await active_jobs()
    ids = [j["id"] for j in jobs]
    if job_id not in ids:
        raise QueueError("that job is not in the queue any more")
    index = ids.index(job_id)
    if to is not None:
        target = max(0, min(len(ids) - 1, to - 1))
    elif action == "top":
        target = 0
    elif action == "bottom":
        target = len(ids) - 1
    elif action == "up":
        target = max(0, index - 1)
    elif action == "down":
        target = min(len(ids) - 1, index + 1)
    else:
        raise QueueError("action must be top | up | down | bottom, or pass a position")
    ids.insert(target, ids.pop(index))
    await _renumber(ids)
    return ids


async def reorder(job_ids: list[str]) -> list[str]:
    """Apply a full ordering (a drag-and-drop drop). Jobs the client did not
    list — one may have finished while the list was open — keep their relative
    order behind the ones it did."""
    jobs = await active_jobs()
    known = {j["id"] for j in jobs}
    unknown = [i for i in job_ids if i not in known]
    if unknown:
        raise QueueError(f"not in the queue: {', '.join(unknown[:3])}")
    listed = [i for i in job_ids]
    rest = [j["id"] for j in jobs if j["id"] not in set(listed)]
    ids = listed + rest
    await _renumber(ids)
    return ids


async def set_paused(job_id: str, paused: bool) -> str:
    """Pause or resume a job.

    Pausing is not cancelling: the job keeps its place in the queue and its
    tracks stay PENDING, the worker simply steps over it. A track already
    downloading finishes — the governor's single slot is not interruptible —
    so the pause takes effect from the next track."""
    job = await db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not job:
        raise QueueError("job not found")
    if paused:
        if job["status"] not in (JobStatus.QUEUED, JobStatus.DOWNLOADING,
                                 JobStatus.RESOLVING):
            raise QueueError(f"a {job['status']} job cannot be paused")
        await db.execute("UPDATE jobs SET status = ? WHERE id = ?",
                         (JobStatus.PAUSED, job_id))
        return JobStatus.PAUSED
    if job["status"] != JobStatus.PAUSED:
        raise QueueError("that job is not paused")
    # Back to whichever phase it was in: a job with tracks is mid-download,
    # one without has not been expanded yet.
    row = await db.fetchone("SELECT COUNT(*) AS n FROM tracks WHERE job_id = ?", (job_id,))
    status = JobStatus.DOWNLOADING if row["n"] else JobStatus.QUEUED
    await db.execute("UPDATE jobs SET status = ?, finished_at = NULL WHERE id = ?",
                     (status, job_id))
    return status


async def snapshot(current_track_id: str | None = None) -> dict:
    """The queue as the UI draws it: ordered jobs, each with what it is
    waiting on, plus the track actually running right now."""
    jobs = await active_jobs()
    pending = {r["job_id"]: r["n"] for r in await db.fetchall(
        "SELECT job_id, COUNT(*) AS n FROM tracks WHERE status = ? GROUP BY job_id",
        (TrackStatus.PENDING,))}

    items = []
    for position, job in enumerate(jobs, start=1):
        out = row_to_job(job).model_dump(exclude={"tracks"})
        out["position"] = position
        out["pending_tracks"] = pending.get(job["id"], 0)
        items.append(out)

    current = None
    if current_track_id:
        row = await db.fetchone("SELECT * FROM tracks WHERE id = ?", (current_track_id,))
        if row:
            from .models import row_to_track
            current = row_to_track(row).model_dump()
    return {"items": items, "current_track": current,
            "queued_tracks": sum(pending.values())}
