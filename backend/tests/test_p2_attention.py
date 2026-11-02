"""Needs attention shows what needs the user: the count is open and failed
issues, with what is under way (queued) and the Tried bucket beside it;
the counts are published as soon as the file pass has run; and a Review's
"select all" takes exactly what its list shows.

The sidebar counted queued fixes, the Review dialog reloaded applied rows as
queued, and Dismiss on "select all" took rows the list didn't show (review
2026-11, P2-A4).

Run with: python -m unittest discover backend/tests"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_attention import DbTestCase
from test_metadata import HAVE_FFMPEG
from test_p2_issue_states import NSYNC, CelebrityTestCase


class CountsTestCase(DbTestCase):

    async def issue(self, kind: str, ref: str, state: str, *, artist: str = "Ay") -> int:
        await self.db.execute(
            "INSERT INTO md_issues (key, kind, entity, ref, artist_dir, album_key, severity, "
            "summary, detail, auto, state, created_at, updated_at) VALUES (?, ?, 'album', ?, ?, ?, "
            "'warning', ?, '{}', 1, ?, 'x', 'x')",
            (f"{kind}:{ref}", kind, ref, artist, f"{artist}/{ref}", f"{ref} ({state})", state))
        row = await self.db.fetchone("SELECT id FROM md_issues WHERE key = ?", (f"{kind}:{ref}",))
        await self.db.execute("INSERT INTO md_issue_refs (issue_id, artist_dir, album_key) "
                              "VALUES (?, ?, ?)", (row["id"], artist, f"{artist}/{ref}"))
        return row["id"]

    async def states(self, kind: str) -> dict[str, int]:
        ids = {}
        for state in ("open", "failed", "queued", "tried", "applied"):
            ids[state] = await self.issue(kind, state, state)
        return ids

    async def test_the_count_is_what_needs_the_user(self):
        from app.metadata import runner as runmod, views
        await self.states("missing_tracks")
        got = await views.attention()
        group = next(g for g in got["groups"] if g["kind"] == "missing_tracks")
        self.assertEqual((group["count"], group["failed_count"], group["queued_count"],
                          group["tried_count"]), (2, 1, 1, 1))
        self.assertEqual(got["total"], 2)
        self.assertEqual(sum((await views._issue_counts_by_artist())["Ay"].values()), 2)
        sent = []
        runner = runmod.Runner()
        with mock.patch.object(runmod.bus, "publish", lambda topic, data: sent.append((topic, data))):
            await runner.publish_issues()
        self.assertEqual(sent, [("library.issues", {"open": 2, "by_kind": {"missing_tracks": 2},
                                                    "in_progress": 1, "tried": 1})])

    async def test_a_group_under_way_still_shows(self):
        from app.metadata import views
        await self.issue("tags", "a", "queued")
        await self.issue("missing_tracks", "b", "tried")
        got = await views.attention()
        self.assertEqual({g["kind"]: (g["count"], g["queued_count"], g["tried_count"])
                          for g in got["groups"]},
                         {"tags": (0, 1, 0), "missing_tracks": (0, 0, 1)})
        self.assertEqual(got["total"], 0)

    async def selected(self, kind: str) -> tuple[int, int, dict]:
        from app.metadata import routes
        ids = await self.states(kind)
        listed = await routes.list_issues(kind=kind, state="actionable", limit=100, offset=0)
        return listed["total"], ids, {i["id"] for i in listed["items"]}

    async def test_select_all_counts_what_apply_takes(self):
        from app.metadata import routes
        total, ids, shown = await self.selected("tags")
        with mock.patch.object(routes.runner, "publish", mock.AsyncMock()):
            try:
                got = await routes.apply_issues(routes.ApplyIn(kind="tags", state="actionable",
                                                               only_auto=False))
            finally:
                routes.runner._apply_queue.clear()
        self.assertEqual(total, 2)
        self.assertEqual(shown, {ids["open"], ids["failed"]})
        self.assertEqual(got["queued"], total)

    async def test_dismiss_all_takes_only_what_the_list_shows(self):
        from app.metadata import routes
        total, ids, _ = await self.selected("tags")
        with mock.patch.object(routes.runner, "publish_issues", mock.AsyncMock()):
            got = await routes.dismiss_issues(routes.IdsIn(kind="tags", state="actionable"))
        self.assertEqual(got["dismissed"], total)
        queued = await self.db.fetchone("SELECT state FROM md_issues WHERE id = ?",
                                        (ids["queued"],))
        self.assertEqual(queued["state"], "queued")

    async def test_accept_incomplete_takes_the_tried_bucket(self):
        from app.metadata import routes
        ids = await self.states("missing_tracks")
        listed = await routes.list_issues(kind="missing_tracks", state="tried", limit=100, offset=0)
        self.assertEqual([i["id"] for i in listed["items"]], [ids["tried"]])
        with mock.patch.object(routes.runner, "publish", mock.AsyncMock()):
            try:
                applied = await routes.apply_issues(routes.ApplyIn(
                    kind="missing_tracks", state="tried", only_auto=False))
            finally:
                routes.runner._apply_queue.clear()
        self.assertEqual(applied["queued"], 0)      # never queued again by a select-all
        with mock.patch.object(routes.runner, "publish_issues", mock.AsyncMock()):
            got = await routes.dismiss_issues(routes.IdsIn(kind="missing_tracks", state="tried"))
        self.assertEqual(got["dismissed"], 1)
        rows = {r["id"]: r["state"] for r in await self.db.fetchall("SELECT id, state FROM md_issues")}
        self.assertEqual(rows[ids["tried"]], "dismissed")
        self.assertEqual(rows[ids["open"]], "open")


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class FilePassTestCase(CelebrityTestCase):

    async def test_issues_are_published_after_the_file_pass(self):
        from app.metadata import runner as runmod
        sent = []
        with self.services(), \
                mock.patch.object(self.runner, "publish_issues",
                                  runmod.Runner.publish_issues.__get__(self.runner)), \
                mock.patch.object(runmod.bus, "publish",
                                  lambda topic, data: sent.append((topic, data))):
            await self.runner.check_unit(NSYNC)
            tags = await self.open_issue("tags", NSYNC)
            before = (await self.db.fetchone(
                "SELECT COUNT(*) AS n FROM md_issues WHERE state IN ('open', 'failed')"))["n"]
            self.assertEqual(await self.runner.request_apply([tags["id"]]), 1)
            sent.clear()
            await self.runner._files_pass(self.runner._apply_queue.pop())
            self.assertEqual((await self.issue(tags["id"]))["state"], "applied")
        published = [d for topic, d in sent if topic == "library.issues"]
        self.assertTrue(published, [topic for topic, _ in sent])
        self.assertEqual(published[-1]["open"], before - 1)
        self.assertNotIn("tags", published[-1]["by_kind"])
        changed = [d for topic, d in sent if topic == "library.changed"]
        self.assertTrue(changed and NSYNC in changed[-1]["album_keys"], changed)


if __name__ == "__main__":
    unittest.main()
