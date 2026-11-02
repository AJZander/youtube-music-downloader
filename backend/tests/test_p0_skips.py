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

from app import library_index
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


# ------------------------------------------------------------------ P0-4
class SkippedListCase(DbTestCase):
    async def indexed(self, path, *, album=None, rg=None):
        """A library file, as a scan leaves it."""
        await self.db.execute(
            "INSERT INTO library_index (norm_artist, norm_title, path, dirpath, artist_dir, "
            "album, mb_releasegroupid, added_at) VALUES (?,?,?,?,?,?,?,?)",
            ("a", "t", path, path.rsplit("/", 1)[0], path.split("/", 1)[0], album, rg, "t"))

    async def identified(self, folder, rg, *, tag=None):
        """An album folder the metadata manager has matched to a release group
        (`tag`: the album tag it registered the folder with)."""
        await self.db.execute(
            "INSERT INTO md_albums (key, dirpath, artist_dir, album_tag, releasegroup_mbid) "
            "VALUES (?,?,?,?,?)", (folder, folder, folder.split("/", 1)[0], tag, rg))

    async def skip(self, track_id, job_id, album, matched, *, error_code=None, acked=0):
        await self.db.execute(
            "INSERT INTO tracks (id, job_id, position, status, album, library_path, "
            "error_code, review_acked, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (track_id, job_id, 1, TrackStatus.SKIPPED_DUPLICATE, album, matched,
             error_code, acked, "t"))

    async def listed(self):
        out = await self.api.list_skipped(limit=500, offset=0, only_different_album=False)
        return {t["id"]: (t["different_album"], t["different_reason"]) for t in out["items"]}


class DifferentAlbumTestCase(SkippedListCase):
    """The label compared the raw folder name `(2019) Rare Ass Shit` with the
    album tag `Rare Ass Shit`, so it called 999 of the 1,008 Download-missing
    skips "different album", the 558 that matched a file in the album's own
    folder among them."""

    async def test_a_year_prefixed_folder_is_the_same_album(self):
        await self.add_job()
        await self.indexed("Rich The Kid/(2019) Rare Ass Shit/05 - Splashin.m4a")
        await self.skip("t1", "j1", "Rare Ass Shit",
                        "Rich The Kid/(2019) Rare Ass Shit/05 - Splashin.m4a")
        self.assertEqual(await self.listed(), {"t1": (False, None)})

    async def test_a_download_missing_skip_in_its_own_folder_is_not_different(self):
        # YouTube Music names the album its own way; the file the gate found
        # is in the album's Disc 2 folder, spelled in another case.
        await self.add_job(target_dir="Slipknot/(2008) All Hope Is Gone")
        matched = "SLIPKNOT/(2008) all hope is gone/Disc 2/03 - Wherein Lies Continue.flac"
        await self.indexed(matched, album="All Hope Is Gone")
        await self.skip("t1", "j1", "All Hope Is Gone (Special Edition)", matched)
        # one an earlier Download missing delivered: the importer's index row
        # has no dirpath until the next scan
        delivered = "Slipknot/(2008) All Hope Is Gone/12 - Snuff.m4a"
        await library_index.add_entry(delivered, "Slipknot", "Snuff",
                                      "All Hope Is Gone (Special Edition)", "m4a", 276.0, "v12")
        await self.skip("t2", "j1", "All Hope Is Gone (Bonus Tracks)", delivered)
        self.assertEqual(await self.listed(), {"t1": (False, None), "t2": (False, None)})

    async def test_a_sibling_edition_is_labelled(self):
        await self.add_job(target_dir="Wiz Khalifa/(2011) Rolling Papers")
        await self.identified("Wiz Khalifa/(2011) Rolling Papers", "rg-rolling-papers")
        matched = "Wiz Khalifa/(2011) Rolling Papers (Deluxe Version)/04 - Roll Up.m4a"
        await self.indexed(matched, album="Rolling Papers (Deluxe Version)",
                           rg="rg-rolling-papers")
        # The names agree; the folder and the release group say what it is.
        await self.skip("t1", "j1", "Rolling Papers (Deluxe Version)", matched)
        self.assertEqual(await self.listed(), {"t1": (True, "edition")})

    async def test_another_release_group_is_different(self):
        # library.album_identity strips every [...], so by name a single
        # folder is the album.
        await self.add_job(target_dir="Paul Kelly/(1985) Post")
        await self.identified("Paul Kelly/(1985) Post", "rg-post-album")
        matched = "Paul Kelly/Post [Single]/01 - Post.m4a"
        await self.indexed(matched, album="Post", rg="rg-post-single")
        await self.skip("t1", "j1", "Post", matched)
        self.assertEqual(await self.listed(), {"t1": (True, "release group")})

    async def test_edition_words_count_without_release_groups(self):
        await self.add_job()
        matched = "Paul Kelly/Seventy (Deluxe Edition)/03 - Rita Wrote A Letter.m4a"
        await self.indexed(matched)
        await self.skip("t1", "j1", "Seventy", matched)
        await self.skip("t2", "j1", "Seventy (Deluxe Edition)", matched)
        self.assertEqual(await self.listed(), {"t1": (True, "album"), "t2": (False, None)})

    async def test_a_moved_match_is_judged_by_its_folder(self):
        # A Download missing for Music To Be Murdered By, all of it skipped
        # for the copies in the Limited Edition folder. Since then the target
        # folder has gone (its one file was filed in there) and two of the
        # matched files moved on to Side B. The folder still says what album
        # the gate saw, as it does for the skips whose files stayed.
        await self.add_job(target_dir="Eminem/(2020) Music To Be Murdered By")
        limited = "Eminem/(2020) Music to Be Murdered By (Limited Edition)"
        side_b = "Eminem/(2020) Music to Be Murdered By_ Side B (deluxe edition)"
        await self.identified(limited, "rg-mtbmb", tag="Music To Be Murdered By")
        await self.identified(side_b, "rg-mtbmb",
                              tag="Music to Be Murdered By: Side B (deluxe edition)")
        await self.indexed(f"{limited}/01 - Premonition (intro).m4a",
                           album="Music to Be Murdered By", rg="rg-mtbmb")
        await self.indexed(f"{side_b}/2-04 - Alfred (interlude).m4a",
                           album="Music to Be Murdered By: Side B (deluxe edition)",
                           rg="rg-mtbmb")
        await self.skip("stayed", "j1", "Music To Be Murdered By",
                        f"{limited}/01 - Premonition (intro).m4a")
        await self.skip("moved", "j1", "Music To Be Murdered By",
                        f"{limited}/04 - Alfred (interlude).m4a")
        self.assertEqual(await self.listed(),
                         {"stayed": (False, None), "moved": (False, None)})


