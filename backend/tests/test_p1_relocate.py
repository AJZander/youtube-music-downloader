"""One relocate service for every path change: after any mover (an import,
a layout move, a merge of copies, filing a download, an undo, Trash and
Restore, an artist merge) every path every table holds still names a file
or folder the index knows, in any case (the share ignores it), or one that
is in Trash.

Six tables hold paths and each mover updated a different subset of them:
11% of completed downloads named a path no longer indexed, and 236 index
rows lost their video id through a case mismatch (review 2026-11, P1-F3).

Run with: python -m unittest discover backend/tests"""
import json
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.engine import tagio
from app.identity import path_key
from app.metadata import units
from test_attention import DbTestCase
from test_metadata import HAVE_FFMPEG, make_audio
from test_p0_oscillation import KEEP, ORIG, SONGS
from test_p1_unit_id import RIP, UnitTestCase

OTHER = "Nobody/(2000) Elsewhere"
OTHER_FILE = f"{OTHER}/01 - Elsewhere.mp3"
OPEN = ("open", "queued", "failed")


async def dangling(db) -> list[tuple[str, str]]:
    """Every reference a table holds to a file or folder that the index
    doesn't know, in any case (identity.path_key), and that isn't in Trash:
    (table, value) pairs. A file is in Trash when it, or a folder above it,
    is held there; a folder when Trash holds what was in it.

    Also a completed download whose indexed file has lost the video id it
    came from (the index row was dropped and read again as a new file)."""
    from app.metadata.issues import MERGE_KEEPER, UNIT_KINDS
    from app.models import ACTIVE_JOB_STATUSES

    def ancestors(key: str) -> list[str]:
        parts = key.split("/")
        return ["/".join(parts[:i]) for i in range(1, len(parts))]

    files: dict[str, str | None] = {}
    folders: set[str] = set()
    for r in await db.fetchall("SELECT path, provider_track_id FROM library_index"):
        key = path_key(r["path"])
        files[key] = r["provider_track_id"]
        folders.update(ancestors(key))
    trashed: set[str] = set()
    trashed_from: set[str] = set()
    for r in await db.fetchall(
            "SELECT original_path FROM library_trash WHERE restored_at IS NULL"):
        key = path_key(r["original_path"])
        trashed.add(key)
        trashed_from.update(ancestors(key))

    def in_trash(key: str) -> bool:
        return key in trashed or any(a in trashed for a in ancestors(key))

    def file_ok(path: str) -> bool:
        key = path_key(path)
        return key in files or in_trash(key)

    def folder_ok(path: str) -> bool:
        key = path_key(path)
        return key in folders or in_trash(key) or (key in trashed_from and key not in folders)

    out: list[tuple[str, str]] = []

    def check(table: str, value: str | None, ok) -> None:
        if value and not ok(value):
            out.append((table, value))

    for r in await db.fetchall(
            "SELECT library_path, provider_track_id FROM tracks "
            "WHERE status = 'completed' AND library_path IS NOT NULL ORDER BY id"):
        check("tracks.library_path", r["library_path"], file_ok)
        key = path_key(r["library_path"])
        if r["provider_track_id"] and key in files and files[key] is None:
            out.append(("library_index.provider_track_id", r["library_path"]))
    unit_ids = set()
    for r in await db.fetchall("SELECT key, dirpath, unit_id FROM md_albums ORDER BY key"):
        unit_ids.add(r["unit_id"])
        check("md_albums.key", units.unit_folder(r["key"]), folder_ok)
        check("md_albums.dirpath", r["dirpath"], folder_ok)
    marks = ",".join("?" * len(UNIT_KINDS))
    for r in await db.fetchall(
            f"SELECT album_key FROM md_issues WHERE state IN {OPEN} AND kind IN ({marks}) "
            f"AND album_key IS NOT NULL ORDER BY id", UNIT_KINDS):
        check("md_issues.album_key", units.unit_folder(r["album_key"]), folder_ok)
    for r in await db.fetchall(
            f"SELECT r.album_key FROM md_issue_refs r JOIN md_issues i ON i.id = r.issue_id "
            f"WHERE i.state IN {OPEN} AND r.album_key IS NOT NULL ORDER BY i.id"):
        check("md_issue_refs.album_key", units.unit_folder(r["album_key"]), folder_ok)
    for r in await db.fetchall("SELECT scope, key_a, key_b FROM library_exclusions "
                               "WHERE scope IN ('album', 'track') ORDER BY id"):
        ok = folder_ok if r["scope"] == "album" else file_ok
        check(f"library_exclusions.{r['scope']}", r["key_a"], ok)
        check(f"library_exclusions.{r['scope']}", r["key_b"], ok)
    statuses = tuple(str(s) for s in ACTIVE_JOB_STATUSES)
    for r in await db.fetchall(
            f"SELECT target_dir FROM jobs WHERE target_dir IS NOT NULL "
            f"AND status IN ({','.join('?' * len(statuses))}) ORDER BY id", statuses):
        check("jobs.target_dir", r["target_dir"], folder_ok)
    for r in await db.fetchall(
            "SELECT ref FROM md_overrides WHERE entity = 'album' ORDER BY ref"):
        if r["ref"] not in unit_ids:
            out.append(("md_overrides.ref", r["ref"]))
    for r in await db.fetchall("SELECT key FROM md_state WHERE substr(key, 1, ?) = ?",
                               (len(MERGE_KEEPER), MERGE_KEEPER)):
        check("md_state.merge_keeper", r["key"][len(MERGE_KEEPER):], folder_ok)
    return out


