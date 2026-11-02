"""Changes that land whole, and journal lookups that don't read the journal.

Every db.execute committed on its own, so a writer that failed half-way
(an issue saved without its listings, a move followed by some tables and
not others) left the half it had done. db.transaction() makes a writer's
statements one commit, or none. And the journal (md_changes, 285,911 rows
on a copy of the live data) was read whole by every Plex-lock check, undo
step, oscillation check and match flip-flop check (review 2026-11, P1-F5).

Run with: python -m unittest discover backend/tests"""
import asyncio
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.identity import path_key
from app.metadata import issues, units
from test_attention import DbTestCase

OLD, NEW = "Paul Kelly/Post", "Paul Kelly/(1985) Post"
FILES = ["01 - Post.mp3", "02 - From St Kilda to Kings Cross.mp3"]


class Boom(Exception):
    pass


async def snapshot(db, *tables: str) -> dict[str, list[tuple]]:
    return {t: [tuple(r) for r in await db.fetchall(f"SELECT * FROM {t} ORDER BY rowid")]
            for t in tables}


def item(key: str, refs: list[tuple]) -> dict:
    return issues.planned("tags", key, f"tags of {key}", {"n": 1}, album_key=refs[0][1],
                          artist_dir=refs[0][0], refs=refs)


class TransactionTestCase(DbTestCase):

    async def rows(self, sql: str, params=()) -> list[tuple]:
        return [tuple(r) for r in await self.db.fetchall(sql, params)]

    async def test_a_failure_rolls_back_every_statement(self):
        with self.assertRaises(Boom):
            async with self.db.transaction():
                await self.db.execute("INSERT INTO md_state (key, value) VALUES ('a', '1')")
                await self.db.executemany("INSERT INTO md_state (key, value) VALUES (?, ?)",
                                          [("b", "2"), ("c", "3")])
                self.assertEqual(len(await self.rows("SELECT * FROM md_state")), 3)
                raise Boom()
        self.assertEqual(await self.rows("SELECT * FROM md_state"), [])
        # and the connection is usable after
        await self.db.execute("INSERT INTO md_state (key, value) VALUES ('d', '4')")
        self.assertEqual(await self.rows("SELECT key FROM md_state"), [("d",)])

    async def test_db_calls_inside_a_transaction_share_it(self):
        commits = []
        real = self.db._conn.commit

        async def commit():
            commits.append(1)
            await real()

        with mock.patch.object(self.db._conn, "commit", commit):
            async with self.db.transaction():
                await asyncio.wait_for(issues.save(
                    [item("k1", [("A", "A/x")]), item("k2", [("A", "A/y"), ("B", "B/z")])],
                    scope_kinds=("tags",), all_scope=True), 5)
                async with self.db.transaction():          # nested: the same one
                    await self.db.execute("INSERT INTO md_state (key, value) VALUES ('n', '1')")
        self.assertEqual(len(commits), 1)
        self.assertEqual(len(await self.rows("SELECT * FROM md_issues")), 2)
        self.assertEqual(len(await self.rows("SELECT * FROM md_issue_refs")), 3)

    async def test_another_task_waits_for_the_commit(self):
        inside, go = asyncio.Event(), asyncio.Event()

        async def writer():
            async with self.db.transaction():
                await self.db.execute("INSERT INTO md_state (key, value) VALUES ('w', '1')")
                inside.set()
                await go.wait()
                await self.db.execute("INSERT INTO md_state (key, value) VALUES ('x', '2')")

        task = asyncio.create_task(writer())
        await asyncio.wait_for(inside.wait(), 5)
        reader = asyncio.create_task(self.db.fetchall("SELECT key FROM md_state ORDER BY key"))
        await asyncio.sleep(0.1)
        self.assertFalse(reader.done())
        go.set()
        await asyncio.wait_for(task, 5)
        self.assertEqual([tuple(r) for r in await asyncio.wait_for(reader, 5)],
                         [("w",), ("x",)])

    async def test_issues_save_is_all_or_nothing(self):
        await issues.save([item("k1", [("A", "A/x")])], scope_kinds=("tags",), all_scope=True)
        before = await snapshot(self.db, "md_issues", "md_issue_refs")
        broken = item("k3", [("A", "A/q")])
        del broken["entity"]
        with self.assertRaises(KeyError):
            await issues.save([item("k1", [("A", "A/x"), ("C", "C/w")]),
                               item("k2", [("A", "A/y")]), broken],
                              scope_kinds=("tags",), all_scope=True)
        self.assertEqual(await snapshot(self.db, "md_issues", "md_issue_refs"), before)

    async def seed_album(self) -> str:
        """Post at OLD: indexed, recorded with a unit, downloaded by a job
        (as the importer spelt it, in another case), and a Download missing
        queued into it."""
        uid = "u-post"
        await self.db.executemany(
            "INSERT INTO library_index (norm_artist, norm_title, path, added_at, dirpath, "
            "artist_dir, path_key, unit_id, album, album_artist, title) "
            "VALUES ('paul kelly', ?, ?, 'x', ?, 'Paul Kelly', ?, ?, 'Post', 'Paul Kelly', ?)",
            [(f, f"{OLD}/{f}", OLD, path_key(f"{OLD}/{f}"), uid, f) for f in FILES])
        await self.db.execute("INSERT INTO md_albums (key, unit_id, dirpath, artist_dir, status) "
                              "VALUES (?, ?, ?, 'Paul Kelly', 'resolved')", (OLD, uid, OLD))
        for job, status, target in (("j1", "completed", None), ("j2", "queued", OLD)):
            await self.db.execute(
                "INSERT INTO jobs (id, source_url, provider, source_type, status, target_dir, "
                "created_at) VALUES (?, 'u', 'youtube_music', 'album', ?, ?, 'x')",
                (job, status, target))
        await self.db.execute(
            "INSERT INTO tracks (id, job_id, status, library_path, created_at) "
            "VALUES ('t1', 'j1', 'completed', ?, 'x')", (f"{OLD}/{FILES[0]}".upper(),))
        await issues.save([issues.planned("tags", uid, "tags", {}, album_key=OLD,
                                          artist_dir="Paul Kelly")],
                          scope_kinds=("tags",), album_key=OLD)
        return uid

    async def test_a_failed_relocate_changes_no_table(self):
        from app import relocate
        await self.seed_album()
        tables = ("library_index", "tracks", "jobs", "md_albums", "md_issues", "md_issue_refs",
                  "md_state")
        before = await snapshot(self.db, *tables)
        pairs = [(f"{OLD}/{f}", f"{NEW}/{f}") for f in FILES]
        # the last step fails: every table it had followed already goes back
        with mock.patch.object(relocate, "bump", mock.AsyncMock(side_effect=Boom())), \
                self.assertRaises(Boom):
            await relocate.relocate(pairs, units={OLD: NEW})
        self.assertEqual(await snapshot(self.db, *tables), before)
        # and the same call, whole, does follow
        await relocate.relocate(pairs, units={OLD: NEW})
        self.assertEqual(await self.rows("SELECT key FROM md_albums"), [(NEW,)])
        self.assertEqual(await self.rows("SELECT library_path FROM tracks"),
                         [(f"{NEW}/{FILES[0]}",)])
        self.assertEqual(await self.rows("SELECT target_dir FROM jobs WHERE id = 'j2'"), [(NEW,)])

    async def test_a_failed_forget_changes_no_table(self):
        from app import relocate
        await self.seed_album()
        before = await snapshot(self.db, "library_index", "md_state")
        with mock.patch.object(relocate, "bump", mock.AsyncMock(side_effect=Boom())), \
                self.assertRaises(Boom):
            await relocate.forget([f"{OLD}/{FILES[0]}"], [OLD])
        self.assertEqual(await snapshot(self.db, "library_index", "md_state"), before)

    async def test_sync_registry_is_atomic(self):
        """Two albums' folders were renamed into one: the first record takes
        the new key, the second merges into it. The merge fails half-way:
        neither happened."""
        await self.db.executemany(
            "INSERT INTO library_index (norm_artist, norm_title, path, added_at, dirpath, "
            "artist_dir, path_key, unit_id, album, album_artist, title) "
            "VALUES ('paul kelly', ?, ?, 'x', ?, 'Paul Kelly', ?, ?, 'Post', 'Paul Kelly', ?)",
            [(f"t{n}", f"{NEW}/0{n} - T{n}.mp3", NEW, path_key(f"{NEW}/0{n} - T{n}.mp3"), uid,
              f"T{n}") for n, uid in ((1, "u1"), (2, "u1"), (3, "u2"))])
        await self.db.executemany(
            "INSERT INTO md_albums (key, unit_id, dirpath, artist_dir, manual, release_mbid) "
            "VALUES (?, ?, ?, 'Paul Kelly', ?, ?)",
            [(OLD, "u1", OLD, 0, None), ("Paul Kelly/Post CD", "u2", "Paul Kelly/Post CD", 1,
                                         "rel-post")])
        await self.db.execute("INSERT INTO md_overrides (entity, ref, field, value, created_at) "
                              "VALUES ('album', 'u2', 'genres', '[\"Rock\"]', 'x')")
        tables = ("md_albums", "md_overrides", "md_unit_aliases", "library_index")
        before = await snapshot(self.db, *tables)
        real = units.repoint
        calls = []

        async def repoint(*args):
            calls.append(args)
            if len(calls) == 2:         # the merge's, after it wrote the keeper's record
                raise Boom()
            return await real(*args)

        with mock.patch.object(units, "repoint", repoint), self.assertRaises(Boom):
            await units.sync_registry()
        self.assertEqual(await snapshot(self.db, *tables), before)
        counts = await units.sync_registry()
        self.assertEqual((counts["successors"], counts["merged"]), (1, 1))
        self.assertEqual(await self.rows("SELECT key, unit_id, manual FROM md_albums"),
                         [(NEW, "u1", 1)])
        self.assertEqual(await self.rows("SELECT ref FROM md_overrides"), [("u1",)])

    async def test_a_statement_that_failed_alone_does_not_break_the_next_transaction(self):
        """sqlite3 opens a transaction for an INSERT on its own, and one that
        fails (a constraint) leaves it open: the next transaction() starts
        all the same, and so does the one after it."""
        await self.db.execute("INSERT INTO md_state (key, value) VALUES ('a', '1')")
        for n in (1, 2):
            with self.assertRaises(sqlite3.IntegrityError):
                await self.db.execute("INSERT INTO md_state (key, value) VALUES ('a', '2')")
            async with self.db.transaction():
                await self.db.execute("INSERT INTO md_state (key, value) VALUES (?, '1')", (f"t{n}",))
        self.assertEqual(await self.rows("SELECT key, value FROM md_state ORDER BY key"),
                         [("a", "1"), ("t1", "1"), ("t2", "1")])

    def put_files(self) -> None:
        (self.lib / OLD).mkdir(parents=True)
        for f in FILES:
            (self.lib / OLD / f).write_bytes(b"x" * 100)

    async def test_trash_after_a_failed_statement_keeps_its_record(self):
        """A Trash after such a statement: its record and the index's
        forgetting both land."""
        from app import library
        from app.config import settings
        await self.seed_album()
        self.put_files()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.execute("INSERT INTO md_albums (key, unit_id) VALUES (?, 'u-x')", (OLD,))
        with mock.patch.object(library, "_refresh_plex", mock.AsyncMock()):
            result = await library.trash_paths([f"{OLD}/{FILES[0]}"], "test")
        self.assertEqual(result["trashed"], 1)
        self.assertEqual(len(list(settings.trash_dir.rglob(FILES[0]))), 1)
        self.assertEqual(len(await self.rows("SELECT id FROM library_trash")), 1)
        self.assertEqual(await self.rows("SELECT path FROM library_index"), [(f"{OLD}/{FILES[1]}",)])

    async def test_trash_keeps_its_record_when_the_index_update_fails(self):
        """The files are in Trash before the index learns it: a failure
        there leaves Trash's record (what the Trash list and Restore read),
        not files in Trash nothing lists."""
        from app import library, relocate
        from app.config import settings
        await self.seed_album()
        self.put_files()
        with mock.patch.object(relocate, "bump", mock.AsyncMock(side_effect=Boom())), \
                mock.patch.object(library, "_refresh_plex", mock.AsyncMock()), \
                self.assertRaises(Boom):
            await library.trash_paths([f"{OLD}/{FILES[0]}"], "test")
        self.assertFalse((self.lib / OLD / FILES[0]).exists())
        self.assertEqual(len(list(settings.trash_dir.rglob(FILES[0]))), 1)
        self.assertEqual(await self.rows("SELECT original_path FROM library_trash"),
                         [(f"{OLD}/{FILES[0]}",)])
        self.assertEqual(len((await library.list_trash())["batches"]), 1)
        # the index forgot it whole, or not at all
        self.assertEqual(len(await self.rows("SELECT path FROM library_index")), 2)

    async def test_a_move_committed_while_the_registry_reads_keeps_the_record(self):
        """The apply loop's move commits while sync_registry reads the
        units: the registry reads them and writes in one transaction, so it
        sees the album where the move took it, pin and all, not gone."""
        from app import relocate
        uid = await self.seed_album()
        await self.db.execute("UPDATE md_albums SET manual = 1, release_mbid = 'rel-post' "
                              "WHERE key = ?", (OLD,))
        pairs = [(f"{OLD}/{f}", f"{NEW}/{f}") for f in FILES]
        real = units.all_units
        mover = {}

        async def all_units():
            # started as the registry reads: it waits for the database
            mover["task"] = asyncio.create_task(relocate.relocate(pairs, units={OLD: NEW}))
            return await real()

        with mock.patch.object(units, "all_units", all_units):
            await asyncio.wait_for(units.sync_registry(), 10)
        await asyncio.wait_for(mover["task"], 10)
        self.assertEqual(await self.rows("SELECT key, unit_id, manual, release_mbid FROM md_albums"),
                         [(NEW, uid, 1, "rel-post")])
        self.assertEqual(await self.rows("SELECT album_key, state FROM md_issues"), [(NEW, "open")])
        # and a registry that reads after the move finds the same
        await units.sync_registry()
        self.assertEqual(await self.rows("SELECT key, unit_id FROM md_albums"), [(NEW, uid)])

    async def test_an_album_following_its_files_moves_whole_or_not_at_all(self):
        """units.follow: the record takes its files' new key, and its
        issues and journal with it, in one transaction."""
        uid = await self.seed_album()
        # renamed by hand: the files are at NEW, still stamped with the album
        await self.db.execute("DELETE FROM library_index")
        await self.db.executemany(
            "INSERT INTO library_index (norm_artist, norm_title, path, added_at, dirpath, "
            "artist_dir, path_key, unit_id, album, album_artist, title) "
            "VALUES ('paul kelly', ?, ?, 'x', ?, 'Paul Kelly', ?, ?, 'Post', 'Paul Kelly', ?)",
            [(f, f"{NEW}/{f}", NEW, path_key(f"{NEW}/{f}"), uid, f) for f in FILES])
        await self.db.execute(
            "INSERT INTO md_changes (batch_id, target, ref, field, summary, applied_at, "
            "artist_dir, album_key) VALUES ('b1', 'file', ?, 'tags', 'x', 'x', 'Paul Kelly', ?)",
            (f"{OLD}/{FILES[0]}", OLD))
        tables = ("md_albums", "md_issues", "md_issue_refs", "md_changes")
        before = await snapshot(self.db, *tables)
        real = self.db.execute

        async def execute(sql, params=()):
            if sql.startswith("UPDATE md_issue_refs"):     # repoint's last table
                raise Boom()
            return await real(sql, params)

        with mock.patch.object(self.db, "execute", execute), self.assertRaises(Boom):
            await units.follow(OLD)
        self.assertEqual(await snapshot(self.db, *tables), before)
        self.assertEqual(await units.follow(OLD), NEW)
        self.assertEqual(await self.rows("SELECT key, unit_id FROM md_albums"), [(NEW, uid)])
        for table in ("md_issues", "md_issue_refs", "md_changes"):
            self.assertEqual(await self.rows(f"SELECT DISTINCT album_key FROM {table}"), [(NEW,)],
                             table)