class RecheckBannerTestCase(SkippedListCase):
    """Re-check re-queued every labelled skip whatever gate made it, so the
    current gate skipped them again and the banner came straight back."""

    async def test_skips_made_by_the_current_gate_are_not_offered(self):
        await self.add_job("done", JobStatus.COMPLETED, 1)
        await self.add_job("dm", JobStatus.COMPLETED, 2,
                           target_dir="Wiz Khalifa/(2011) Rolling Papers")
        await self.identified("Wiz Khalifa/(2011) Rolling Papers", "rg-rolling-papers")
        sibling = "Wiz Khalifa/(2011) Rolling Papers (Deluxe Version)/04 - Roll Up.m4a"
        await self.indexed(sibling, rg="rg-rolling-papers")
        # legacy: the old artist + title gate skipped it for a compilation copy
        await self.skip("legacy", "done", "Under The Sun", "PK/Songs From The South/02.m4a",
                        acked=1)
        # made by the current gate: it would make them again
        await self.skip("held", "done", "Gossip", "PK/Time/09.m4a",
                        error_code="held_same_album")
        await self.skip("dest", "done", "Gossip", "PK/Time/10.m4a", error_code="dest_exists")
        # held by another edition: also skipped again
        await self.skip("edition", "dm", "Rolling Papers", sibling)

        listed = await self.listed()
        self.assertEqual(listed["held"], (True, "album"))
        self.assertEqual(listed["edition"], (True, "edition"))
        self.assertEqual(await self.api.skipped_recheck_preview(), {"tracks": 1, "jobs": 1})
        with mock.patch.object(self.api.worker, "wake"):
            result = await self.api.skipped_recheck()
        self.assertEqual(result, {"requeued": 1, "jobs": 1})
        status = {r["id"]: r["status"] for r in await self.db.fetchall("SELECT * FROM tracks")}
        self.assertEqual(status, {"legacy": TrackStatus.PENDING,
                                  "held": TrackStatus.SKIPPED_DUPLICATE,
                                  "dest": TrackStatus.SKIPPED_DUPLICATE,
                                  "edition": TrackStatus.SKIPPED_DUPLICATE})

    async def test_download_missing_skips_are_not_offered(self):
        """Download missing came after the same-album gate, so that gate made
        every skip in one, stamped or not (all 1,008 are unstamped). Re-check
        would only skip them again, or download what another edition holds."""
        await self.add_job("done", JobStatus.COMPLETED, 1)
        await self.add_job("dm", JobStatus.COMPLETED, 2,
                           target_dir="Eminem/(2020) Music To Be Murdered By")
        await self.add_job("single", JobStatus.COMPLETED, 3,
                           target_dir="Australian Crawl/(1980) The Boys Light Up")
        # The target folder has gone and so has the matched file, which now
        # sits in Side B; its folder is known by release group only, so by
        # name (edition words count) it is another album.
        limited = "Eminem/(2020) Music to Be Murdered By (Limited Edition)"
        side_b = "Eminem/(2020) Music to Be Murdered By_ Side B (deluxe edition)"
        await self.identified(limited, "rg-mtbmb")
        await self.identified(side_b, "rg-mtbmb")
        await self.indexed(f"{side_b}/2-04 - Alfred (interlude).m4a", rg="rg-mtbmb")
        await self.skip("moved", "dm", "Music To Be Murdered By",
                        f"{limited}/04 - Alfred (interlude).m4a")
        # The gate took the [Single] folder for the album; it still would.
        await self.identified("Australian Crawl/(1980) The Boys Light Up", "rg-album")
        single = "Australian Crawl/(1980) The Boys Light Up [Single]/01 - The Boys Light Up.m4a"
        await self.indexed(single, album="The Boys Light Up", rg="rg-single")
        await self.skip("single", "single", "The Boys Light Up", single)
        await self.skip("legacy", "done", "Under The Sun", "PK/Songs From The South/02.m4a")

        listed = await self.listed()
        self.assertEqual(listed["moved"], (True, "album"))
        self.assertEqual(listed["single"], (True, "release group"))
        self.assertEqual(await self.api.skipped_recheck_preview(), {"tracks": 1, "jobs": 1})
        with mock.patch.object(self.api.worker, "wake"):
            result = await self.api.skipped_recheck()
        self.assertEqual(result, {"requeued": 1, "jobs": 1})
        status = {r["id"]: r["status"] for r in await self.db.fetchall("SELECT * FROM tracks")}
        self.assertEqual(status, {"legacy": TrackStatus.PENDING,
                                  "moved": TrackStatus.SKIPPED_DUPLICATE,
                                  "single": TrackStatus.SKIPPED_DUPLICATE})


if __name__ == "__main__":
    unittest.main()