class RelocateTestCase(UnitTestCase):
    """UnitTestCase, plus a reference in every path table to the albums
    under test, and the importer as the worker calls it."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.write(OTHER_FILE, seconds=2.0, title="Elsewhere", artist="Nobody",
                   albumartist="Nobody", album="Elsewhere", tracknumber="1")
        self.staging = self.tmp / "staging"
        self.staging.mkdir()
        self.serial = 0

    def next_id(self, prefix: str) -> str:
        self.serial += 1
        return f"{prefix}{self.serial}"

    async def job(self, *, status="queued", target_dir=None,
                  source_url="https://music.youtube.com/browse/MPREb_test") -> str:
        job_id = self.next_id("j")
        await self.db.execute(
            "INSERT INTO jobs (id, source_url, provider, source_type, status, target_dir, "
            "created_at) VALUES (?, ?, 'youtube_music', 'album', ?, ?, '2026-11-02T00:00:00Z')",
            (job_id, source_url, status, target_dir))
        return job_id

    async def downloaded(self, job_id: str, path: str, *, spelled: str | None = None) -> str:
        """A completed track of the job, recorded at `spelled` (the path as
        the importer wrote it) or `path`; the index row has its video id."""
        video = self.next_id("vid")
        await self.db.execute(
            "INSERT INTO tracks (id, job_id, position, provider_track_id, status, library_path, "
            "created_at) VALUES (?, ?, 1, ?, 'completed', ?, '2026-11-02T00:00:00Z')",
            (self.next_id("t"), job_id, video, spelled or path))
        await self.db.execute("UPDATE library_index SET provider_track_id = ? WHERE path = ?",
                              (video, path))
        return video

    async def files_in(self, folder: str) -> list[str]:
        return [r["path"] for r in await self.db.fetchall(
            "SELECT path FROM library_index WHERE dirpath = ? ORDER BY path", (folder,))]

    @staticmethod
    def queued_after():
        """The fixes run as if seed_refs' Download missing was queued once
        they had begun: one queued before keeps its album as it is until it
        ends (P1-F4, test_p1_settling), and this one's folder must follow."""
        from app import locks
        return mock.patch.object(locks, "settling", mock.AsyncMock(return_value={}))

    async def seed_refs(self, folder: str, *, keeper: bool = False) -> None:
        """Something in every path table that names this album folder or its
        files: its downloads (the first as the importer spelled it, in
        another case), a Download missing queued into it, "not duplicates"
        decisions about it and its first file, an override on its unit, and
        (`keeper`) the hold a merge of copies leaves on its folder."""
        from app import library
        paths = await self.files_in(folder)
        self.assertTrue(paths, folder)
        done = await self.job(status="completed")
        for i, path in enumerate(paths):
            await self.downloaded(done, path, spelled=path.upper() if i == 0 else None)
        await self.job(target_dir=folder)
        await library.add_exclusions("album", [folder, OTHER], "test")
        await library.add_exclusions("track", [paths[0], OTHER_FILE], "test")
        record = await self.record(folder)
        if record:
            await self.db.execute(
                "INSERT INTO md_overrides (entity, ref, field, value, created_at) "
                "VALUES ('album', ?, 'genres', '[\"Pop\"]', 'x')", (record["unit_id"],))
        if keeper:
            await self.db.execute(
                "INSERT INTO md_state (key, value) VALUES (?, ?)",
                (f"merge_keeper:{folder.casefold()}", json.dumps({"at": 0, "batch": "x"})))
        self.assertEqual(await dangling(self.db), [])

    async def import_one(self, *, artist: str, album: str, title: str, number: int,
                         year: str = "2014", target_dir: str | None = None,
                         job_id: str | None = None) -> str:
        """A downloaded track through the importer, then indexed and
        recorded as the worker does (worker.py, the import step)."""
        from app import importer, library_index
        source = self.staging / f"{self.next_id('s')}.mp3"
        make_audio(source, 2.0 + number)
        tagio.write(source, {"title": [title], "artist": [artist], "albumartist": [artist],
                             "album": [album], "tracknumber": [str(number)], "date": [year]})
        validated = {"tags": {"title": title, "artist": artist, "album": album,
                              "album_artist": artist, "track_number": number,
                              "release_date": year},
                     "duration_sec": 2.0 + number, "format": "mp3", "path": source}
        with mock.patch.object(importer.lyrics_mod, "fetch_synced",
                               mock.AsyncMock(return_value=None)):
            result = await importer.import_track(source, validated, {}, target_dir=target_dir)
        meta = result["meta"]
        video = self.next_id("vid")
        await library_index.add_entry(result["library_path"], meta["artist"], meta["title"],
                                      meta["album"], "mp3", 2.0 + number, video)
        await self.db.execute(
            "INSERT INTO tracks (id, job_id, position, provider_track_id, status, library_path, "
            "created_at) VALUES (?, ?, ?, ?, 'completed', ?, '2026-11-02T00:00:00Z')",
            (self.next_id("t"), job_id or await self.job(status="completed"), number, video,
             result["library_path"]))
        return result["library_path"]

    def album(self, folder: str, artist: str, album: str, titles: list[str]) -> None:
        for n, title in enumerate(titles, start=1):
            self.write(f"{folder}/{n:02d} - {title}.mp3", seconds=2.0 + n, title=title,
                       artist=artist, albumartist=artist, album=album, tracknumber=str(n))

    async def latest_batch(self, summary_like: str = "%") -> str:
        return (await self.db.fetchone(
            "SELECT batch_id FROM md_changes WHERE summary LIKE ? AND reverted_at IS NULL "
            "ORDER BY id DESC LIMIT 1", (summary_like,)))["batch_id"]

    def thriller_rip(self) -> None:
        for n, rec in enumerate(["r1", "r4", "r2", "r3"], start=1):
            self.thriller_file(RIP, f"{n} {SONGS[rec][0]}.mp3", rec, n)

    def celebrity(self) -> None:
        """Old's two albums, one of them also in New's folder (one copy of
        its first track there already)."""
        self.album("Old/(2001) Celebrity", "Old", "Celebrity", ["Pop", "Celebrity"])
        self.album("Old/(1999) Early", "Old", "Early", ["First Song"])
        self.album("New/(2001) Celebrity", "New", "Celebrity", ["Pop"])
        (self.lib / "Old/(1999) Early/folder.jpg").write_bytes(b"art")


