"""Fixes that converge: once auto-apply has run a pass, the next passes
change nothing. A label the tag writer strips as junk is never wanted, a
copy of an album the merge would leave where it is is no copy to merge, and
a two-stage track_order is applied once its Plex part has run.

DMX's Mixtape got 23 tag batches for its "Siccness.net" label, and Thriller
25 was planned for a merge every pass that merged nothing (review 2026-11,
P1-A2).

Run with: python -m unittest discover backend/tests"""
import contextlib
import copy
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.metadata import issues, units
from test_metadata import HAVE_FFMPEG
from test_p0_oscillation import FILES, KEEP, ORIG, RELEASES, SONGS, thriller
from test_p1_unit_id import MJ, UnitTestCase

MIXTAPE = "DMX/(2010) Mixtape"
SAMPLER = f"{MJ}/(1982) Thriller Sampler"
POST = "Paul Kelly/(1985) Post"
POST_SONGS = [("Incident on South Dowling", 3.0), ("From St Kilda to Kings Cross", 4.0),
              ("Little Boy Don't Lose Your Balls", 5.0)]


def album(rid: str, group: str, title: str, date: str, artist: str, songs, *,
          label: str) -> dict:
    """A release of its own: its own ids, recordings and group."""
    return {
        "id": rid, "title": title, "date": date, "disambiguation": "", "status": "Official",
        "country": "US",
        "artist-credit": [{"name": artist, "joinphrase": "",
                           "artist": {"id": f"{rid}-artist", "name": artist,
                                      "sort-name": artist}}],
        "release-group": {"id": group, "primary-type": "Album", "secondary-types": [],
                          "first-release-date": date},
        "label-info": [{"label": {"name": label}}],
        "media": [{"position": 1, "format": "CD", "track-count": len(songs), "tracks": [
            {"id": f"{rid}-t{n}", "position": n, "number": str(n), "title": t,
             "length": int(s * 1000), "recording": {"id": f"{rid}-r{n}", "title": t, "isrcs": []}}
            for n, (t, s) in enumerate(songs, start=1)]}],
    }


def sampler() -> dict:
    """Thriller's group again, with "Wanna Be Startin' Somethin'" in a long
    mix: a copy the merge leaves where it is (one track, two lengths) beside
    a recording the keeper's release hasn't got."""
    rel = thriller("rel-x", "Thriller Sampler", "1982-11-30", ["r1", "r9"])
    rel["media"][0]["tracks"][0]["length"] = 10000
    return rel


