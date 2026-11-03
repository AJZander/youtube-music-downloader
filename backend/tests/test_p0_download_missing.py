"""Download missing fetches a track once: what an earlier download of the
release delivered or skipped isn't queued again unless asked (Retry), and
"Download missing all" takes only the albums never tried.

Run with: python -m unittest discover backend/tests"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_metadata import release

ALBUM = "Ay/(2001) Bee"
BROWSE = "MPREb_bee2001"
SONGS = [("One", 200.0), ("Two", 210.0), ("Three", 220.0), ("Four", 230.0)]
# YouTube Music's album: v1 … v4, in the release's order
YTM_ALBUM = {"title": "Bee", "artists": ["Ay"], "tracks": [
    {"video_id": f"v{i}", "title": t, "track_number": i, "duration_sec": d, "available": True}
    for i, (t, d) in enumerate(SONGS, start=1)]}


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    """A scratch database per test."""

    async def asyncSetUp(self):
        from app.config import settings
        from app.db import db
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(settings, "data_dir", self.tmp / "data"),
                        mock.patch.object(settings, "library_dir", self.tmp / "library"),
                        mock.patch.object(settings, "plex_token", "")]
        for p in self.patches:
            p.start()
        await db.connect()
        self.db = db

    async def asyncTearDown(self):
        await self.db.close()
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class QueueOnceTestCase(DbTestCase):
    """The album holds tracks 1 and 2 of four; 3 and 4 are missing."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app import catalog
        from app.metadata import missing
        from app.worker import worker
        desired = {"album": "Bee", "albumartist": "Ay", "original_year": "2001", "tracks": {
            f"{ALBUM}/0{i} - {t}.m4a": {"matched": True, "releasetrack_mbid": f"t{i}"}
            for i, (t, _) in enumerate(SONGS[:2], start=1)}}
        await self.db.execute(
            "INSERT INTO md_albums (key, dirpath, artist_dir, status, release_mbid, desired) "
            "VALUES (?, ?, 'Ay', 'resolved', 'rel-1', ?)", (ALBUM, ALBUM, json.dumps(desired)))
        # only MusicBrainz's release and the YouTube Music album are faked;
        # the job goes through the real queue
        fakes = [mock.patch.object(missing.mb, "release", mock.AsyncMock(
                     return_value=release(title="Bee", artist="Ay", tracks=SONGS))),
                 mock.patch.object(missing, "_browse_from_history",
                                   mock.AsyncMock(return_value=BROWSE)),
                 mock.patch.object(catalog, "album", mock.AsyncMock(return_value=YTM_ALBUM)),
                 mock.patch.object(worker, "wake")]
        for p in fakes:
            p.start()
            self.addCleanup(p.stop)
        self.missing = missing
        await missing.refresh("rel-1")

    async def _first_job(self, outcomes: dict[int, str]) -> str:
        """Queue the missing tracks, then play the worker: each track ends as
        `outcomes` says, with the id the album-audio playlist rewrote it to."""
        first = await self.missing.queue_download("rel-1")
        self.assertEqual(first["queued"], 2)
        job = first["job_id"]
        self.assertEqual(json.loads((await self.db.fetchone(
            "SELECT track_filter FROM jobs WHERE id = ?", (job,)))["track_filter"]), ["v3", "v4"])
        for n, status in outcomes.items():
            await self.db.execute(
                "INSERT INTO tracks (id, job_id, position, provider_track_id, title, track_number, "
                "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, '')",
                (f"{job}-{n}", job, n - 2, f"audio{n}", SONGS[n - 1][0], n, status))
        failed = "failed" in outcomes.values()
        await self.db.execute("UPDATE jobs SET status = ? WHERE id = ?",
                              ("partial" if failed else "completed", job))
        return job

    async def _jobs(self) -> int:
        return (await self.db.fetchone("SELECT COUNT(*) AS n FROM jobs"))["n"]

    async def _download(self) -> tuple[dict, str | None]:
        row = await self.db.fetchone("SELECT detail, error FROM md_issues "
                                     "WHERE key = 'missing_tracks:rel-1'")
        return json.loads(row["detail"])["download"], row["error"]

    async def test_a_second_queue_after_a_fully_skipped_job_queues_nothing(self):
        j1 = await self._first_job({3: "skipped_duplicate", 4: "skipped_duplicate"})
        again = await self.missing.queue_download("rel-1")
        self.assertEqual(again["queued"], 0)
        self.assertEqual(again["message"], "already tried")
        self.assertEqual(again["already_tried"], 2)
        self.assertEqual(await self._jobs(), 1)
        download, error = await self._download()
        self.assertEqual([a["job_id"] for a in download["attempts"]], [j1])
        self.assertTrue(error.startswith("already tried"))
        # and it is in the Tried bucket (P1-A1)
        state = (await self.db.fetchone("SELECT state FROM md_issues "
                                        "WHERE key = 'missing_tracks:rel-1'"))["state"]
        self.assertEqual(state, "tried")

    async def test_force_queues_them_again(self):
        j1 = await self._first_job({3: "skipped_duplicate", 4: "completed"})
        again = await self.missing.queue_download("rel-1", force=True)
        self.assertEqual(again["queued"], 2)
        self.assertNotEqual(again["job_id"], j1)
        self.assertEqual(await self._jobs(), 2)
        download, _ = await self._download()
        self.assertEqual([a["job_id"] for a in download["attempts"]], [j1, again["job_id"]])
        self.assertEqual(download["job_id"], again["job_id"])

    async def test_only_failed_tracks_are_tried_again(self):
        await self._first_job({3: "skipped_duplicate", 4: "failed"})
        again = await self.missing.queue_download("rel-1")
        self.assertEqual(again["queued"], 1)
        row = await self.db.fetchone("SELECT track_filter FROM jobs WHERE id = ?",
                                     (again["job_id"],))
        self.assertEqual(json.loads(row["track_filter"]), ["v4"])

    async def test_a_download_from_before_attempts_were_listed_counts(self):
        """The issue only kept the job id, and was asked for again while
        YouTube Music was busy: the earlier job counts, and the answer stops
        the next pass asking again."""
        await self._old_job()
        row = await self.db.fetchone("SELECT id, detail FROM md_issues")
        detail = {**json.loads(row["detail"]), "download": {
            "job_id": "j0", "queued_at": "2026-10-21T06:55:50Z", "active": False,
            "wanted": True, "wanted_at": "2026-10-22T01:00:00Z"}}
        await self.db.execute("UPDATE md_issues SET detail = ? WHERE id = ?",
                              (json.dumps(detail), row["id"]))
        self.assertEqual(await self.missing.wanted_downloads(), [row["id"]])
        again = await self.missing.queue_download("rel-1")
        self.assertEqual(again["message"], "already tried")
        self.assertEqual(await self._jobs(), 1)
        download, _ = await self._download()
        self.assertEqual(download["attempts"], [{"job_id": "j0", "queued_at": "2026-10-21T06:55:50Z",
                                                 "videos": ["v3", "v4"]}])
        self.assertNotIn("wanted", download)
        self.assertEqual(await self.missing.wanted_downloads(), [])

    async def test_a_job_the_issue_lost_track_of_counts(self):
        """A second download replaced the first's record, or none was kept: a
        job from the same album into an album folder still counts, and the
        answer takes the issue out of "Download missing all"."""
        from app.metadata import views
        await self._old_job()

        async def untried():
            return (await views.issues_for(kinds=["missing_tracks"], filter_="untried"))[0]
        self.assertEqual(await untried(), 1)
        again = await self.missing.queue_download("rel-1")
        self.assertEqual(again["message"], "already tried")
        download, _ = await self._download()
        self.assertEqual([a["job_id"] for a in download["attempts"]], ["j0"])
        self.assertEqual(await untried(), 0)

    async def _old_job(self):
        """j0: a download of v3 and v4 from before, both skipped as held."""
        await self.db.execute(
            "INSERT INTO jobs (id, source_url, provider, source_type, status, created_at, "
            "track_filter, target_dir) VALUES ('j0', ?, 'youtube_music', 'album', 'completed', "
            "'2026-10-21T06:55:50Z', '[\"v3\", \"v4\"]', ?)",
            (f"https://music.youtube.com/browse/{BROWSE}", ALBUM))
        for n in (3, 4):
            await self.db.execute(
                "INSERT INTO tracks (id, job_id, provider_track_id, title, track_number, status, "
                "created_at) VALUES (?, 'j0', ?, ?, ?, 'skipped_duplicate', '')",
                (f"j0-{n}", f"audio{n}", SONGS[n - 1][0], n))

    async def test_attempts_survive_a_recheck(self):
        j1 = await self._first_job({3: "completed", 4: "skipped_duplicate"})
        await self.missing.refresh("rel-1")
        download, _ = await self._download()
        self.assertEqual([a["job_id"] for a in download["attempts"]], [j1])
        self.assertEqual(download["attempts"][0]["videos"], ["v3", "v4"])
        self.assertFalse(download["active"])

    async def test_already_tried_says_the_download_has_finished(self):
        """Nothing was imported, so no check ran since the job ended: the
        answer still says the download is over, and the album page offers
        Retry instead of "download queued"."""
        await self._first_job({3: "skipped_duplicate", 4: "skipped_duplicate"})
        again = await self.missing.queue_download("rel-1")
        self.assertEqual(again["message"], "already tried")
        download, _ = await self._download()
        self.assertIs(download["active"], False)
        summary = (await self.db.fetchone("SELECT summary FROM md_issues "
                                          "WHERE key = 'missing_tracks:rel-1'"))["summary"]
        self.assertEqual(summary, "2 of 4 tracks missing")

    async def test_a_job_removed_from_the_history_still_counts(self):
        """The Downloads page's "Remove from history" takes the job and its
        tracks: what the issue lists as asked for counts as tried."""
        j1 = await self._first_job({3: "skipped_duplicate", 4: "skipped_duplicate"})
        await self.db.execute("DELETE FROM jobs WHERE id = ?", (j1,))
        again = await self.missing.queue_download("rel-1")
        self.assertEqual(again["queued"], 0)
        self.assertEqual(again["message"], "already tried")
        self.assertEqual(await self._jobs(), 0)

    async def test_a_later_failure_outweighs_a_removed_job(self):
        """j1 (removed since) asked for v3 and v4; a later retry skipped v3
        and failed v4: v4 is fetched again, v3 isn't."""
        j1 = await self._first_job({3: "skipped_duplicate", 4: "skipped_duplicate"})
        await self.db.execute("DELETE FROM jobs WHERE id = ?", (j1,))
        j2 = (await self.missing.queue_download("rel-1", force=True))["job_id"]
        for n, status in ((3, "skipped_duplicate"), (4, "failed")):
            await self.db.execute(
                "INSERT INTO tracks (id, job_id, provider_track_id, title, track_number, status, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, '')",
                (f"j2-{n}", j2, f"audio{n}", SONGS[n - 1][0], n, status))
        await self.db.execute("UPDATE jobs SET status = 'partial' WHERE id = ?", (j2,))
        again = await self.missing.queue_download("rel-1")
        self.assertEqual(again["queued"], 1)
        row = await self.db.fetchone("SELECT track_filter FROM jobs WHERE id = ?",
                                     (again["job_id"],))
        self.assertEqual(json.loads(row["track_filter"]), ["v4"])

    async def _still_downloading(self) -> str:
        """j1 [v3, v4] is downloading: v3 came in and the album's check
        filed it, so only v4 is missing; v4 is still to come."""
        j1 = (await self.missing.queue_download("rel-1"))["job_id"]
        for n, status in ((3, "completed"), (4, "pending")):
            await self.db.execute(
                "INSERT INTO tracks (id, job_id, provider_track_id, title, track_number, status, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, '')",
                (f"{j1}-{n}", j1, f"audio{n}", SONGS[n - 1][0], n, status))
        await self.db.execute("UPDATE jobs SET status = 'downloading' WHERE id = ?", (j1,))
        row = await self.db.fetchone("SELECT desired FROM md_albums WHERE key = ?", (ALBUM,))
        desired = json.loads(row["desired"])
        desired["tracks"][f"{ALBUM}/03 - Three.m4a"] = {"matched": True, "releasetrack_mbid": "t3"}
        await self.db.execute("UPDATE md_albums SET desired = ? WHERE key = ?",
                              (json.dumps(desired), ALBUM))
        return j1

    async def test_a_track_a_running_download_still_has_isnt_queued_again(self):
        j1 = await self._still_downloading()
        for force in (False, True):
            again = await self.missing.queue_download("rel-1", force=force)
            self.assertTrue(again["already_queued"])
            self.assertEqual(again["job_id"], j1)
            self.assertEqual(await self._jobs(), 1)
        download, _ = await self._download()
        self.assertEqual(download["job_id"], j1)
        self.assertTrue(download["active"])

    async def test_only_what_no_running_download_has_is_queued(self):
        """A Download missing job from the same album holds v3: only v4 is
        queued."""
        await self.db.execute(
            "INSERT INTO jobs (id, source_url, provider, source_type, status, created_at, "
            "track_filter, target_dir) VALUES ('j0', ?, 'youtube_music', 'album', 'queued', "
            "'2026-11-01T00:00:00Z', '[\"v3\"]', ?)",
            (f"https://music.youtube.com/browse/{BROWSE}", ALBUM))
        again = await self.missing.queue_download("rel-1")
        self.assertEqual(again["queued"], 1)
        self.assertFalse(again["already_queued"])
        row = await self.db.fetchone("SELECT track_filter FROM jobs WHERE id = ?",
                                     (again["job_id"],))
        self.assertEqual(json.loads(row["track_filter"]), ["v4"])