# ------------------------------------------------------------------ every mover
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class PathsResolveTestCase(RelocateTestCase):

    async def test_after_an_import(self):
        # the share: "Sam Smith/" is the "SAM SMITH/" folder (a scan sees it once)
        self.album("SAM SMITH/(2014) In the Lonely Hour", "Sam Smith", "In the Lonely Hour",
                   ["Money on My Mind"])
        os.symlink("SAM SMITH", self.lib / "Sam Smith")
        await self.rescan()
        await self.seed_refs("SAM SMITH/(2014) In the Lonely Hour")
        await self.import_one(artist="Sam Smith", album="In the Lonely Hour",
                              title="Stay With Me", number=2)
        self.assertEqual(await dangling(self.db), [])
        await self.rescan()
        self.assertEqual(await dangling(self.db), [])

    async def test_after_a_move(self):
        from app import library
        self.thriller_rip()
        await library.scan(full=True)
        with self.services(), self.queued_after():
            await self.check_all()
            await self.seed_refs(RIP)
            move = await self.open_issue("move", RIP)
            await self.apply_kinds(RIP, ("move",))
            self.assertEqual((await self.issue(move["id"]))["state"], "applied")
        self.assertFalse((self.lib / RIP).exists())
        self.assertEqual(await dangling(self.db), [])

    async def test_after_a_merge_of_copies(self):
        await self.make_thriller()
        with self.services(), self.queued_after():
            await self.check_all()
            await self.seed_refs(ORIG)
            merged = await self.merge()
        self.assertEqual(merged["state"], "applied", merged["error"])
        self.assertFalse((self.lib / ORIG).exists())
        self.assertEqual(await dangling(self.db), [])
        # the copy's record joined the keeper's, decisions and all
        self.assertIsNone(await self.record(ORIG))
        keeper = await self.record(KEEP)
        overrides = await self.db.fetchall("SELECT ref FROM md_overrides")
        self.assertEqual([r["ref"] for r in overrides], [keeper["unit_id"]])

    async def test_after_filing_in_downloads(self):
        from app.metadata import apply
        home, landed = "Paul Kelly/(1985) Post", "Paul Kelly/Post (Deluxe)"
        self.album(home, "Paul Kelly", "Post", ["Post", "From St Kilda to Kings Cross"])
        for n, title in ((3, "Incident on South Dowling"), (4, "Little Aches and Pains")):
            self.write(f"{landed}/{n:02d} - {title}.mp3", seconds=2.0 + n, title=title,
                       artist="Paul Kelly", albumartist="Paul Kelly", album="Post (Deluxe)",
                       tracknumber=str(n))
        await self.rescan()
        fetched = await self.job(status="completed", target_dir=home)
        for path in await self.files_in(landed):
            await self.downloaded(fetched, path)
        await self.seed_refs(landed)
        ctx = apply.Ctx(home, await units.load_unit(home), None, "Paul Kelly",
                        await units.unit_id_of(home))
        filed, _ = await apply._file_in_downloads({"detail": {"download": {"job_id": fetched}}},
                                                  ctx)
        self.assertEqual(filed, 2)
        self.assertFalse((self.lib / landed).exists())
        self.assertEqual(await dangling(self.db), [])

    async def test_after_an_undo(self):
        from app import library
        from app.metadata import apply
        self.thriller_rip()
        await library.scan(full=True)
        with self.services(), self.queued_after():
            await self.check_all()
            await self.seed_refs(RIP)
            await self.apply_kinds(RIP, ("move",))
        result = await apply.revert(await self.latest_batch("Moved %"))
        self.assertFalse(result["failures"], result)
        self.assertTrue((self.lib / RIP).is_dir())
        self.assertEqual(await dangling(self.db), [])
        self.assertIsNotNone(await self.record(RIP))

    async def test_after_trash_and_restore(self):
        from app import library
        folder = "Hilltop Hoods/(2003) The Calling"
        self.album(folder, "Hilltop Hoods", "The Calling", ["The Calling", "Dumb Enough",
                                                            "Testimonial Year"])
        await self.rescan()
        await self.seed_refs(folder)
        paths = await self.files_in(folder)
        result = await library.trash_paths(paths, reason="test")
        self.assertEqual(result["failures"], [])
        self.assertFalse((self.lib / folder).exists())
        self.assertEqual(await dangling(self.db), [])
        restored = await library.restore_batch(result["batch_id"])
        self.assertEqual(restored["failures"], [])
        self.assertEqual(await dangling(self.db), [])
        self.assertEqual(await self.files_in(folder), paths)

    async def test_after_an_artist_merge(self):
        from app import library
        self.celebrity()
        await self.rescan()
        await self.seed_refs("Old/(2001) Celebrity", keeper=True)
        await self.seed_refs("Old/(1999) Early")
        result = await library.merge_artist_folders(["Old"], "New")
        self.assertEqual(result["failures"], [])
        self.assertFalse((self.lib / "Old").exists())
        self.assertEqual(await dangling(self.db), [])

    async def test_any_sequence_of_movers_keeps_every_path_resolving(self):
        for seed in (20261102, 7, 1984):
            with self.subTest(seed=seed):
                await self.reset()
                await MoverSequence(self, random.Random(seed)).run(12)

    async def reset(self) -> None:
        """A fresh database and library, for another sequence."""
        await self.asyncTearDown()
        await self.asyncSetUp()