class ConvergeBase(UnitTestCase):
    """Real files, the real scan, registry, checks, planners and executors;
    MusicBrainz and Last.fm faked, and Plex too: no server, only a mirror
    of the one album it holds (Post), and a fake that takes the edits."""

    releases = {**RELEASES, "rel-x": sampler(),
                "rel-dmx": album("rel-dmx", "rg-dmx", "Mixtape", "2010-01-01", "DMX",
                                 [("Intro", 3.0), ("Ruff", 4.0)], label="Siccness.net"),
                "rel-post": album("rel-post", "rg-post", "Post", "1985-06-01", "Paul Kelly",
                                  POST_SONGS, label="Mushroom")}

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app.config import settings
        p = mock.patch.object(settings, "metadata_write_plex", True)
        p.start()
        self.patches.append(p)
        self.edits: dict[str, dict] = {}

    # ------------------------------------------------ fixtures
    def mixtape(self) -> None:
        for n, title in enumerate(["Intro", "Ruff"], start=1):
            self.write(f"{MIXTAPE}/0{n} - {title}.mp3", seconds=2.0 + n, title=title, artist="DMX",
                       albumartist="DMX", album="Mixtape", tracknumber=str(n),
                       mb_albumid="rel-dmx", mb_releasetrackid=f"rel-dmx-t{n}",
                       label="Siccness.net")

    def sampler_files(self) -> None:
        for n, (rec, seconds) in enumerate([("r1", 10.0), ("r9", SONGS["r9"][1])], start=1):
            title = SONGS[rec][0]
            self.write(f"{SAMPLER}/0{n} - {title}.mp3", seconds=seconds, title=title, artist=MJ,
                       albumartist=MJ, album="Thriller Sampler", tracknumber=str(n),
                       mb_albumid="rel-x", mb_releasegroupid="rg-1", mb_trackid=rec,
                       mb_releasetrackid=f"rel-x-t{n}")

    async def post(self) -> None:
        """Post: its first file says it is track 3, and Plex has the second as
        track 5 (the third is right in both)."""
        for n, (title, seconds) in enumerate(POST_SONGS, start=1):
            self.write(f"{POST}/0{n} - {title}.mp3", seconds=seconds, title=title,
                       artist="Paul Kelly", albumartist="Paul Kelly", album="Post",
                       tracknumber="3" if n == 1 else str(n), mb_albumid="rel-post",
                       mb_releasetrackid=f"rel-post-t{n}")
        await self.mirror("400", "artist", title="Paul Kelly")
        await self.mirror("500", "album", title="Post", guid="mbid://rel-post", mbid="rel-post",
                          parent_key="400", thumb="/thumb", genres=json.dumps(["Rock"]),
                          folder=POST)
        for n, (title, _) in enumerate(POST_SONGS, start=1):
            await self.mirror(str(500 + n), "track", title=title, parent_key="500",
                              grandparent_key="400", idx={1: 3, 2: 5}.get(n, n), parent_idx=1,
                              path=f"{POST}/0{n} - {title}.mp3", folder=POST,
                              genres=json.dumps(["Rock"]))

    async def mirror(self, key, type_, **fields):
        fields.setdefault("seen_at", time.time())
        cols = ", ".join(["rating_key", "type", *fields])
        await self.db.execute(
            f"INSERT INTO plex_items ({cols}) VALUES ({','.join('?' * (len(fields) + 2))})",
            (key, type_, *fields.values()))

    @contextlib.contextmanager
    def plex(self):
        """Plex as far as Post's renumbering goes: an edit is taken, and the
        mirror reads it back."""
        from app.metadata import apply, plexsync
        from app.plex import plex as real
        fake = mock.MagicMock(configured=False)
        fake.field_pairs = real.field_pairs

        async def edit(type_num, rating_key, pairs):
            self.edits.setdefault(rating_key, {}).update(dict(pairs))

        async def refresh_items(keys):
            for k in keys:
                got = self.edits.get(k) or {}
                if "index.value" in got:
                    await self.db.execute("UPDATE plex_items SET idx = ? WHERE rating_key = ?",
                                          (int(got["index.value"]), k))
                if "parentIndex.value" in got:
                    await self.db.execute("UPDATE plex_items SET parent_idx = ? "
                                          "WHERE rating_key = ?", (int(got["parentIndex.value"]), k))
        fake.edit = mock.AsyncMock(side_effect=edit)
        with mock.patch.object(apply, "plex", fake), \
                mock.patch.object(plexsync, "refresh_items", refresh_items):
            yield fake

    # ------------------------------------------------ the runner's passes
    async def apply_pass(self, ids: list[int]) -> None:
        """Queue these fixes as auto-apply does, then the runner's file pass
        and Plex pass."""
        if ids and await self.runner.request_apply(ids):
            await self.runner._files_pass(self.runner._apply_queue.pop())
        await self.runner._plex_pass()

    async def marks(self) -> tuple[int, int]:
        change = await self.db.fetchone("SELECT COALESCE(MAX(id), 0) AS n FROM md_changes")
        event = await self.db.fetchone("SELECT COALESCE(MAX(id), 0) AS n FROM md_issue_events")
        return change["n"], event["n"]

    async def since(self, marks: tuple[int, int]) -> dict:
        batches = await self.db.fetchall("SELECT DISTINCT batch_id, target, field FROM md_changes "
                                         "WHERE id > ?", (marks[0],))
        queued = await self.db.fetchall(
            "SELECT i.kind, i.album_key FROM md_issue_events e JOIN md_issues i ON i.id = e.issue_id "
            "WHERE e.id > ? AND e.from_state = 'open' AND e.to_state = 'queued'", (marks[1],))
        return {"batches": sorted({(r["target"], r["field"]) for r in batches}),
                "queued": sorted((r["kind"], r["album_key"]) for r in queued)}

    async def issue_states(self, kind: str, key: str) -> list[tuple]:
        row = await self.db.fetchone("SELECT id FROM md_issues WHERE kind = ? AND album_key = ?",
                                     (kind, key))
        self.assertIsNotNone(row, f"no {kind} for {key}")
        return [tuple(r) for r in await self.db.fetchall(
            "SELECT from_state, to_state FROM md_issue_events WHERE issue_id = ? ORDER BY id",
            (row["id"],))]


# ------------------------------------------------------------------ five cycles
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class ConvergeTestCase(ConvergeBase):

    async def test_five_auto_apply_cycles_change_nothing_after_the_first(self):
        from app import library
        await self.make_thriller()
        self.sampler_files()
        self.mixtape()
        await self.post()
        await library.scan(full=True)
        seen = []
        with self.services(), self.plex():
            for n in range(1, 6):
                marks = await self.marks()
                auto = await self.check_all()
                copies = await self.plan_copies()
                if n == 1:
                    # the user merges Thriller's copies; the rest is auto-apply's
                    self.assertEqual([c["detail"]["keep"] for c in copies], [KEEP])
                    auto += [c["id"] for c in copies]
                else:
                    self.assertEqual([(c["key"], c["summary"]) for c in copies], [], f"cycle {n}")
                await self.apply_pass(auto)
                seen.append(await self.since(marks))
        first, *rest = seen
        self.assertTrue(first["batches"], "the first cycle changed nothing")
        for n, got in enumerate(rest, start=2):
            with self.subTest(cycle=n):
                self.assertEqual(got, {"batches": [], "queued": []})
        # and every fix that ran stayed fixed
        left = await self.db.fetchall("SELECT kind, album_key, error FROM md_issues "
                                      "WHERE state = 'failed'")
        self.assertEqual([tuple(r) for r in left], [])
        self.assertEqual(sorted(tuple(r) for r in await self.db.fetchall(
            "SELECT rating_key, idx FROM plex_items WHERE type = 'track'")),
            [("501", 1), ("502", 2), ("503", 3)])