class BulkDefaultTestCase(DbTestCase):
    async def _issue(self, ref: str, download: dict | None) -> int:
        await self.db.execute(
            "INSERT INTO md_issues (key, kind, entity, ref, artist_dir, album_key, severity, summary, "
            "detail, auto, state, created_at, updated_at) VALUES (?, 'missing_tracks', 'album', ?, "
            "'Ay', ?, 'warning', '', ?, 0, 'open', '', '')",
            (f"missing_tracks:{ref}", ref, f"Ay/{ref}",
             json.dumps({"missing": [{"title": "x"}], "download": download})))
        return (await self.db.fetchone("SELECT id FROM md_issues WHERE ref = ?", (ref,)))["id"]

    async def _issues(self) -> list[int]:
        tried = await self._issue("tried", {"job_id": "j1", "active": False, "attempts": [
            {"job_id": "j1", "queued_at": "", "videos": ["v"]}]})
        legacy = await self._issue("legacy", {"job_id": "j0", "active": False})
        # asked for while YouTube Music was busy: nothing ran yet
        untried = await self._issue("wanted", {"wanted": True, "wanted_at": ""})
        return [tried, legacy, untried]

    async def _apply(self, body) -> list[int]:
        from app.metadata import routes
        with mock.patch.object(routes.runner, "request_apply",
                               mock.AsyncMock(return_value=1)) as request:
            await routes.apply_issues(body)
        return request.await_args.args[0]

    async def test_download_missing_all_takes_only_untried_issues(self):
        """The sidebar's "Download missing all" takes what the group says."""
        from app.metadata import routes, views
        *_, untried = await self._issues()
        group = next(g for g in (await views.attention())["groups"]
                     if g["kind"] == "missing_tracks")
        self.assertEqual(group["count"], 3)
        self.assertEqual(group["bulk_count"], 1)
        self.assertEqual(await self._apply(routes.ApplyIn(
            kind="missing_tracks", only_auto=False, filter=group["bulk_filter"])), [untried])

    async def test_every_issue_ticked_in_review_is_taken(self):
        """Review's "select all" with no filter picked: every open one, less
        the unticked, as its confirm said."""
        from app.metadata import routes
        tried, legacy, untried = await self._issues()
        self.assertEqual(sorted(await self._apply(routes.ApplyIn(
            kind="missing_tracks", only_auto=False, exclude=[legacy]))), [tried, untried])


if __name__ == "__main__":
    unittest.main()
