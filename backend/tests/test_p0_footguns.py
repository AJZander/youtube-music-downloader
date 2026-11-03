"""Foot-guns: `mdl index --rebuild` wiped every album record, and the CLI
logged the Plex token.

Run with: python -m unittest discover backend/tests"""
import io
import logging
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import cli
from app.engine import tagio
from test_metadata import HAVE_FFMPEG, make_audio


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class RebuildTestCase(unittest.IsolatedAsyncioTestCase):
    """The rebuild deleted library_index and wrote back 8 columns with no
    dirpath; units.all_units reads only rows with a dirpath, so the next
    sync_registry dropped every md_albums row, Accept match and all."""

    FOLDER = "Paul Kelly/(1987) Under The Sun"

    async def asyncSetUp(self):
        from app import library
        from app.config import settings
        from app.db import db
        from app.metadata import units
        self.library, self.units = library, units
        self.tmp = Path(tempfile.mkdtemp())
        self.lib = self.tmp / "library"
        (self.lib / self.FOLDER).mkdir(parents=True)
        (self.lib / ".mdl-library-online").touch()
        self.patches = [mock.patch.object(settings, "data_dir", self.tmp / "data"),
                        mock.patch.object(settings, "library_dir", self.lib),
                        mock.patch.object(settings, "plex_token", "")]
        for p in self.patches:
            p.start()
        await db.connect()
        self.db = db
        path = make_audio(self.lib / self.FOLDER / "01 - Dumb Things.flac", 3.0)
        tagio.write(path, {"title": ["Dumb Things"], "artist": ["Paul Kelly"],
                           "album": ["Under The Sun"], "albumartist": ["Paul Kelly"],
                           "tracknumber": ["1"], "mb_albumid": ["rel-1"],
                           "mb_releasegroupid": ["rg-1"], "mb_trackid": ["rec-1"]})
        await library.scan(full=True)
        await units.sync_registry()
        # the user's Accept match
        await db.execute("UPDATE md_albums SET manual = 1, release_mbid = 'rel-1' WHERE key = ?",
                         (self.FOLDER,))

    async def asyncTearDown(self):
        await self.db.close()
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def index_rows(self):
        return [dict(r) for r in await self.db.fetchall(
            "SELECT path, dirpath, title, mb_albumid, mb_releasegroupid, mb_trackid "
            "FROM library_index ORDER BY path")]

    async def test_index_rebuild_keeps_every_album_record(self):
        with mock.patch.object(cli, "_app_running", return_value=False, create=True):
            self.assertEqual(await cli._cmd_index(True), 0)
        await self.units.sync_registry()
        album = await self.db.fetchone("SELECT * FROM md_albums WHERE key = ?", (self.FOLDER,))
        self.assertIsNotNone(album, "the album record was dropped")
        self.assertEqual((album["manual"], album["release_mbid"]), (1, "rel-1"))
        self.assertEqual(await self.index_rows(), [{
            "path": f"{self.FOLDER}/01 - Dumb Things.flac", "dirpath": self.FOLDER,
            "title": "Dumb Things", "mb_albumid": "rel-1", "mb_releasegroupid": "rg-1",
            "mb_trackid": "rec-1"}])

    async def test_rebuild_refuses_while_the_app_is_up(self):
        before = await self.index_rows()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            scan = mock.AsyncMock()
            with mock.patch.object(cli, "APP_PORT", port, create=True), \
                    mock.patch.object(self.library, "scan", scan):
                code = await cli._cmd_index(True)
        self.assertNotEqual(code, 0)
        scan.assert_not_awaited()
        self.assertEqual(await self.index_rows(), before)


class CliLoggingTestCase(unittest.TestCase):
    """httpx logs every request URL at INFO, and plex.request puts the token
    in the query string."""

    def setUp(self):
        root = logging.getLogger()
        saved = (root.handlers[:], root.level,
                 {n: logging.getLogger(n).level for n in ("httpx", "httpcore")})

        def restore():
            root.handlers[:], root.level = saved[0], saved[1]
            for name, level in saved[2].items():
                logging.getLogger(name).setLevel(level)
        self.addCleanup(restore)
        root.handlers = []          # as in a fresh CLI process

    def test_cli_logging_never_shows_the_plex_token(self):
        cli._setup_logging()
        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        handler.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(handler)
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
        with httpx.Client(transport=transport) as client:
            client.get("http://plex.invalid:32400/library/sections",
                       params={"X-Plex-Token": "SECRET"})
        self.assertNotIn("SECRET", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