# ------------------------------------------------------------------ one at a time
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class LabelTestCase(ConvergeBase):

    async def test_a_label_the_writer_calls_junk_is_never_wanted(self):
        from app import library
        self.mixtape()
        await library.scan(full=True)
        with self.services(), self.plex():
            await units.sync_registry()
            first = await self.runner.check_unit(MIXTAPE)
            want = json.loads((await self.record(MIXTAPE))["desired"])
            self.assertNotIn("Siccness.net", json.dumps(want))
            tags = await self.open_issue("tags", MIXTAPE)
            self.assertIn(tags["id"], first["auto_ids"])
            await self.apply_pass([tags["id"]])
            self.assertEqual((await self.issue(tags["id"]))["state"], "applied")
            again = await self.runner.check_unit(MIXTAPE)
        self.assertNotIn("tags", [k.split(":", 1)[0] for k in again["planned"]])
        for path in sorted((self.lib / MIXTAPE).glob("*.mp3")):
            with self.subTest(path=path.name):
                self.assertNotIn("label", self.tags(str(path.relative_to(self.lib))))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class CopiesTestCase(ConvergeBase):

    async def test_copies_the_merge_would_leave_are_not_an_issue(self):
        """Thriller 25 and the sampler share one recording, in two lengths:
        the merge would leave both of the sampler's files where they are."""
        from app import library
        for folder, name, rec, rid, n in [f for f in FILES
                                          if f[0] == KEEP]:
            self.thriller_file(folder, name, rec, n)
        self.sampler_files()
        await library.scan(full=True)
        with self.services():
            await self.check_all()
            keep, sampler_ = await self.record(KEEP), await self.record(SAMPLER)
            self.assertEqual((keep["status"], sampler_["status"]), ("resolved", "resolved"))
            # one planned before, as the old planner did
            ref = f"rg-1:{min(keep['unit_id'], sampler_['unit_id'])}"
            await issues.save([issues.planned(
                "album_copies", ref, "Thriller 25: 2 copies", {"keep": KEEP, "copies": [SAMPLER]},
                artist_dir=MJ, album_key=KEEP, refs=[(MJ, KEEP), (MJ, SAMPLER)])],
                scope_kinds=("album_copies",), all_scope=True)
            planned = await issues.plan_album_copies()
            left = await self.plan_copies()
            old = await self.db.fetchone("SELECT state FROM md_issues WHERE key = ?",
                                         (f"album_copies:{ref}",))
        self.assertEqual([p["key"] for p in planned], [])
        self.assertEqual(left, [])
        self.assertNotIn(old["state"], ("open", "queued", "failed"))

    async def test_a_set_says_what_the_merge_would_do(self):
        from app import library
        await self.make_thriller()
        self.sampler_files()
        await library.scan(full=True)
        with self.services():
            await self.check_all()
            planned = [p for p in await issues.plan_album_copies() if "rg-1" in p["key"]]
        self.assertEqual(len(planned), 1, planned)
        detail = planned[0]["detail"]
        # the original's three files: two move in, one is the worse copy;
        # the keeper's worse copy of the first goes too; the sampler stays out
        self.assertEqual(detail["copies"], [ORIG])
        self.assertEqual((detail["moves"], detail["trash"], detail["leaves"]), (2, 2, 0))
        self.assertIn("2 moved in, 2 to Trash", planned[0]["summary"])


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class TrackOrderTestCase(ConvergeBase):

    async def test_track_order_is_applied_once_its_plex_part_has_run(self):
        from app import library
        await self.post()
        await library.scan(full=True)
        with self.services(), self.plex():
            await units.sync_registry()
            await self.runner.check_unit(POST)
            order = await self.open_issue("track_order", POST)
            detail = json.loads(order["detail"])
            self.assertTrue(detail["files"] and detail["plex"], detail)
            self.assertEqual(await self.runner.request_apply([order["id"]]), 1)
            await self.runner._files_pass(self.runner._apply_queue.pop())
            after_files = await self.issue(order["id"])
            await self.runner._plex_pass()
            after_plex = await self.issue(order["id"])
        self.assertEqual(after_files["state"], "queued")
        self.assertEqual(after_plex["state"], "applied", after_plex["error"])
        self.assertEqual(after_plex["attempts"], 1)
        self.assertEqual(await self.issue_states("track_order", POST),
                         [(None, "open"), ("open", "queued"), ("queued", "applied")])
        self.assertEqual(self.tags(f"{POST}/01 - {POST_SONGS[0][0]}.mp3").get("tracknumber"), ["1"])
        self.assertEqual(sorted(tuple(r) for r in await self.db.fetchall(
            "SELECT rating_key, idx FROM plex_items WHERE type = 'track'")),
            [("501", 1), ("502", 2), ("503", 3)])


if __name__ == "__main__":
    unittest.main()