class MoverSequence:
    """Two artists, three albums, each named in every path table, then
    random movers one after another, the paths checked after each."""

    ARTISTS = {"Alpha": {"(2001) First": ["One", "Two", "Three"], "(2003) Second": ["Four", "Five"]},
               "Beta": {"(2010) Third": ["Six", "Seven"]}}

    def __init__(self, case: RelocateTestCase, rng: random.Random):
        self.case, self.rng = case, rng
        self.undoable: str | None = None
        self.trash: list[str] = []
        self.log: list[str] = []

    async def run(self, steps: int) -> None:
        case = self.case
        for artist, albums in self.ARTISTS.items():
            for folder, titles in albums.items():
                case.album(f"{artist}/{folder}", artist, folder.split(") ", 1)[1], titles)
        await case.rescan()
        for artist, albums in self.ARTISTS.items():
            for folder in albums:
                await case.seed_refs(f"{artist}/{folder}", keeper=folder == "(2010) Third")
        ops = [self.move, self.undo, self.trash_one, self.restore, self.merge_artists,
               self.import_one, self.file_download, self.cycle]
        for step in range(steps):
            while True:
                op = self.rng.choice(ops)
                note = await op(step)
                if note:
                    break
            self.log.append(note)
            found = await dangling(case.db)
            case.assertEqual(found, [], "after " + " → ".join(self.log))

    async def albums(self) -> dict[str, list[dict]]:
        """Album (unit key: a folder holding files of two albums, as a
        filed download's are until they are tagged, is two) -> its index
        rows; not the bystander's."""
        rows = [dict(r) for r in await self.case.db.fetchall(
            f"SELECT {units.UNIT_COLUMNS} FROM library_index WHERE dirpath IS NOT NULL")]
        return {key: members for key, members in sorted(units.build_units(rows).items())
                if units.unit_folder(key) != OTHER and "/" in key}

    async def move(self, step: int) -> str | None:
        from app.metadata import apply
        albums = await self.albums()
        if not albums:
            return None
        key = self.rng.choice(list(albums))
        artist, leaf = units.unit_folder(key).split("/", 1)
        new = f"{artist}/{leaf.upper() if leaf != leaf.upper() else leaf.title()}" \
            if self.rng.random() < 0.3 else f"{artist}/{leaf} ({step})"
        rows = albums[key]
        ctx = apply.Ctx(key, rows, {"folder": new, "tracks": {}}, artist,
                        await units.unit_id_of(key))
        moves = [{"from": r["path"], "to": f"{new}/{Path(r['path']).name}"} for r in rows]
        batch = ctx.new_batch()
        await apply._relocate(ctx, moves, batch)
        self.undoable = batch
        return f"move {key} to {new}"

    async def undo(self, step: int) -> str | None:
        from app.metadata import apply
        if not self.undoable:
            return None
        result = await apply.revert(self.undoable)
        self.case.assertFalse(result["failures"], result)
        self.undoable = None
        return "undo"

    async def trash_one(self, step: int) -> str | None:
        from app import library
        albums = {f: rows for f, rows in (await self.albums()).items() if len(rows) > 1}
        if not albums:
            return None
        path = self.rng.choice(albums[self.rng.choice(list(albums))])["path"]
        result = await library.trash_paths([path], reason="test")
        self.trash.append(result["batch_id"])
        self.undoable = None
        return f"trash {path}"

    async def restore(self, step: int) -> str | None:
        from app import library
        if not self.trash:
            return None
        batch = self.trash.pop(self.rng.randrange(len(self.trash)))
        result = await library.restore_batch(batch)
        self.case.assertEqual(result["failures"], [])
        self.undoable = None
        return "restore"

    async def merge_artists(self, step: int) -> str | None:
        from app import library
        artists = sorted({f.split("/", 1)[0] for f in await self.albums()})
        source = self.rng.choice(artists)
        others = [a for a in artists if a != source]
        target = self.rng.choice(others) if others and self.rng.random() < 0.5 else f"Gamma{step}"
        (self.case.lib / target).mkdir(exist_ok=True)
        result = await library.merge_artist_folders([source], target)
        self.case.assertEqual(result["failures"], [])
        self.undoable = result.get("batch_id")
        return f"merge {source} into {target}"

    async def import_one(self, step: int) -> str | None:
        albums = await self.albums()
        if not albums:
            return None
        folder = units.unit_folder(self.rng.choice(list(albums)))
        artist, leaf = folder.split("/", 1)
        year, _, name = leaf.partition(") ")
        await self.case.import_one(artist=artist.upper(), album=(name or leaf).upper(),
                                   title=f"New {step}", number=10 + step,
                                   year=year.strip("(") if name else "2020")
        self.undoable = None
        return f"import into {folder}"

    async def file_download(self, step: int) -> str | None:
        """A Download missing whose track landed in a folder of its own,
        filed into the album."""
        from app.metadata import apply
        albums = await self.albums()
        if not albums:
            return None
        key = self.rng.choice(list(albums))
        folder = units.unit_folder(key)
        artist = folder.split("/", 1)[0]
        fetched = await self.case.job(status="completed", target_dir=folder)
        await self.case.import_one(artist=artist, album=f"Elsewhere {step}", title=f"Got {step}",
                                   number=20 + step, job_id=fetched)
        ctx = apply.Ctx(key, await units.load_unit(key), None, artist,
                        await units.unit_id_of(key))
        filed, _ = await apply._file_in_downloads(
            {"detail": {"download": {"job_id": fetched}}}, ctx)
        self.case.assertEqual(filed, 1)
        self.undoable = None
        return f"file a download into {key}"

    async def cycle(self, step: int) -> str | None:
        await self.case.rescan()
        self.undoable = None
        return "scan"


