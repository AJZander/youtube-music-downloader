"""A Plex mirror nothing acts on blindly: the full sync pages in a stable
order and drops rows only after a complete listing, a "Not in Plex" re-add
first asks Plex itself whether the files are missing, and Plex's trash is
never emptied while the library mount is offline.

Plex's unsorted paging let a full sync miss items and drop their rows; the
re-add then hid and re-added files Plex had all along (review 2026-11, 4.C).

Run with: python -m unittest discover backend/tests"""
import json
import shutil
import sys
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_attention import DbTestCase

ROOT = "/data/Music"
TRACKS = [str(k) for k in range(101, 108)]       # seven tracks


def track(key: str) -> dict:
    return {"ratingKey": key, "type": "track", "title": f"Song {key}", "guid": f"local://{key}",
            "parentRatingKey": "20", "grandparentRatingKey": "10", "index": int(key) - 100,
            "parentIndex": 1, "updatedAt": 100, "addedAt": 100,
            "Media": [{"Part": [{"file": f"{ROOT}/A/B/{key}.flac"}]}]}


class FakePlex:
    """The few read-only GETs a sync makes, answered from a list. Plex lists
    a section in no stable order unless asked to sort: unsorted, each page
    request sees the items rotated. `shift` makes the second page of a
    sorted listing start one item early, as when an item is added mid-sync:
    one item is listed twice and another not at all. `churn` has Plex drop
    track 102 and add 108 once the first page of tracks is out (an item Plex
    re-creates after a move): the count holds, but the later pages start one
    item on, so 104 is never listed."""

    PAGE = 3                         # Plex may answer with fewer than asked

    def __init__(self, tracks=TRACKS, shift=False, churn=False):
        self.items = {8: [{"ratingKey": "10", "type": "artist", "title": "A", "updatedAt": 100}],
                      9: [{"ratingKey": "20", "type": "album", "title": "B", "parentRatingKey": "10",
                           "updatedAt": 100}],
                      10: [track(k) for k in tracks]}
        self.shift = shift
        self.churn = churn
        self.calls: list[tuple[str, dict]] = []
        self.pages = 0

    def by_key(self, key):
        return next((i for items in self.items.values() for i in items
                     if i["ratingKey"] == key), None)

    async def get(self, path, **params):
        from app.plex import PlexError
        self.calls.append((path, params))
        if path == "/library/sections/5/all":
            items = self.items[int(params["type"])]
            start = int(params["X-Plex-Container-Start"])
            size = int(params["X-Plex-Container-Size"])
            if size == 0:
                return {"totalSize": len(items), "size": 0}
            if self.churn and int(params["type"]) == 10 and start > 0:
                self.churn = False
                items = self.items[10] = [i for i in items if i["ratingKey"] != "102"] + [track("108")]
            self.pages += 1
            if params.get("sort") == "id":
                order = sorted(items, key=lambda i: int(i["ratingKey"]))
                if self.shift and start == self.PAGE:
                    start -= 1
            else:
                turn = self.pages % len(items)
                order = items[turn:] + items[:turn]
            page = order[start:start + min(size, self.PAGE)]
            return {"totalSize": len(items), "size": len(page), "Metadata": page}
        if path.startswith("/library/metadata/"):
            found = [i for i in (self.by_key(k) for k in path.rsplit("/", 1)[1].split(",")) if i]
            if not found:
                raise PlexError(f"GET {path} -> 404")
            return {"Metadata": found}
        if path.endswith("/mood"):
            return {"Directory": []}
        if path.endswith("/prefs"):
            return {"Setting": []}
        raise PlexError(f"GET {path} -> 404")


