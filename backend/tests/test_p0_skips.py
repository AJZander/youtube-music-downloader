"""Skipped downloads: every skip says why, and the Skipped list's "different
album" label and Re-check banner tell the truth.

Run with: python -m unittest discover backend/tests"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.importer import ImportError_
from app.models import JobStatus, TrackStatus


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    """A scratch database per test, behind the app's own `db`."""

    async def asyncSetUp(self):
        from app import api, worker as worker_module
        from app.config import settings
        from app.db import db
        self.api, self.worker_module = api, worker_module
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(settings, "data_dir", self.tmp / "data"),
                        mock.patch.object(settings, "library_dir", self.tmp / "library"),
                        mock.patch.object(settings, "plex_token", ""),
                        mock.patch.object(worker_module.bus, "publish"),
                        mock.patch.object(api.bus, "publish")]
        for p in self.patches:
            p.start()
        await db.connect()
        self.db = db

    async def asyncTearDown(self):
        await self.db.close()
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def add_job(self, job_id="j1", status=JobStatus.DOWNLOADING, position=1,
                      target_dir=None):
        await self.db.execute(
            "INSERT INTO jobs (id, source_url, provider, source_type, status, "
            "queue_position, target_dir, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (job_id, "u", "youtube_music", "album", status, position, target_dir,
             f"2026-0{position}"))

    async def library(self, *rows):
        """(norm_artist, norm_title, path, provider_id, duration)"""
        await self.db.executemany(
            "INSERT INTO library_index (norm_artist, norm_title, path, dirpath, "
            "provider_track_id, duration_sec, added_at) VALUES (?,?,?,?,?,?,?)",
            [(a, t, p, p.rsplit("/", 1)[0], pid, d, "t") for a, t, p, pid, d in rows])

    async def add_track(self, track_id, *, artist, title, album, video_id=None,
                        duration=None, job_id="j1", status=TrackStatus.PENDING,
                        error_code=None, error_message=None):
        await self.db.execute(
            "INSERT INTO tracks (id, job_id, position, provider, provider_track_id, "
            "artist, album, title, expected_duration_sec, status, error_code, "
            "error_message, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (track_id, job_id, 1, "youtube_music", video_id, artist, album, title,
             duration, status, error_code, error_message, "t"))
        return await self.track(track_id)

    async def track(self, track_id):
        return await self.db.fetchone("SELECT * FROM tracks WHERE id = ?", (track_id,))


# ------------------------------------------------------------------ P0-5
class SkipReasonTestCase(DbTestCase):
    """A skip used to write no error_code and no message: 2,598 of 2,599 skips
    said nothing about what they matched or how."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.worker = self.worker_module.Worker()
        await self.add_job()
        await self.library(
            ("paul kelly", "rita wrote a letter", "PK/Seventy/03.m4a", "vid1", 255),
            ("hilltop hoods", "hilltop hoods parade of the dead",
             "HH/State Of The Art/07.opus", None, 215))

    async def gate(self, track):
        """The real worker pipeline, up to the dedup gate's skip."""
        await self.worker._process_track(track)
        return await self.track(track["id"])

    async def test_a_gate_skip_records_its_reason(self):
        # Same artist, title and album, but 45 s longer: the gate still says
        # "have" (P1-B3 changes that). Now it at least says so.
        track = await self.add_track("t1", artist="Paul Kelly", title="Rita Wrote A Letter",
                                     album="Seventy", video_id="vid9", duration=300)
        track = await self.gate(track)
        self.assertEqual(track["status"], TrackStatus.SKIPPED_DUPLICATE)
        self.assertEqual(track["library_path"], "PK/Seventy/03.m4a")
        self.assertEqual(track["error_code"], "held_same_album")
        message = track["error_message"] or ""
        self.assertIn("PK/Seventy/03.m4a", message)
        self.assertIn("exact title", message)
        self.assertIn("version or length differs", message)

        listed = await self.api.list_skipped(limit=100, offset=0, only_different_album=False)
        self.assertEqual([(t["id"], t["error_code"], t["error_message"])
                          for t in listed["items"]],
                         [("t1", "held_same_album", message)])

    async def test_the_match_type_is_named(self):
        video = await self.gate(await self.add_track(
            "t1", artist="Paul Kelly", title="Rita Wrote A Letter", album="Seventy",
            video_id="vid1", duration=255))
        fuzzy = await self.gate(await self.add_track(
            "t2", artist="Hilltop Hoods", title="Parade Of The Dead",
            album="State Of The Art", duration=214))
        self.assertEqual((video["status"], video["error_code"]),
                         (TrackStatus.SKIPPED_DUPLICATE, "held_same_album"))
        self.assertEqual(video["error_message"],
                         "Already in the library: PK/Seventy/03.m4a (matched by video)")
        self.assertEqual((fuzzy["status"], fuzzy["error_code"]),
                         (TrackStatus.SKIPPED_DUPLICATE, "held_same_album"))
        self.assertEqual(fuzzy["error_message"],
                         "Already in the library: HH/State Of The Art/07.opus (matched by fuzzy)")

    async def test_dest_exists_records_its_reason(self):
        track = await self.add_track("t1", artist="Paul Kelly", title="Rita Wrote A Letter",
                                     album="Seventy", status=TrackStatus.IMPORTING)
        await self.worker._handle_import_failure(track, ImportError_(
            "dest_exists", "file already exists", path="PK/Seventy/03.m4a"))
        track = await self.track("t1")
        self.assertEqual((track["status"], track["library_path"], track["error_code"]),
                         (TrackStatus.SKIPPED_DUPLICATE, "PK/Seventy/03.m4a", "dest_exists"))
        self.assertIn("PK/Seventy/03.m4a", track["error_message"] or "")

    async def test_a_stale_error_code_is_replaced(self):
        # A retry after a rate limit leaves its code on the row; the skip that
        # follows must not wear it.
        track = await self.add_track("t1", artist="Paul Kelly", title="Rita Wrote A Letter",
                                     album="Seventy", video_id="vid1", duration=255,
                                     error_code="rate_limited", error_message="HTTP Error 429")
        track = await self.gate(track)
        self.assertEqual((track["status"], track["error_code"]),
                         (TrackStatus.SKIPPED_DUPLICATE, "held_same_album"))
        self.assertNotIn("429", track["error_message"] or "")

        stale = await self.add_track("t2", artist="Paul Kelly", title="To Her Door",
                                     album="Seventy", status=TrackStatus.IMPORTING,
                                     error_code="import_failed", error_message="mount gone")
        await self.worker._handle_import_failure(stale, ImportError_(
            "dest_exists", "file already exists", path="PK/Seventy/05.m4a"))
        stale = await self.track("t2")
        self.assertEqual(stale["error_code"], "dest_exists")
        self.assertNotIn("mount gone", stale["error_message"] or "")


if __name__ == "__main__":
    unittest.main()