# ------------------------------------------------------------------ the artist merge
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class ArtistMergeTestCase(RelocateTestCase):

    async def test_an_artist_merge_keeps_its_files_indexed_with_their_video_ids(self):
        from app import library
        self.celebrity()
        await self.rescan()
        job = await self.job(status="completed")
        videos = {}
        for folder in ("Old/(2001) Celebrity", "Old/(1999) Early"):
            for path in await self.files_in(folder):
                videos[path.replace("Old/", "New/", 1)] = await self.downloaded(job, path)
        result = await library.merge_artist_folders(["Old"], "New")
        self.assertEqual(result["failures"], [])
        moved = {p: v for p, v in videos.items() if not p.endswith("01 - Pop.mp3")}
        for path, video in moved.items():
            row = await self.db.fetchone("SELECT * FROM library_index WHERE path = ?", (path,))
            with self.subTest(path=path):
                self.assertIsNotNone(row)
                self.assertEqual(row["provider_track_id"], video)
                self.assertEqual(row["dirpath"], str(Path(path).parent))
                self.assertEqual(row["artist_dir"], "New")
                self.assertEqual(row["path_key"], path_key(path))
                # the album artist the merge wrote is what the index says
                self.assertEqual(row["album_artist"], "New")
        # nothing for a scan to do: no file read again, none added or lost
        snapshot = await library.scan(full=False)
        self.assertEqual((snapshot["added"], snapshot["updated"], snapshot["removed"]), (0, 0, 0))

    async def test_an_artist_merge_can_be_undone(self):
        from app import library
        from app.metadata import apply
        self.celebrity()
        await self.rescan()
        before = {str(p.relative_to(self.lib)): tagio.read(p)["fields"]
                  for p in sorted(self.lib.rglob("*.mp3"))}
        result = await library.merge_artist_folders(["Old"], "New")
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["trashed"], 1)
        self.assertEqual(tagio.read(self.lib / "New/(1999) Early/01 - First Song.mp3")["fields"]
                         ["albumartist"], ["New"])
        changes = await self.db.fetchall(
            "SELECT target, field FROM md_changes WHERE batch_id = ?", (result["batch_id"],))
        self.assertEqual({(r["target"], r["field"]) for r in changes},
                         {("move", "file"), ("move", "leftover"), ("trash", "files"),
                          ("file", "tags")})
        undone = await apply.revert(result["batch_id"])
        self.assertFalse(undone["failures"], undone)
        after = {str(p.relative_to(self.lib)): tagio.read(p)["fields"]
                 for p in sorted(self.lib.rglob("*.mp3")) if ".mdl-trash" not in p.parts}
        self.assertEqual(after, before)
        self.assertTrue((self.lib / "Old/(1999) Early/folder.jpg").exists())
        self.assertFalse((self.lib / "New/(1999) Early").exists())
        self.assertEqual(await self.trashed(), set())
        self.assertEqual(await dangling(self.db), [])
        # the index has the files back where they are, as they are, and
        # nothing else: no row for the folder of copies the undo restored
        indexed = {r["path"] for r in await self.db.fetchall("SELECT path FROM library_index")}
        self.assertEqual(indexed, set(after))
        row = await self.db.fetchone("SELECT album_artist FROM library_index WHERE path = ?",
                                     ("Old/(1999) Early/01 - First Song.mp3",))
        self.assertEqual(row["album_artist"], "Old")

    async def test_an_album_trash_emptied_hands_nothing_to_the_one_beside_it(self):
        """A folder holding two albums by tag. One album's only file goes to
        Trash, then the artist is merged before the next registry pass: the
        other album's record follows its files, and keeps none of the
        decisions about the album that went (its pin, ignore, override)."""
        from app import library
        folder = "Old/(2005) Mixed"
        self.write(f"{folder}/01 - Song A.mp3", seconds=3.0, title="Song A", artist="Old",
                   albumartist="Old", album="Alpha", tracknumber="1")
        for n, title in ((1, "Song B"), (2, "Song C")):
            self.write(f"{folder}/0{n + 1} - {title}.mp3", seconds=3.0 + n, title=title,
                       artist="Old", albumartist="Old", album="Beta", tracknumber=str(n))
        (self.lib / "New").mkdir()
        await self.rescan()
        alpha, beta = await self.record(f"{folder}|alpha"), await self.record(f"{folder}|beta")
        self.assertTrue(alpha and beta)
        await self.db.execute("UPDATE md_albums SET manual = 1, release_mbid = 'rel-alpha', "
                              "ignored = 1 WHERE unit_id = ?", (alpha["unit_id"],))
        await self.db.execute(
            "INSERT INTO md_overrides (entity, ref, field, value, created_at) "
            "VALUES ('album', ?, 'album', '\"Alpha (Remastered)\"', 'x')", (alpha["unit_id"],))
        await library.trash_paths([f"{folder}/01 - Song A.mp3"], reason="test")
        result = await library.merge_artist_folders(["Old"], "New")
        self.assertEqual(result["failures"], [])
        self.assertEqual(await dangling(self.db), [])
        for when in ("after the merge", "after the next registry pass"):
            with self.subTest(when):
                rec = await self.record("New/(2005) Mixed")
                self.assertEqual(rec["unit_id"], beta["unit_id"])
                self.assertEqual((rec["manual"], rec["release_mbid"], rec["ignored"]),
                                 (0, None, 0))
                self.assertEqual(await self.db.fetchall(
                    "SELECT * FROM md_overrides WHERE ref = ?", (rec["unit_id"],)), [])
                self.assertIsNone(await self.db.fetchone(
                    "SELECT * FROM md_unit_aliases WHERE unit_id = ?", (beta["unit_id"],)))
            await self.rescan()
        # the registry drops the record of the album that went
        self.assertIsNone(await self.db.fetchone("SELECT * FROM md_albums WHERE unit_id = ?",
                                                 (alpha["unit_id"],)))