class PlexTestCase(DbTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app.config import settings
        for p in (mock.patch.object(settings, "plex_token", "dummy-token"),
                  mock.patch.object(settings, "plex_section_id", 5),
                  mock.patch.object(settings, "plex_library_root", ROOT)):
            p.start()
            self.patches.append(p)


class PagingTestCase(PlexTestCase):
    async def seed_mirror(self, keys):
        from app.metadata import plexsync
        rows = [(k, "track", f"local://{k}", f"Song {k}", "20", "10", f"A/B/{k}.flac", "A/B")
                for k in keys]
        rows += [("10", "artist", None, "A", None, None, None, None),
                 ("20", "album", None, "B", "10", None, None, "A/B")]
        # read before, and unchanged since: a sync has no reason to read the pages again
        await self.db.executemany(
            "INSERT INTO plex_items (rating_key, type, guid, title, parent_key, grandparent_key, "
            "path, folder, updated_at, seen_at, detail_at) VALUES (?,?,?,?,?,?,?,?,100,1000,5000)",
            rows)
        for key, value in (("plex_last_full", "1000.0"), ("plex_last_sync", "1000.0"),
                           ("plex_detail_rev", plexsync.DETAIL_REV)):
            await self.db.execute("INSERT INTO md_state (key, value) VALUES (?, ?)", (key, value))

    async def mirrored(self) -> set[str]:
        return {r["rating_key"] for r in await self.db.fetchall(
            "SELECT rating_key FROM plex_items WHERE type = 'track'")}

    async def last_full(self) -> str:
        row = await self.db.fetchone("SELECT value FROM md_state WHERE key = 'plex_last_full'")
        return row["value"]

    async def test_iter_items_asks_for_a_stable_order(self):
        from app.plex import TRACK, PlexClient
        client = PlexClient()
        fake = FakePlex()
        with mock.patch.object(client, "get", fake.get):
            got = [i["ratingKey"] async for i in client.iter_items(TRACK)]
            filtered = [i["ratingKey"] async for i in client.iter_items(TRACK, **{"addedAt>>": 5})]
        pages = [params for path, params in fake.calls if params.get("X-Plex-Container-Size")]
        self.assertGreater(len(pages), 4)
        self.assertTrue(all(params.get("sort") == "id" for params in pages), pages)
        self.assertEqual(got, TRACKS)
        self.assertEqual(filtered, TRACKS)

    async def test_a_page_that_shifts_mid_sync_deletes_nothing(self):
        from app.metadata import plexsync
        await self.seed_mirror(TRACKS)
        fake = FakePlex(shift=True)
        with mock.patch.object(plexsync.plex, "get", fake.get):
            result = await plexsync.sync(full=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["removed"], 0)
        self.assertEqual(await self.mirrored(), set(TRACKS))
        # the listing came up short (six of seven), so the next sync lists
        # everything again
        self.assertEqual(await self.last_full(), "1000.0")

    async def test_a_complete_listing_still_drops_what_plex_deleted(self):
        from app.metadata import plexsync
        await self.seed_mirror([*TRACKS, "199"])
        fake = FakePlex()
        with mock.patch.object(plexsync.plex, "get", fake.get):
            result = await plexsync.sync(full=True)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(await self.mirrored(), set(TRACKS))
        self.assertNotEqual(await self.last_full(), "1000.0")

    async def test_an_add_and_a_delete_mid_sync_drop_no_live_row(self):
        from app.metadata import plexsync
        await self.seed_mirror(TRACKS)
        fake = FakePlex(churn=True)
        with mock.patch.object(plexsync.plex, "get", fake.get):
            result = await plexsync.sync(full=True)
        self.assertEqual(result["removed"], 0)
        # seven listed, as many as Plex counted, but not 104: it was asked
        # for by key, and Plex still has it
        asked = {k for path, _ in fake.calls if path.startswith("/library/metadata/")
                 for k in path.rsplit("/", 1)[1].split(",")}
        self.assertIn("104", asked)
        self.assertEqual(await self.mirrored(), {*TRACKS, "108"})
        self.assertEqual(await self.last_full(), "1000.0")
        # 102 was listed before Plex dropped it: the next sync drops its row
        fake = FakePlex(tracks=[*(k for k in TRACKS if k != "102"), "108"])
        with mock.patch.object(plexsync.plex, "get", fake.get):
            result = await plexsync.sync(full=True)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(await self.mirrored(), {*TRACKS, "108"} - {"102"})
        self.assertNotEqual(await self.last_full(), "1000.0")

    async def test_an_answer_without_a_count_deletes_nothing(self):
        # an empty or non-JSON body comes back as {}: no totalSize is no
        # count, not a count of 0
        from app.metadata import plexsync
        from app.plex import TRACK
        await self.seed_mirror(TRACKS)

        async def blank(path, **params):
            return {}
        with mock.patch.object(plexsync.plex, "get", blank):
            self.assertIsNone(await plexsync.plex.count(TRACK))
            result = await plexsync.sync(full=True)
        self.assertEqual(result["removed"], 0)
        self.assertEqual(await self.mirrored(), set(TRACKS))
        self.assertEqual(await self.last_full(), "1000.0")


class FolderViewTestCase(PlexTestCase):
    """plex.folder_files reads Plex's folder view, one level a request."""

    VIEW = {
        "": {"size": 2, "Directory": [
            {"key": "/library/sections/5/folder?parent=1", "title": "Snoop Dogg"},
            {"key": "/library/sections/5/folder?parent=2", "title": "Sia"}]},
        "1": {"size": 2, "Directory": [
            {"key": "/library/sections/5/folder?parent=3", "title": "(1993) Doggystyle"},
            {"key": "/library/sections/5/folder?parent=4", "title": "(1993) Doggystyle [2005 Reissue]"}]},
        "3": {"size": 3, "Metadata": [
            {"ratingKey": "301", "type": "track", "title": "Bathtub",
             "Media": [{"Part": [{"file": f"{ROOT}/Snoop Dogg/(1993) Doggystyle/01 - Bathtub.m4a"}]}]},
            {"ratingKey": "302", "type": "track", "title": "G Funk Intro",
             "Media": [{"Part": [{"file": f"{ROOT}/Snoop Dogg/(1993) Doggystyle/02 - G Funk Intro.m4a"}]}]}],
            "Directory": [{"key": "/library/sections/5/folder?parent=5", "title": "Scans"}]},
    }

    def client(self, view):
        from app.plex import PlexClient, PlexError
        client = PlexClient()
        calls = []

        async def get(path, **params):
            calls.append(params.get("parent", ""))
            if path != "/library/sections/5/folder" or str(params.get("parent", "")) not in view:
                raise PlexError(f"GET {path} -> 404")
            return view[str(params.get("parent", ""))]
        client.get = get
        return client, calls

    async def test_the_tracks_listed_in_a_folder(self):
        client, calls = self.client(self.VIEW)
        got = await client.folder_files("snoop dogg/(1993) Doggystyle")
        self.assertEqual(got, {"Snoop Dogg/(1993) Doggystyle/01 - Bathtub.m4a": "301",
                               "Snoop Dogg/(1993) Doggystyle/02 - G Funk Intro.m4a": "302"})
        self.assertEqual(calls, ["", "1", "3"])

    async def test_anything_it_cant_read_is_none_not_empty(self):
        client, _ = self.client(self.VIEW)
        # a folder Plex doesn't list, a Plex error, and an answer of another shape
        self.assertIsNone(await client.folder_files("Snoop Dogg/(2000) Tha Last Meal"))
        self.assertIsNone(await client.folder_files("Sia/(2016) This Is Acting"))
        view = dict(self.VIEW, **{"3": {"size": 1, "Track": [{"ratingKey": "301"}]}})
        client, _ = self.client(view)
        self.assertIsNone(await client.folder_files("Snoop Dogg/(1993) Doggystyle"))
        view = dict(self.VIEW, **{"3": {"size": 1, "Metadata": [
            {"ratingKey": "300", "type": "album", "key": "/library/metadata/300/children"}]}})
        client, _ = self.client(view)
        self.assertIsNone(await client.folder_files("Snoop Dogg/(1993) Doggystyle"))
        view = dict(self.VIEW, **{"4": {"size": 0}})
        client, _ = self.client(view)
        self.assertEqual(await client.folder_files("Snoop Dogg/(1993) Doggystyle [2005 Reissue]"), {})


class ReaddLiveTestCase(PlexTestCase):
    async def test_no_readd_without_a_live_miss(self):
        from app.metadata import apply
        path = unicodedata.normalize("NFD", "A/Bé/1.m4a")
        rows = [{"path": path, "dirpath": path.rsplit("/", 1)[0], "artist_dir": "A"}]
        issue = {"detail": {}, "created_at": "2026-01-01T00:00:00Z"}
        fake = mock.MagicMock(configured=True, folder_files=mock.AsyncMock())
        with mock.patch.object(apply, "plex", fake), \
                mock.patch.object(apply.plexsync, "refresh_items", mock.AsyncMock()) as refresh, \
                mock.patch.object(apply, "readd", mock.AsyncMock(return_value=1)) as readd:
            # the mirror doesn't list it, but Plex does (its own spelling)
            fake.folder_files.return_value = {unicodedata.normalize("NFC", "a/bé/1.M4A"): "77"}
            note = await apply.do_scan(issue, apply.Ctx("A/Bé", rows, None, "A"))
            readd.assert_not_awaited()
            refresh.assert_awaited_once_with(["77"])
            self.assertIn("mirror", note)
            fake.folder_files.assert_awaited_with(path.rsplit("/", 1)[0])
            # Plex can't be asked: nothing is hidden on the mirror's word
            # alone, but the folder is scanned, and the issue stays open
            fake.folder_files.return_value = None
            ctx = apply.Ctx("A/Bé", rows, None, "A")
            with self.assertRaises(apply.KeepOpen):
                await apply.do_scan(issue, ctx)
            readd.assert_not_awaited()
            self.assertTrue(ctx.plex_dirty)
            self.assertEqual(ctx.touched_folders, {path.rsplit("/", 1)[0]})
            # Plex really doesn't list it
            fake.folder_files.return_value = {"A/Bé/2.m4a": "78"}
            await apply.do_scan(issue, apply.Ctx("A/Bé", rows, None, "A"))
            readd.assert_awaited_once_with([path])

    async def test_a_folder_plex_never_listed_is_scanned_not_re_added(self):
        # an import whose partial scans hadn't landed an hour on: Plex's
        # folder view doesn't list the folder yet
        from app.metadata import apply
        rows = [{"path": "New Artist/(2026) New/01 - A.m4a", "dirpath": "New Artist/(2026) New",
                 "artist_dir": "New Artist"}]
        issue = {"detail": {}, "created_at": "2026-01-01T00:00:00Z"}
        fake = mock.MagicMock(configured=True, folder_files=mock.AsyncMock(return_value=None))
        ctx = apply.Ctx("New Artist/(2026) New", rows, None, "New Artist")
        with mock.patch.object(apply, "plex", fake), \
                mock.patch.object(apply.plexsync, "album_for_paths",
                                  mock.AsyncMock(return_value={"tracks": {}})), \
                mock.patch.object(apply, "readd", mock.AsyncMock(return_value=1)) as readd, \
                self.assertRaises(apply.KeepOpen):
            await apply.do_scan(issue, ctx)
        readd.assert_not_awaited()
        self.assertTrue(ctx.plex_dirty)
        self.assertEqual(ctx.touched_folders, {"New Artist/(2026) New"})
        # nor does a re-add a week later wait on this one
        self.assertIsNone(await self.db.fetchone(
            "SELECT 1 FROM md_state WHERE key = 'plex_readd:New Artist/(2026) New'"))

    async def test_empty_trash_waits_for_the_library_sentinel(self):
        from app.config import settings
        from app.metadata import apply
        (self.lib / "A" / "B").mkdir(parents=True)
        song = self.lib / "A" / "B" / "01 - Pop.m4a"
        song.write_bytes(b"audio")

        async def blip(*args, **kwargs):
            settings.library_sentinel.unlink(missing_ok=True)    # the mount went away mid-scan
            return True
        fake = mock.MagicMock(refresh_path=mock.AsyncMock(return_value=True),
                              wait_for_scan=mock.AsyncMock(side_effect=blip),
                              empty_trash=mock.AsyncMock())
        with mock.patch.object(apply, "plex", fake), self.assertRaises(apply.ApplyError):
            await apply.readd(["A/B/01 - Pop.m4a"])
        fake.empty_trash.assert_not_awaited()
        self.assertTrue(song.exists())
        self.assertFalse((song.parent / ".mdl-readd-01 - Pop.m4a").exists())
        self.assertIsNone(await self.db.fetchone(
            "SELECT 1 FROM md_state WHERE key = ?", (apply.READD_PENDING,)))

    async def test_files_a_lost_mount_keeps_hidden_are_put_back_later(self):
        from app.metadata import apply
        (self.lib / "A" / "B").mkdir(parents=True)
        song = self.lib / "A" / "B" / "01 - Pop.m4a"
        song.write_bytes(b"audio")
        hidden = song.parent / ".mdl-readd-01 - Pop.m4a"
        away = self.tmp / "share-away"

        async def share_drops(*args, **kwargs):
            # the share goes away mid-scan, leaving an empty mount point
            shutil.move(str(self.lib), str(away))
            self.lib.mkdir()
            return True
        fake = mock.MagicMock(refresh_path=mock.AsyncMock(return_value=True),
                              wait_for_scan=mock.AsyncMock(side_effect=share_drops),
                              empty_trash=mock.AsyncMock())
        with mock.patch.object(apply, "plex", fake), self.assertRaises(apply.ApplyError) as caught:
            await apply.readd(["A/B/01 - Pop.m4a"])
        fake.empty_trash.assert_not_awaited()
        self.assertNotIn("files put back", str(caught.exception))
        self.assertIn("still hidden", str(caught.exception))
        # the share comes back: the file is still hidden, and the record of it kept
        self.lib.rmdir()
        shutil.move(str(away), str(self.lib))
        self.assertTrue(hidden.exists())
        row = await self.db.fetchone("SELECT value FROM md_state WHERE key = ?",
                                     (apply.READD_PENDING,))
        self.assertEqual(json.loads(row["value"]), [["A/B/01 - Pop.m4a", "A/B/.mdl-readd-01 - Pop.m4a"]])
        # the next start puts it back
        self.assertEqual(await apply.restore_hidden(), 1)
        self.assertTrue(song.exists())
        self.assertFalse(hidden.exists())

    async def test_a_re_add_puts_an_earlier_ones_files_back_first(self):
        from app.metadata import apply
        (self.lib / "A" / "B").mkdir(parents=True)
        left = self.lib / "A" / "B" / ".mdl-readd-01 - Pop.m4a"
        left.write_bytes(b"audio")
        await self.db.execute("INSERT INTO md_state (key, value) VALUES (?, ?)", (
            apply.READD_PENDING, json.dumps([["A/B/01 - Pop.m4a", "A/B/.mdl-readd-01 - Pop.m4a"]])))
        other = self.lib / "A" / "B" / "02 - Rock.m4a"
        other.write_bytes(b"audio")
        fake = mock.MagicMock(refresh_path=mock.AsyncMock(return_value=True),
                              wait_for_scan=mock.AsyncMock(return_value=True),
                              empty_trash=mock.AsyncMock())
        with mock.patch.object(apply, "plex", fake):
            self.assertEqual(await apply.readd(["A/B/02 - Rock.m4a"]), 1)
        self.assertTrue((self.lib / "A" / "B" / "01 - Pop.m4a").exists())
        self.assertFalse(left.exists())
        self.assertTrue(other.exists())
        self.assertIsNone(await self.db.fetchone(
            "SELECT 1 FROM md_state WHERE key = ?", (apply.READD_PENDING,)))

    async def test_clean_ghosts_waits_for_the_library_sentinel(self):
        from app.config import settings
        from app.metadata import apply
        settings.library_sentinel.unlink()
        fake = mock.MagicMock(refresh_path=mock.AsyncMock(return_value=True),
                              refresh_all=mock.AsyncMock(return_value=True),
                              wait_for_scan=mock.AsyncMock(return_value=True),
                              empty_trash=mock.AsyncMock())
        with mock.patch.object(apply, "plex", fake), self.assertRaises(apply.ApplyError):
            await apply.clean_ghosts([{"detail": {"folder": "A/B", "paths": ["A/B/1.flac"]}}])
        fake.empty_trash.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