class IndexTestCase(DbTestCase):

    async def plan(self, sql: str, params: tuple) -> list[str]:
        return [r["detail"] for r in await self.db.fetchall(f"EXPLAIN QUERY PLAN {sql}", params)]

    async def test_the_journal_lookups_use_an_index(self):
        from app.metadata import apply
        cases = {
            "ours_locked": (apply.OURS_LOCKED, ("123", "genre")),
            "current_path": (apply.CURRENT_PATH, ("A/x/01.mp3",)),
            "oscillation": (apply.OSCILLATION, ("2026-10-19T00:00:00Z",)),
            "match_flipflop": (issues.MATCH_FLIPFLOP, ("123", "2026-10-19T00:00:00Z")),
            "changed_by_us": (issues.CHANGED_BY_US, ("123", *issues.SHARED_FIELDS)),
        }
        for name, (sql, params) in cases.items():
            with self.subTest(query=name):
                plan = await self.plan(sql, params)
                self.assertTrue(any("md_changes USING" in d and "INDEX idx_changes_target_"
                                    in d for d in plan), plan)
                self.assertFalse(any(d.startswith("SCAN md_changes") for d in plan), plan)

    async def test_a_download_follows_its_file_by_its_key(self):
        """relocate's tracks.library_path update is by track id since P1-F3
        (the lookup before it matches any spelling, through path_key)."""
        from app import relocate
        plan = await self.plan(relocate.TRACK_PATH, ("A/x/01.mp3", "t1"))
        self.assertTrue(any(d.startswith("SEARCH tracks USING") for d in plan), plan)

    async def test_v14_is_an_index_migration_a_second_start_leaves_alone(self):
        from app import db as dbmod
        self.assertGreaterEqual(len(dbmod.MIGRATIONS), 14)
        names = {r["name"] for r in await self.db.fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'md_changes'")}
        self.assertLessEqual({"idx_changes_target_ref", "idx_changes_target_field_at"}, names)
        await self.db.close()
        await self.db.connect()
        version = await self.db.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
        self.assertEqual(version["v"], len(dbmod.MIGRATIONS))


if __name__ == "__main__":
    unittest.main()