# ------------------------------------------------------------------ the importer
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class ImporterSpellingTestCase(RelocateTestCase):

    async def test_the_importer_uses_the_on_disk_spelling(self):
        self.album("SAM SMITH/(2014) In the Lonely Hour", "Sam Smith", "In the Lonely Hour",
                   ["Money on My Mind"])
        # a folder whose name only starts like the album's is another album
        self.album("SAM SMITH/(2014) In the Lonely Hour (Drowning Shadows Edition)", "Sam Smith",
                   "In the Lonely Hour (Drowning Shadows Edition)", ["Money on My Mind"])
        await self.rescan()
        path = await self.import_one(artist="Sam Smith", album="In the Lonely Hour",
                                     title="Stay With Me", number=2)
        self.assertEqual(path, "SAM SMITH/(2014) In the Lonely Hour/02 - Stay With Me.mp3")
        self.assertTrue((self.lib / path).exists())
        # an album the library doesn't have yet goes under the artist's folder
        later = await self.import_one(artist="Sam Smith", album="The Thrill of It All",
                                      title="Too Good at Goodbyes", number=1, year="2017")
        self.assertEqual(later, "SAM SMITH/(2017) The Thrill of It All/01 - Too Good at Goodbyes.mp3")
        self.assertEqual(sorted(p.name for p in self.lib.iterdir() if not p.name.startswith(".")),
                         ["Nobody", "SAM SMITH"])
        row = dict(await self.db.fetchone("SELECT * FROM library_index WHERE path = ?", (path,)))
        self.assertEqual(row["dirpath"], "SAM SMITH/(2014) In the Lonely Hour")
        self.assertEqual(row["artist_dir"], "SAM SMITH")
        self.assertEqual(row["path_key"], path_key(path))
        self.assertEqual((row["title"], row["album"], row["album_artist"], row["track_number"]),
                         ("Stay With Me", "In the Lonely Hour", "Sam Smith", 2))
        self.assertTrue(row["provider_track_id"])
        self.assertIsNotNone(row["scanned_at"])
        # and a scan has nothing to read again
        from app import library
        snapshot = await library.scan(full=False)
        self.assertEqual((snapshot["added"], snapshot["updated"], snapshot["removed"]), (0, 0, 0))


# ------------------------------------------------------------------ joins
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class CaseInsensitiveJoinTestCase(RelocateTestCase):

    async def test_a_case_only_difference_still_joins(self):
        from app import api
        from app.metadata import apply, missing
        folder = "Wiz Khalifa/(2011) Rolling Papers (Deluxe Version)"
        self.write(f"{folder}/04 - Roll Up.mp3", seconds=3.0, title="Roll Up",
                   artist="Wiz Khalifa", albumartist="Wiz Khalifa",
                   album="Rolling Papers (Deluxe Version)", tracknumber="4",
                   mb_albumid="rel-rp", mb_releasegroupid="rg-rp")
        await self.rescan()
        indexed = f"{folder}/04 - Roll Up.mp3"
        spelled = indexed.upper()
        job = await self.job(status="completed", target_dir="Wiz Khalifa/(2011) Rolling Papers")
        await self.db.execute(
            "INSERT INTO tracks (id, job_id, position, provider_track_id, status, library_path, "
            "album, created_at) VALUES ('t1', ?, 1, 'v1', 'completed', ?, "
            "'Rolling Papers (Deluxe Version)', 'x')", (job, spelled))
        await self.db.execute("UPDATE library_index SET provider_track_id = 'v1' WHERE path = ?",
                              (indexed,))
        with self.subTest("missing._album_download"):
            self.assertTrue(await missing._album_download([indexed]))
        with self.subTest("missing._browse_from_history"):
            self.assertEqual(await missing._browse_from_history("rel-rp"), "MPREb_test")
        with self.subTest("api._album_context"):
            await self.db.execute(
                "INSERT INTO md_albums (key, dirpath, artist_dir, releasegroup_mbid) "
                "VALUES ('Wiz Khalifa/(2011) Rolling Papers', 'Wiz Khalifa/(2011) Rolling Papers', "
                "'Wiz Khalifa', 'rg-rp')")
            await self.db.execute("UPDATE tracks SET status = 'skipped_duplicate'")
            rows = await self.db.fetchall("SELECT * FROM tracks")
            ctx = await api._album_context(rows)
            # found in the index: a sibling edition of the album wanted
            self.assertEqual(api._different_album(rows[0], ctx), "edition")
            await self.db.execute("UPDATE tracks SET status = 'completed'")
        with self.subTest("apply._file_in_downloads"):
            home = "Wiz Khalifa/(2011) Rolling Papers"
            self.write(f"{home}/01 - Rolling Papers.mp3", seconds=2.0, title="Rolling Papers",
                       artist="Wiz Khalifa", albumartist="Wiz Khalifa", album="Rolling Papers",
                       tracknumber="1")
            await self.rescan()
            ctx = apply.Ctx(home, await units.load_unit(home), None, "Wiz Khalifa",
                            await units.unit_id_of(home))
            filed, videos = await apply._file_in_downloads(
                {"detail": {"download": {"job_id": job}}}, ctx)
            self.assertEqual((filed, videos), (1, {"v1"}))
            self.assertTrue((self.lib / home / "04 - Roll Up.mp3").exists())
            self.assertEqual(await dangling(self.db), [])


