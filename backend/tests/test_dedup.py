"""The same-album rule: a track is a duplicate only of itself on the same album.

Run with: python -m unittest discover backend/tests"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ownership
from app.db import Database
from app.importer import ImportError_
from app.library import album_identity
from app.models import JobStatus, TrackStatus


class AlbumIdentityTestCase(unittest.TestCase):
    def same(self, a, b, hints=()):
        return bool(album_identity(a, hints) & album_identity(b, hints))

    def test_folder_conventions_are_one_album(self):
        self.assertTrue(self.same("(2013) - Ariana Grande - Yours Truly", "Yours Truly",
                                  ["Ariana Grande"]))
        self.assertTrue(self.same("Yours Truly [FLAC 24bit]", "Yours Truly"))
        self.assertTrue(self.same("Speakerboxxx_The Love Below", "Speakerboxxx/The Love Below"))
        self.assertTrue(self.same("Yours Truly (Full Album)", "Yours Truly"))

    def test_a_remaster_is_the_album_and_an_edition_is_not(self):
        self.assertTrue(self.same("Abbey Road (Remastered 2009)", "Abbey Road"))
        self.assertTrue(self.same("Abbey Road - 2019 Remaster", "Abbey Road"))
        self.assertFalse(self.same("Seventy (Deluxe Edition)", "Seventy"))
        self.assertFalse(self.same("Leveler: 10th Anniversary Edition", "Leveler"))

    def test_nothing_is_not_an_album(self):
        self.assertEqual(album_identity(None), frozenset())
        self.assertEqual(album_identity(""), frozenset())


class DatabaseCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app import api, worker as worker_module
        self.api, self.worker_module = api, worker_module
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Database()
        patches = [
            mock.patch.object(api.settings, "data_dir", Path(self._tmp.name)),
            mock.patch.object(ownership, "db", self.db),
            mock.patch.object(api, "db", self.db),
            mock.patch.object(worker_module, "db", self.db),
            mock.patch.object(worker_module.bus, "publish"),
            mock.patch.object(api.bus, "publish"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)

    async def library(self, *rows):
        """(norm_artist, norm_title, path, provider_id, duration)"""
        await self.db.executemany(
            "INSERT INTO library_index (norm_artist, norm_title, path, dirpath, "
            "provider_track_id, duration_sec, added_at) VALUES (?,?,?,?,?,?,?)",
            [(a, t, p, p.rsplit("/", 1)[0], pid, d, "t") for a, t, p, pid, d in rows])


class GateTestCase(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app.library_index import find_duplicate
        self.find = find_duplicate
        await self.library(
            ("paul kelly", "rita wrote a letter", "PK/Seventy/03.m4a", "vid1", 255),
            ("paul kelly", "to her door", "PK/Songs From The South/02.m4a", None, 200))

    async def test_the_same_track_on_the_same_album_is_skipped(self):
        self.assertEqual(await self.find("vid9", "Paul Kelly", "Rita Wrote A Letter",
                                         "Seventy", 255), "PK/Seventy/03.m4a")

    async def test_the_same_song_on_another_album_is_not(self):
        # The single, the compilation's song on the studio album: each downloads.
        self.assertIsNone(await self.find("vid1", "Paul Kelly", "Rita Wrote A Letter",
                                          "Rita Wrote A Letter", 255))
        self.assertIsNone(await self.find(None, "Paul Kelly", "To Her Door",
                                          "Under The Sun", 200))

    async def test_without_an_album_only_the_same_video_counts(self):
        self.assertEqual(await self.find("vid1", "Paul Kelly", "Rita Wrote A Letter"),
                         "PK/Seventy/03.m4a")
        self.assertIsNone(await self.find("other", "Paul Kelly", "To Her Door"))

    async def test_an_old_youtube_title_in_the_album_folder_is_the_track(self):
        await self.library(("hilltop hoods", "hilltop hoods parade of the dead",
                            "HH/State Of The Art/07.opus", None, 215))
        self.assertEqual(
            await self.find(None, "Hilltop Hoods", "Parade Of The Dead", "State Of The Art", 214),
            "HH/State Of The Art/07.opus")


class CollisionTestCase(DatabaseCase):
    async def test_a_file_already_at_the_destination_is_a_skip_not_a_failure(self):
        """Otherwise maintenance re-queues import_failed tracks every five
        minutes, and the track downloads again forever."""
        worker = self.worker_module.Worker()
        with mock.patch.object(worker, "wake"):
            job_id = await worker.enqueue("https://music.youtube.com/browse/MPREb_x")
        await self.db.execute(
            "INSERT INTO tracks (id, job_id, status, created_at) VALUES (?,?,?,?)",
            ("t1", job_id, TrackStatus.IMPORTING, "t"))
        track = await self.db.fetchone("SELECT * FROM tracks WHERE id = 't1'")
        await worker._handle_import_failure(track, ImportError_(
            "dest_exists", "file already exists", path="PK/Seventy/03.m4a"))
        track = await self.db.fetchone("SELECT * FROM tracks WHERE id = 't1'")
        self.assertEqual((track["status"], track["library_path"]),
                         (TrackStatus.SKIPPED_DUPLICATE, "PK/Seventy/03.m4a"))


class RecheckTestCase(DatabaseCase):
    async def add_job(self, job_id, status, position):
        await self.db.execute(
            "INSERT INTO jobs (id, source_url, provider, source_type, status, "
            "queue_position, created_at) VALUES (?,?,?,?,?,?,?)",
            (job_id, "u", "youtube_music", "album", status, position, f"2026-0{position}"))

    async def skip(self, track_id, job_id, album, matched, acked=1):
        await self.db.execute(
            "INSERT INTO tracks (id, job_id, status, album, library_path, review_acked, "
            "created_at) VALUES (?,?,?,?,?,?,?)",
            (track_id, job_id, TrackStatus.SKIPPED_DUPLICATE, album, matched, acked, "t"))

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.add_job("done", JobStatus.COMPLETED, 1)
        await self.add_job("live", JobStatus.DOWNLOADING, 2)
        await self.add_job("gone", JobStatus.CANCELLED, 3)
        # skipped for a copy on a compilation: wrong under the album rule
        await self.skip("a", "done", "Under The Sun", "PK/Songs From The South/02.m4a")
        # skipped for the same album: still right
        await self.skip("b", "done", "Seventy", "PK/Seventy/03.m4a")
        await self.skip("c", "live", "Gossip", "PK/Time/09.m4a", acked=0)
        await self.skip("d", "gone", "Gossip", "PK/Time/10.m4a")

    async def test_preview_counts_reviewed_skips_too(self):
        self.assertEqual(await self.api.skipped_recheck_preview(), {"tracks": 2, "jobs": 2})

    async def test_recheck_requeues_cross_album_skips_only(self):
        with mock.patch.object(self.api.worker, "wake"):
            result = await self.api.skipped_recheck()
        self.assertEqual(result, {"requeued": 2, "jobs": 2})
        status = {r["id"]: r["status"] for r in await self.db.fetchall("SELECT * FROM tracks")}
        self.assertEqual(status, {"a": TrackStatus.PENDING, "b": TrackStatus.SKIPPED_DUPLICATE,
                                  "c": TrackStatus.PENDING, "d": TrackStatus.SKIPPED_DUPLICATE})
        jobs = {r["id"]: r for r in await self.db.fetchall("SELECT * FROM jobs")}
        # The finished job reopens at the back of the queue; the live one keeps its place.
        self.assertEqual(jobs["done"]["status"], JobStatus.DOWNLOADING)
        self.assertEqual(jobs["done"]["queue_position"], 4)
        self.assertEqual(jobs["live"]["queue_position"], 2)
        self.assertEqual(jobs["gone"]["status"], JobStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
