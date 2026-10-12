"""Run with: python -m unittest discover backend/tests"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ordering
from app.models import JobStatus, TrackStatus


class FakeDB:
    """Just enough of the SQL surface ordering.py uses, backed by dicts."""

    def __init__(self, jobs, tracks=None):
        self.jobs = {j["id"]: dict(j) for j in jobs}
        self.tracks = list(tracks or [])

    async def fetchall(self, sql, params=()):
        if "FROM jobs" in sql:
            wanted = set(params)
            rows = [j for j in self.jobs.values() if j["status"] in wanted]
            return sorted(rows, key=lambda j: (j["queue_position"], j["created_at"]))
        if "GROUP BY job_id" in sql:
            counts = {}
            for t in self.tracks:
                if t["status"] == params[0]:
                    counts[t["job_id"]] = counts.get(t["job_id"], 0) + 1
            return [{"job_id": k, "n": v} for k, v in counts.items()]
        return []

    async def fetchone(self, sql, params=()):
        if "COUNT(*) AS n FROM tracks" in sql:
            return {"n": sum(1 for t in self.tracks if t["job_id"] == params[0])}
        if "FROM jobs WHERE id" in sql:
            return self.jobs.get(params[0])
        return None

    async def execute(self, sql, params=()):
        if "SET queue_position" in sql:
            self.jobs[params[1]]["queue_position"] = params[0]
        elif "SET status = ?, finished_at = NULL" in sql:
            self.jobs[params[1]]["status"] = params[0]
        elif "SET status" in sql:
            self.jobs[params[1]]["status"] = params[0]

    def order(self):
        return [j["id"] for j in sorted(self.jobs.values(),
                                        key=lambda j: j["queue_position"])]


def job(job_id, position, status=JobStatus.QUEUED):
    return {"id": job_id, "queue_position": position, "status": status,
            "created_at": f"2026-01-0{position}T00:00:00Z", "source_url": "u",
            "provider": "youtube", "source_type": "track", "title": None,
            "artist": None, "force": 0, "error_code": None, "error_message": None,
            "total_tracks": 0, "completed_tracks": 0, "failed_tracks": 0,
            "skipped_tracks": 0, "started_at": None, "finished_at": None}


class MoveTestCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB([job("a", 1), job("b", 2), job("c", 3), job("d", 4)])
        patcher = mock.patch.object(ordering, "db", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def move(self, job_id, action=None, to=None):
        return asyncio.run(ordering.move(job_id, action=action, to=to))

    def test_move_up_swaps_with_the_job_above(self):
        self.assertEqual(self.move("c", "up"), ["a", "c", "b", "d"])

    def test_move_to_top(self):
        self.assertEqual(self.move("d", "top"), ["d", "a", "b", "c"])

    def test_move_to_bottom(self):
        self.assertEqual(self.move("a", "bottom"), ["b", "c", "d", "a"])

    def test_move_down(self):
        self.assertEqual(self.move("a", "down"), ["b", "a", "c", "d"])

    def test_moving_the_first_job_up_is_a_no_op(self):
        self.assertEqual(self.move("a", "up"), ["a", "b", "c", "d"])

    def test_moving_the_last_job_down_is_a_no_op(self):
        self.assertEqual(self.move("d", "down"), ["a", "b", "c", "d"])

    def test_absolute_position(self):
        self.assertEqual(self.move("a", to=3), ["b", "c", "a", "d"])

    def test_out_of_range_position_clamps(self):
        self.assertEqual(self.move("a", to=99), ["b", "c", "d", "a"])

    def test_positions_are_renumbered_densely(self):
        self.move("d", "top")
        self.assertEqual([self.db.jobs[i]["queue_position"] for i in self.db.order()],
                         [1, 2, 3, 4])

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(ordering.QueueError):
            self.move("a", "sideways")

    def test_finished_jobs_are_not_in_the_queue(self):
        self.db.jobs["b"]["status"] = JobStatus.COMPLETED
        self.assertEqual(self.move("d", "top"), ["d", "a", "c"])

    def test_moving_a_finished_job_is_rejected(self):
        self.db.jobs["b"]["status"] = JobStatus.COMPLETED
        with self.assertRaises(ordering.QueueError):
            self.move("b", "top")


class ReorderTestCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB([job("a", 1), job("b", 2), job("c", 3)])
        patcher = mock.patch.object(ordering, "db", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_applies_a_whole_ordering(self):
        self.assertEqual(asyncio.run(ordering.reorder(["c", "a", "b"])),
                         ["c", "a", "b"])

    def test_jobs_the_client_left_out_keep_their_order_behind(self):
        """A job can finish between the page rendering and the drop landing."""
        self.assertEqual(asyncio.run(ordering.reorder(["c"])), ["c", "a", "b"])

    def test_rejects_ids_that_are_not_queued(self):
        with self.assertRaises(ordering.QueueError):
            asyncio.run(ordering.reorder(["a", "nope"]))


class PauseTestCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB(
            [job("a", 1, JobStatus.DOWNLOADING), job("b", 2, JobStatus.QUEUED),
             job("c", 3, JobStatus.COMPLETED)],
            tracks=[{"job_id": "a", "status": TrackStatus.PENDING}])
        patcher = mock.patch.object(ordering, "db", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pause_keeps_the_job_in_the_queue(self):
        asyncio.run(ordering.set_paused("a", True))
        self.assertEqual(self.db.jobs["a"]["status"], JobStatus.PAUSED)
        order = asyncio.run(ordering.active_jobs())
        self.assertIn("a", [j["id"] for j in order])

    def test_resume_returns_an_expanded_job_to_downloading(self):
        asyncio.run(ordering.set_paused("a", True))
        self.assertEqual(asyncio.run(ordering.set_paused("a", False)),
                         JobStatus.DOWNLOADING)

    def test_resume_returns_an_unexpanded_job_to_queued(self):
        asyncio.run(ordering.set_paused("b", True))
        self.assertEqual(asyncio.run(ordering.set_paused("b", False)),
                         JobStatus.QUEUED)

    def test_a_finished_job_cannot_be_paused(self):
        with self.assertRaises(ordering.QueueError):
            asyncio.run(ordering.set_paused("c", True))

    def test_resuming_a_job_that_is_not_paused_is_rejected(self):
        with self.assertRaises(ordering.QueueError):
            asyncio.run(ordering.set_paused("b", False))


class SnapshotTestCase(unittest.TestCase):
    def test_reports_positions_and_pending_counts(self):
        db = FakeDB([job("a", 1), job("b", 2)],
                    tracks=[{"job_id": "a", "status": TrackStatus.PENDING},
                            {"job_id": "a", "status": TrackStatus.PENDING},
                            {"job_id": "b", "status": TrackStatus.COMPLETED}])
        with mock.patch.object(ordering, "db", db):
            snap = asyncio.run(ordering.snapshot())
        self.assertEqual([i["position"] for i in snap["items"]], [1, 2])
        self.assertEqual(snap["items"][0]["pending_tracks"], 2)
        self.assertEqual(snap["queued_tracks"], 2)


if __name__ == "__main__":
    unittest.main()