# ------------------------------------------------------------------ folder names
class FolderPrefixTestCase(DbTestCase):
    """relocate renames what names a folder only for that folder and what is
    under it: `A/Album` is never `A/Album 2`, `A/Album (Deluxe)` or `A/Albums`."""

    async def test_a_folder_never_touches_one_that_starts_like_it(self):
        from app import library, relocate
        folders = ["A/Album", "A/Album 2", "A/Album (Deluxe)", "A/Albums", "A/Album|x"]
        for n, folder in enumerate(folders[:4]):
            path = f"{folder}/01.flac"
            await self.db.execute(
                "INSERT INTO library_index (norm_artist, norm_title, path, path_key, dirpath, "
                "artist_dir, added_at) VALUES ('a', 't', ?, ?, ?, 'A', 'x')",
                (path, path_key(path), folder))
            await self.db.execute(
                "INSERT INTO jobs (id, source_url, provider, source_type, status, target_dir, "
                "created_at) VALUES (?, 'u', 'youtube_music', 'album', 'queued', ?, 'x')",
                (f"j{n}", folder))
            await self.db.execute("INSERT INTO md_state (key, value) VALUES (?, '{}')",
                                  (f"merge_keeper:{folder.casefold()}",))
            await self.db.execute(
                "INSERT INTO plex_items (rating_key, type, path, folder, detail_at, seen_at) "
                "VALUES (?, 'track', ?, ?, 5, 0)", (str(n), path, folder))
        for key in folders:
            await self.db.execute("INSERT INTO md_albums (key, dirpath, artist_dir) "
                                  "VALUES (?, ?, 'A')", (key, units.unit_folder(key)))
        # A/Album's second file is the |x album's (A/Album's own: not stamped yet)
        await self.db.execute(
            "INSERT INTO library_index (norm_artist, norm_title, path, path_key, dirpath, "
            "artist_dir, added_at, unit_id) VALUES ('a', 't2', 'A/Album/02.flac', ?, 'A/Album', "
            "'A', 'x', (SELECT unit_id FROM md_albums WHERE key = 'A/Album|x'))",
            (path_key("A/Album/02.flac"),))
        await library.add_exclusions("album", ["A/Album 2", "A/Album (Deluxe)"], "test")
        await library.add_exclusions("album", ["A/Album", "A/Albums"], "test")
        await library.add_exclusions("track", ["A/Album/01.flac", "A/Albums/01.flac"], "test")
        await relocate.relocate([("A/Album/01.flac", "B/Album/01.flac"),
                                 ("A/Album/02.flac", "B/Album/02.flac")])

        async def column(sql: str) -> list:
            return sorted(tuple(r) if len(r) > 1 else r[0] for r in await self.db.fetchall(sql))

        self.assertEqual(await column("SELECT key FROM md_albums"),
                         sorted(["A/Album 2", "A/Album (Deluxe)", "A/Albums", "B/Album"]))
        self.assertEqual(await column("SELECT target_dir FROM jobs"),
                         sorted(["B/Album", "A/Album 2", "A/Album (Deluxe)", "A/Albums"]))
        self.assertEqual(await column("SELECT key FROM md_state WHERE key LIKE 'merge_keeper:%'"),
                         sorted(f"merge_keeper:{f.casefold()}"
                                for f in ["B/Album", "A/Album 2", "A/Album (Deluxe)", "A/Albums"]))
        self.assertEqual(await column("SELECT folder, detail_at FROM plex_items"),
                         [("A/Album", 0), ("A/Album (Deluxe)", 5), ("A/Album 2", 5),
                          ("A/Albums", 5)])
        # each pair in the order the duplicate finders look it up in
        self.assertEqual(await column("SELECT scope, key_a, key_b FROM library_exclusions"),
                         [("album", "A/Album (Deluxe)", "A/Album 2"),
                          ("album", "A/Albums", "B/Album"),
                          ("track", "A/Albums/01.flac", "B/Album/01.flac")])
        found = await library.load_exclusions()
        self.assertIn(library.pair_key("B/Album", "A/Albums"), found["album"])
        self.assertIn(library.pair_key("B/Album/01.flac", "A/Albums/01.flac"), found["track"])


class ExclusionOrderTestCase(DbTestCase):
    """A "not duplicates" decision is found however its pair is stored: a
    rename before relocate kept pairs in order left 705 of the 2,668 album
    pairs on the real data the other way round."""

    async def test_a_pair_stored_the_other_way_round_is_still_found(self):
        from app import library
        await self.db.execute(
            "INSERT INTO library_exclusions (scope, key_a, key_b, reason, created_at) "
            "VALUES ('album', 'M/Zulu', 'M/Zed', 'test', 'x')")
        found = await library.load_exclusions()
        self.assertEqual(found["album"], {library.pair_key("M/Zulu", "M/Zed")})

    async def test_a_followed_pair_is_kept_once_in_order(self):
        from app import relocate
        for a, b in (("M/Alpha", "M/Zed"), ("M/Zed", "M/Zulu")):
            await self.db.execute(
                "INSERT INTO library_exclusions (scope, key_a, key_b, reason, created_at) "
                "VALUES ('album', ?, ?, 'test', 'x')", (a, b))
        # M/Alpha becomes M/Zulu: its decision is the one M/Zulu has already
        await relocate.rekey_exclusions("album", "M/Alpha", "M/Zulu")
        rows = await self.db.fetchall("SELECT key_a, key_b FROM library_exclusions")
        self.assertEqual([tuple(r) for r in rows], [("M/Zed", "M/Zulu")])


# ------------------------------------------------------------------ the migration
class PathKeyMigrationTestCase(unittest.IsolatedAsyncioTestCase):
    """v13 on a database left at v12, through the real connect()."""

    PATHS = ["SAM SMITH/(2014) In the Lonely Hour/01 - Money on My Mind.m4a",
             "Beyoncé/(2003) Dangerously in Love/01 - Crazy in Love.flac",   # decomposed
             "Sigur Rós/(1999) Ágætis byrjun/01 - Intro.flac",
             "STRAßE/(2001) Groß/01 - Eins.mp3"]

    async def asyncSetUp(self):
        from app.config import settings
        from app.db import db
        self.tmp = Path(tempfile.mkdtemp())
        self.patch = mock.patch.object(settings, "data_dir", self.tmp / "data")
        self.patch.start()
        self.db = db

    async def asyncTearDown(self):
        await self.db.close()
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def at_v12(self) -> list[str]:
        from app import db as dbmod
        with mock.patch.object(dbmod, "MIGRATIONS", dbmod.MIGRATIONS[:12]):
            await self.db.connect()
        # more rows than one backfill chunk
        paths = self.PATHS + [f"Artist {i % 50}/Album {i}/{i:05d} - Track.mp3" for i in range(4500)]
        await self.db.executemany(
            "INSERT INTO library_index (norm_artist, norm_title, path, added_at) VALUES (?,?,?,?)",
            [("a", "t", p, "x") for p in paths])
        await self.db.close()
        return paths

    def inspect(self) -> tuple[int, set[str], set[str]]:
        conn = sqlite3.connect(self.tmp / "data" / "mdl.db")
        try:
            version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            columns = {r[1] for r in conn.execute("PRAGMA table_info(library_index)")}
            indexes = {r[1] for r in conn.execute("PRAGMA index_list(library_index)")}
        finally:
            conn.close()
        return version, columns, indexes

    async def test_v13_backfills_path_keys_atomically(self):
        from app import db as dbmod
        paths = await self.at_v12()
        # cut off after its work, before it is recorded: nothing stays
        real = dbmod.MIGRATIONS[12]

        async def cut_off(conn):
            await real(conn)
            raise RuntimeError("cut off")

        with mock.patch.object(dbmod, "MIGRATIONS", dbmod.MIGRATIONS[:12] + [cut_off]), \
                self.assertRaises(RuntimeError):
            await self.db.connect()
        await self.db.close()
        version, columns, indexes = self.inspect()
        self.assertEqual(version, 12)
        self.assertNotIn("path_key", columns)
        self.assertNotIn("idx_li_path_key", indexes)

        await self.db.connect()
        rows = await self.db.fetchall("SELECT path, path_key FROM library_index ORDER BY path")
        self.assertEqual(len(rows), len(paths))
        self.assertEqual({r["path"]: r["path_key"] for r in rows},
                         {p: path_key(p) for p in paths})
        self.assertEqual(path_key(self.PATHS[1]), "beyoncé/(2003) dangerously in love/"
                                                  "01 - crazy in love.flac")
        self.assertEqual(path_key(self.PATHS[3]), "strasse/(2001) gross/01 - eins.mp3")
        version, columns, indexes = self.inspect()
        self.assertEqual(version, 13)
        self.assertIn("idx_li_path_key", indexes)
        # the SQL function the joins use is the same key
        got = await self.db.fetchone("SELECT path_key(?) AS k", (self.PATHS[0],))
        self.assertEqual(got["k"], path_key(self.PATHS[0]))
        # a second start changes nothing
        await self.db.close()
        await self.db.connect()
        again = await self.db.fetchall("SELECT path, path_key FROM library_index ORDER BY path")
        self.assertEqual([tuple(r) for r in again], [tuple(r) for r in rows])
        self.assertEqual(self.inspect()[0], 13)


if __name__ == "__main__":
    unittest.main()
