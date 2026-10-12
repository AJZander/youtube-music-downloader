"""Run with: python -m unittest discover backend/tests"""
import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import catalog, ownership
from app.catalog import (BACKGROUND, INTERACTIVE, CatalogError, base_title,
                         classify_release, relate)
from app.db import Database
from app.models import ErrorClass, JobStatus


def raw_album(title, tracks, *, artists=("Paul Kelly",), year="2025",
              playlist="OLAK5uy_test"):
    """A get_album response, trimmed to the fields ytmusicapi really returns."""
    return {
        "title": title, "type": "Album", "year": year,
        "artists": [{"name": a, "id": f"UC{a[:2]}" + "x" * 20} for a in artists],
        "thumbnails": [{"url": "small", "width": 60, "height": 60},
                       {"url": "big", "width": 544, "height": 544}],
        "duration_seconds": 100 * len(tracks), "audioPlaylistId": playlist,
        "isExplicit": False,
        "tracks": [{
            "videoId": vid, "title": t, "isAvailable": vid is not None,
            "artists": [{"name": a} for a in (extra or artists)],
            "trackNumber": None if vid is None else n,
            "duration_seconds": 100, "isExplicit": False,
            "feedbackTokens": {"remove": "x" * 90},
        } for n, (vid, t, extra) in enumerate(tracks, start=1)],
    }


def release(browse_id, title, category, tracks=None):
    return {"browse_id": browse_id, "title": title, "category": category,
            "tracks": [{"title": t} for t in tracks] if tracks else None}


class ClassifyTestCase(unittest.TestCase):
    def test_live_albums_by_title(self):
        for title in ("Live at Sydney Opera House", "Goin' Your Way (Live)",
                      "Live, May 1992", "Live At The Continental And The Esplanade",
                      "MTV Unplugged in New York", "Paul Kelly Live",
                      "Stop Making Sense - Live"):
            self.assertEqual(classify_release(title, "album"), "live", title)

    def test_studio_albums_that_merely_say_live(self):
        for title in ("Live Through This", "Life Is Fine", "The Merri Soul Sessions",
                      "Long Live", "Alive", "Live and Let Die", "Deliverance"):
            self.assertEqual(classify_release(title, "album"), "album", title)

    def test_compilations_and_soundtracks(self):
        self.assertEqual(classify_release(
            "Songs From The South: Paul Kelly's Greatest Hits 1985-2019", "album"),
            "compilation")
        self.assertEqual(classify_release("B-Sides & Rarities", "album"), "compilation")
        self.assertEqual(classify_release("The Very Best Of", "album"), "compilation")
        self.assertEqual(classify_release(
            "Jindabyne (Original Motion Picture Soundtrack)", "album"), "soundtrack")
        self.assertEqual(classify_release("Ghost Stories", "album"), "album")

    def test_singles_and_eps_keep_their_type(self):
        self.assertEqual(classify_release("Live at the BBC", "ep"), "ep")
        self.assertEqual(classify_release("Greatest Hits", "single"), "single")

    def test_tracklist_reveals_an_unlabelled_live_album(self):
        live = ["Dumb Things (Live)", "To Her Door (Live)", "Before Too Long - Live",
                "Leaps And Bounds (Live at the Palais)"]
        self.assertEqual(classify_release("The A to Z Recordings", "album", live), "live")
        mostly_studio = ["Dumb Things", "To Her Door", "Before Too Long",
                         "Leaps And Bounds (Live)"]
        self.assertEqual(classify_release("Gossip", "album", mostly_studio), "album")

    def test_a_box_set_is_not_filed_with_the_studio_albums(self):
        self.assertEqual(classify_release(
            "The A to Z Recordings", "album", [f"Song {i}" for i in range(105)]),
            "compilation")
        self.assertEqual(classify_release("Time", "album", [f"S{i}" for i in range(30)]),
                         "album")


class BaseTitleTestCase(unittest.TestCase):
    def test_editions_share_a_base_title(self):
        self.assertEqual(base_title("Seventy (Deluxe Edition)"), base_title("Seventy"))
        self.assertEqual(base_title("Paul Kelly's Christmas Train (2023 Edition)"),
                         base_title("Paul Kelly's Christmas Train"))
        self.assertEqual(base_title("Abbey Road - Remastered 2019"),
                         base_title("Abbey Road"))
        self.assertEqual(base_title("Nevermind [Super Deluxe]"), base_title("Nevermind"))

    def test_a_live_album_is_not_an_edition(self):
        self.assertNotEqual(base_title("Goin' Your Way (Live)"), base_title("Goin' Your Way"))


class RelateTestCase(unittest.TestCase):
    def test_deluxe_contains_the_standard_edition(self):
        std = release("std", "Seventy", "album", ["A", "B"])
        dlx = release("dlx", "Seventy (Deluxe Edition)", "album", ["A", "B", "C"])
        relate([dlx, std])
        self.assertEqual(std["contained_in"], ["dlx"])
        self.assertEqual(dlx["contained_in"], [])
        self.assertEqual(std["versions"], ["dlx"])
        self.assertEqual(dlx["versions"], ["std"])

    def test_a_single_found_on_an_album(self):
        album = release("alb", "Seventy", "album", ["Rita Wrote A Letter", "Other"])
        single = release("sgl", "Rita Wrote A Letter", "single", ["Rita Wrote A Letter"])
        relate([album, single])
        self.assertEqual(single["contained_in"], ["alb"])

    def test_title_track_single_is_not_a_version_of_the_album(self):
        album = release("alb", "Time", "album", ["Time", "Other"])
        single = release("sgl", "Time", "single", ["Time"])
        relate([album, single])
        self.assertEqual(album["versions"], [])
        self.assertEqual(single["versions"], [])

    def test_live_recordings_do_not_stand_in_for_studio_ones(self):
        studio = release("s", "Gossip", "album", ["Before Too Long", "Darling It Hurts"])
        live = release("l", "Live, May 1992", "live",
                       ["Before Too Long", "Darling It Hurts", "Dumb Things"])
        relate([studio, live])
        self.assertEqual(studio["contained_in"], [])

    def test_identical_tracklists_mark_only_the_later_one(self):
        a = release("a", "Album", "album", ["One", "Two"])
        b = release("b", "Album (Remastered)", "album", ["One", "Two"])
        relate([a, b])
        self.assertEqual(a["contained_in"], [])
        self.assertEqual(b["contained_in"], ["a"])

    def test_releases_without_tracklists_are_left_alone(self):
        a = release("a", "Album", "album")
        b = release("b", "Album (Deluxe)", "album", ["One"])
        relate([a, b])
        self.assertEqual(a["contained_in"], [])
        self.assertEqual(a["versions"], ["b"])


class ParsingTestCase(unittest.TestCase):
    def test_listing_card(self):
        item = {"browseId": "MPREb_1", "title": "T", "type": "EP", "year": 2021,
                "thumbnails": [{"url": "u", "width": 226}], "isExplicit": True}
        r = catalog.release_from_listing(item, "singles")
        self.assertEqual((r["type"], r["year"], r["explicit"], r["thumbnail"]),
                         ("ep", "2021", True, "u"))
        self.assertEqual(r["url"], "https://music.youtube.com/browse/MPREb_1")

    def test_untyped_cards_take_their_section(self):
        self.assertEqual(catalog.release_from_listing(
            {"browseId": "b", "title": "x"}, "singles")["type"], "single")
        self.assertEqual(catalog.release_from_listing(
            {"browseId": "b", "title": "x"}, "albums")["type"], "album")
        self.assertIsNone(catalog.release_from_listing({"title": "x"}, "albums"))

    def test_album_numbers_every_row_and_flags_unplayable_ones(self):
        raw = raw_album("Seventy", [("v1", "One", None), (None, "Gone", None),
                                    ("v3", "Three (feat. X)", ("Paul Kelly", "X"))])
        view = catalog.album_from_raw(raw, "MPREb_x")
        self.assertEqual([t["track_number"] for t in view["tracks"]], [1, 2, 3])
        self.assertEqual([t["available"] for t in view["tracks"]], [True, False, True])
        self.assertEqual(view["thumbnail"], "big")
        self.assertEqual(view["audio_playlist_id"], "OLAK5uy_test")
        self.assertNotIn("feedbackTokens", json.dumps(view))

    def test_worker_rows_skip_unplayable_tracks_and_keep_the_shown_id(self):
        view = catalog.album_from_raw(raw_album(
            "Seventy", [("v1", "One", None), (None, "Gone", None),
                        ("v3", "Three", ("Paul Kelly", "X"))]), "MPREb_x")
        rows = catalog.album_track_rows(view, "Paul Kelly")
        self.assertEqual([r["source_video_id"] for r in rows], ["v1", "v3"])
        self.assertEqual(rows[1]["artist"], "Paul Kelly, X")
        self.assertEqual(rows[1]["track_number"], 3)
        self.assertEqual(rows[0]["album"], "Seventy")
        self.assertEqual(rows[0]["release_date"], "2025")


class LibraryKeyTestCase(unittest.TestCase):
    def test_old_youtube_titles_yield_the_song(self):
        self.assertIn("parade of the dead", ownership.library_keys(
            "hilltop hoods parade of the dead", {"hilltop hoods"}))
        self.assertIn("bicycle race live", ownership.library_keys("04 bicycle race live", set()))

    def test_a_title_that_starts_with_a_number_still_matches_itself(self):
        self.assertIn("7 rings", ownership.library_keys("7 rings", {"ariana grande"}))

    def test_remasters_are_the_same_recording(self):
        self.assertEqual(ownership.song_key("song 2011 remaster"), "song")
        self.assertEqual(ownership.song_key("song remastered 2009"), "song")
        self.assertEqual(ownership.song_key("remaster"), "remaster")

    def test_version_markers(self):
        self.assertEqual(ownership.version_markers(
            "Hilltop Hoods - The Return (Official Instrumental Edition)"), {"instrumental"})
        self.assertEqual(ownership.version_markers("Song (A Cappella)"), {"acapella"})
        self.assertEqual(ownership.version_markers("Song (Remixed)"), {"remix"})
        self.assertEqual(ownership.version_markers("Alive"), set())

    def test_album_wide_markers(self):
        self.assertEqual(ownership.album_markers("State Of The Art (Instrumental Edition)"),
                         {"instrumental"})
        self.assertEqual(ownership.album_markers("Live, May 1992"), {"live"})
        self.assertEqual(ownership.album_markers("Gossip", live=True), {"live"})
        # A song can be a cover; an album called Cover Me is not all covers.
        self.assertEqual(ownership.album_markers("Cover Me"), set())


class ErrorTestCase(unittest.TestCase):
    def test_429_backs_off_everything(self):
        with mock.patch.object(catalog.governor, "report_error") as report:
            err = catalog.classify(Exception(
                "Server returned HTTP 429: Too Many Requests.\nslow down"))
        self.assertEqual((err.status, err.error_class), (503, ErrorClass.RATE_LIMITED))
        report.assert_called_once_with(ErrorClass.RATE_LIMITED)

    def test_a_429_page_that_is_not_json_still_counts(self):
        with mock.patch.object(catalog.governor, "report_error") as report:
            err = catalog.classify(ValueError("Expecting value: line 1 column 1"), 429)
        self.assertEqual(err.error_class, ErrorClass.RATE_LIMITED)
        report.assert_called_once_with(ErrorClass.RATE_LIMITED)

    def test_the_status_seen_reaches_classify(self):
        class Session:
            def __init__(self):
                self.hooks = {"response": []}

        class Throttled:
            def __init__(self):
                self._session = Session()

            def get_album(self, browse_id):
                for hook in self._session.hooks["response"]:
                    hook(mock.Mock(status_code=429))
                raise ValueError("Expecting value: line 1 column 1 (char 0)")

        fake = Throttled()
        with mock.patch.object(catalog, "_client", None), \
                mock.patch("ytmusicapi.YTMusic", return_value=fake), \
                mock.patch.object(catalog, "pacer", catalog.Pacer()), \
                mock.patch.object(catalog.governor, "backoff_remaining", return_value=0), \
                mock.patch.object(catalog.governor, "report_error") as report:
            with self.assertRaises(CatalogError) as ctx:
                asyncio.run(catalog.call("get_album", "MPREb_x"))
        self.assertEqual(ctx.exception.status, 503)
        report.assert_called_once_with(ErrorClass.RATE_LIMITED)

    def test_not_found_is_not_throttling(self):
        with mock.patch.object(catalog.governor, "report_error") as report:
            err = catalog.classify(Exception("Server returned HTTP 400: Bad Request."))
        self.assertEqual(err.status, 404)
        report.assert_not_called()

    def test_parser_drift_says_so(self):
        err = catalog.classify(KeyError("musicShelfRenderer"))
        self.assertEqual(err.status, 502)
        self.assertIn("ytmusicapi", str(err))


class PacerTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name, value in (("catalog_prefetch_delay_min", 0.01),
                            ("catalog_prefetch_delay_max", 0.02),
                            ("catalog_requests_per_hour", 600)):
            patcher = mock.patch.object(catalog.settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pacer = catalog.Pacer()

    async def test_someone_waiting_is_told_about_a_backoff_at_once(self):
        with mock.patch.object(catalog.governor, "backoff_remaining", return_value=300):
            with self.assertRaises(CatalogError) as ctx:
                await asyncio.wait_for(self.pacer.acquire(INTERACTIVE), 1)
        self.assertEqual(ctx.exception.status, 503)
        self.assertIn("5 min", str(ctx.exception))

    async def test_background_work_waits_a_backoff_out(self):
        remaining = iter([0.05, 0.05, 0])
        with mock.patch.object(catalog.governor, "backoff_remaining",
                               side_effect=lambda: next(remaining, 0)):
            await asyncio.wait_for(self.pacer.acquire(BACKGROUND), 2)
        self.assertIsNone(self.pacer.waiting)

    async def test_hourly_budget(self):
        with mock.patch.object(catalog.settings, "catalog_requests_per_hour", 2), \
                mock.patch.object(catalog.governor, "backoff_remaining", return_value=0):
            for _ in range(2):
                await self.pacer.acquire(INTERACTIVE)
                self.pacer.release()
            with self.assertRaises(CatalogError):
                await asyncio.wait_for(self.pacer.acquire(INTERACTIVE), 3)

    async def test_interactive_requests_jump_background_ones(self):
        order = []
        with mock.patch.object(catalog.governor, "backoff_remaining", return_value=0):
            await self.pacer.acquire(BACKGROUND)          # a request in flight

            async def take(priority):
                await self.pacer.acquire(priority)
                order.append(priority)
                await asyncio.sleep(0.01)
                self.pacer.release()

            background = asyncio.create_task(take(BACKGROUND))
            await asyncio.sleep(0.05)
            interactive = asyncio.create_task(take(INTERACTIVE))
            await asyncio.sleep(0.05)
            self.pacer.release()                         # the in-flight one ends
            await asyncio.wait_for(asyncio.gather(background, interactive), 5)
        self.assertEqual(order, [INTERACTIVE, BACKGROUND])


class FakeClient:
    """Stands in for YTMusic. Counts calls, so caching can be asserted."""

    def __init__(self, artist=None, more=None, albums=None, resolve=None):
        self.artist = artist or {}
        self.more = more or {}
        self.albums = albums or {}
        self.resolve = resolve
        self.calls = []

    def get_artist(self, channel_id):
        self.calls.append(("get_artist", channel_id))
        return self.artist

    def get_artist_albums(self, browse_id, params, limit=100):
        self.calls.append(("get_artist_albums", params, limit))
        result = self.more[params]
        if isinstance(result, Exception):
            raise result
        return result

    def get_album(self, browse_id):
        self.calls.append(("get_album", browse_id))
        return self.albums[browse_id]

    def search(self, q, filter=None, limit=None):
        self.calls.append(("search", q, filter))
        return [{"browseId": "UCZwSJivYN8aF7lTZw9N2nfA", "artist": "Paul Kelly",
                 "thumbnails": [{"url": "avatar", "width": 120}]},
                {"browseId": "MPREb_notanartist", "artist": "x"}]

    def _send_request(self, endpoint, body):
        if endpoint == "search":
            self.calls.append(("raw-search", body["query"]))
            return self.raw_search
        self.calls.append((endpoint, body["url"]))
        return self.resolve

    raw_search = {}


def search_row(channel_id, name, subtitle):
    """One row of a raw artist search response, as YouTube Music sends it."""
    return {"musicResponsiveListItemRenderer": {
        "navigationEndpoint": {"browseEndpoint": {"browseId": channel_id}},
        "thumbnail": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
            {"url": f"avatar-{channel_id}", "width": 120}]}}},
        "flexColumns": [
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": name}]}}},
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [
                {"text": t} for t in subtitle]}}},
        ]}}


def card(browse_id, title, kind="Album", year="2020"):
    return {"browseId": browse_id, "title": title, "type": kind, "year": year,
            "thumbnails": [{"url": f"cover-{browse_id}", "width": 226}]}


PAUL_KELLY = "UCZwSJivYN8aF7lTZw9N2nfA"


def paul_kelly_page():
    """Ten album cards with a More link, like the real page — the bug was
    reading only these."""
    return {
        "name": "Paul Kelly", "channelId": PAUL_KELLY, "subscribers": "44.2K",
        "thumbnails": [{"url": "banner", "width": 1080, "height": 450}],
        "albums": {"browseId": f"MPAD{PAUL_KELLY}", "params": "albums",
                   "results": [card(f"MPREb_a{i}", f"Album {i}") for i in range(10)]},
        "singles": {"browseId": f"MPAD{PAUL_KELLY}", "params": "singles",
                    "results": [card("MPREb_s0", "Single 0", "Single")]},
        "videos": {"results": [{"videoId": "vid", "title": "A music video"}]},
        "related": {"results": [{"browseId": "UCEYb0pUFLvpeb-PtpHgeTVQ",
                                 "title": "Powderfinger"}]},
    }


class CatalogDatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    """Cache and library lookups are SQL, so these run against a real
    migrated database in a temporary directory."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Database()
        patches = [
            mock.patch.object(catalog.settings, "data_dir", Path(self._tmp.name)),
            mock.patch.object(catalog.settings, "catalog_prefetch_delay_min", 0.0),
            mock.patch.object(catalog.settings, "catalog_prefetch_delay_max", 0.01),
            mock.patch.object(catalog, "db", self.db),
            mock.patch.object(ownership, "db", self.db),
            mock.patch.object(catalog, "pacer", catalog.Pacer()),
            mock.patch.object(catalog, "_prefetch", None),
            mock.patch.object(catalog, "_failed", {}),
            mock.patch.object(catalog.governor, "backoff_remaining", return_value=0),
            mock.patch.object(catalog.bus, "publish"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)

    def use(self, client):
        patcher = mock.patch.object(catalog, "client", return_value=client)
        patcher.start()
        self.addCleanup(patcher.stop)
        return client

    async def stop_prefetch(self):
        if catalog._prefetch and catalog._prefetch.task:
            catalog._prefetch.task.cancel()
            await asyncio.gather(catalog._prefetch.task, return_exceptions=True)

    async def test_the_whole_discography_not_just_the_carousel(self):
        more_albums = [card(f"MPREb_a{i}", f"Album {i}") for i in range(41)]
        more_singles = [card(f"MPREb_s{i}", f"Single {i}", "Single") for i in range(21)]
        fake = self.use(FakeClient(paul_kelly_page(),
                                   {"albums": more_albums, "singles": more_singles}))
        doc = await catalog.discography(PAUL_KELLY)
        self.assertEqual(len(doc["releases"]), 62)
        self.assertIn(("get_artist_albums", "albums", None), fake.calls)
        self.assertFalse(doc["partial"])
        self.assertEqual(doc["related"][0]["name"], "Powderfinger")
        self.assertTrue(doc["thumbnail_wide"])

        await catalog.discography(PAUL_KELLY)
        self.assertEqual(sum(1 for c in fake.calls if c[0] == "get_artist"), 1)

    async def test_the_official_channel_and_the_music_artist_are_one_artist(self):
        official = "UCV4GjyogdR9_0Gqfhf6eNKw"
        fake = self.use(FakeClient(paul_kelly_page(), {"albums": [], "singles": []}))
        doc = await catalog.discography(official)
        self.assertEqual(doc["channel_id"], PAUL_KELLY)
        again = await catalog.discography(PAUL_KELLY)
        self.assertEqual(again["channel_id"], PAUL_KELLY)
        self.assertEqual(sum(1 for c in fake.calls if c[0] == "get_artist"), 1)

    async def test_a_carousel_without_more_is_already_complete(self):
        page = paul_kelly_page()
        del page["albums"]["browseId"]
        fake = self.use(FakeClient(page, {"singles": []}))
        doc = await catalog.discography(PAUL_KELLY)
        self.assertEqual(len(doc["releases"]), 10)
        self.assertNotIn("albums", [c[1] for c in fake.calls if c[0] == "get_artist_albums"])

    async def test_a_failed_more_page_falls_back_to_the_carousel_and_says_so(self):
        self.use(FakeClient(paul_kelly_page(), {
            "albums": KeyError("gridRenderer"), "singles": []}))
        doc = await catalog.discography(PAUL_KELLY)
        self.assertTrue(doc["partial"])
        self.assertEqual(len(doc["releases"]), 10)

    async def test_throttling_on_the_more_page_is_not_swallowed(self):
        self.use(FakeClient(paul_kelly_page(), {
            "albums": Exception("Server returned HTTP 429: Too Many Requests."),
            "singles": []}))
        with mock.patch.object(catalog.governor, "report_error"):
            with self.assertRaises(CatalogError) as ctx:
                await catalog.discography(PAUL_KELLY)
        self.assertEqual(ctx.exception.status, 503)

    async def test_an_ordinary_channel_is_not_an_artist(self):
        fake = self.use(FakeClient())
        fake.get_artist = mock.Mock(side_effect=KeyError("musicImmersiveHeaderRenderer"))
        with self.assertRaises(CatalogError) as ctx:
            await catalog.discography("UCBR8-60-B28hp2BmDPdntcQ")
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn("no YouTube Music artist page", str(ctx.exception))

    async def test_handles_resolve_once(self):
        fake = self.use(FakeClient(resolve={"endpoint": {"browseEndpoint": {
            "browseId": "UCV4GjyogdR9_0Gqfhf6eNKw"}}}))
        for ref in ("https://www.youtube.com/@paulkelly", "https://music.youtube.com/@PaulKelly"):
            self.assertEqual(await catalog.resolve_channel(ref), "UCV4GjyogdR9_0Gqfhf6eNKw")
        self.assertEqual(fake.calls, [("navigation/resolve_url",
                                       "https://music.youtube.com/@paulkelly")])

    async def test_ids_in_links_need_no_request(self):
        fake = self.use(FakeClient())
        self.assertEqual(await catalog.resolve_channel(
            f"https://music.youtube.com/channel/{PAUL_KELLY}?si=x"), PAUL_KELLY)
        self.assertEqual(await catalog.resolve_channel(
            f"https://music.youtube.com/browse/MPAD{PAUL_KELLY}"), PAUL_KELLY)
        self.assertEqual(fake.calls, [])
        with self.assertRaises(CatalogError) as ctx:
            await catalog.resolve_channel("paul kelly")
        self.assertEqual(ctx.exception.status, 422)

    async def test_a_handle_that_is_not_an_artist(self):
        self.use(FakeClient(resolve={"endpoint": {"urlEndpoint": {"url": "x"}}}))
        with self.assertRaises(CatalogError) as ctx:
            await catalog.resolve_channel("@somebody")
        self.assertEqual(ctx.exception.status, 404)

    async def test_search_says_which_paul_kelly_is_which(self):
        fake = self.use(FakeClient())
        fake.raw_search = {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"x": [
            search_row(PAUL_KELLY, "Paul Kelly", ["Artist", "  •  ", "210K monthly audience"]),
            search_row("UCQuGdKMhKWeRg854sxBU36A", "Paul Kelly", ["Artist"]),
            search_row("MPREb_notanartist", "An album", ["Album"]),
        ]}]}}}
        artists = await catalog.search_artists("  Paul   Kelly ")
        self.assertEqual([(a["channel_id"], a["audience"]) for a in artists],
                         [(PAUL_KELLY, "210K monthly audience"),
                          ("UCQuGdKMhKWeRg854sxBU36A", None)])
        self.assertEqual(artists[0]["thumbnail"], f"avatar-{PAUL_KELLY}")
        await catalog.search_artists("paul kelly")
        self.assertEqual(fake.calls, [("raw-search", "Paul Kelly")])

    async def test_search_falls_back_to_ytmusicapi_when_the_layout_changes(self):
        fake = self.use(FakeClient())
        fake.raw_search = {"contents": {"somethingNew": {}}}
        artists = await catalog.search_artists("paul kelly")
        self.assertEqual([a["channel_id"] for a in artists], [PAUL_KELLY])
        self.assertEqual([c[0] for c in fake.calls], ["raw-search", "search"])

    async def test_album_cache_respects_max_age(self):
        fake = self.use(FakeClient(albums={"MPREb_x": raw_album("X", [("v1", "One", None)])}))
        await catalog.album("MPREb_x")
        await catalog.album("MPREb_x")
        self.assertEqual(len(fake.calls), 1)
        await catalog.album("MPREb_x", max_age=0)
        self.assertEqual(len(fake.calls), 2)

    async def add_library(self, *rows):
        """(norm_artist, norm_title, title, path, duration, provider_id)"""
        await self.db.executemany(
            "INSERT INTO library_index (norm_artist, norm_title, title, path, dirpath, "
            "duration_sec, provider_track_id, added_at) VALUES (?,?,?,?,?,?,?,?)",
            [(a, n, t, path, path.rsplit("/", 1)[0], d, pid, "t")
             for a, n, t, path, d, pid in rows])

    async def test_artist_view_marks_what_the_library_already_has(self):
        page = paul_kelly_page()
        page["albums"] = {"results": [card("MPREb_a0", "Seventy")]}
        page["singles"] = {"results": [card("MPREb_s0", "Rita Wrote A Letter", "Single")]}
        fake = self.use(FakeClient(page, albums={
            "MPREb_a0": raw_album("Seventy", [          # every track is 100s long
                ("v1", "Rita Wrote A Letter", None),
                ("v2", "Take It Handy", None),
                ("v3", "Stardust", None),
                ("v4", "Sailing To Byzantium", None),
                ("v5", "The Magpies", None),
                ("v6", "Made For Me", None),
                ("v7", "Happy Birthday, Ada Mae", None)]),
            "MPREb_s0": raw_album("Rita Wrote A Letter", [
                ("v1", "Rita Wrote A Letter", None)]),
        }))
        await self.add_library(
            # same provider id, on this album
            ("paul kelly", "rita wrote a letter", None, "PK/Seventy/03.m4a", 100, "v1"),
            # same artist + title — the old gate's rule — on this album
            ("paul kelly", "take it handy", None, "PK/Seventy/06.m4a", 100, None),
            # other credits, same length, this album
            ("paul kelly the messengers", "stardust", None, "PK/Seventy/13.m4a", 101, None),
            # somebody else's song
            ("someone else", "sailing to byzantium", None, "SE/x.m4a", 100, None),
            # the instrumental edition: another album, another version
            ("paul kelly", "paul kelly the magpies",
             "Paul Kelly - The Magpies (Official Instrumental Edition)",
             "PK/Seventy (Instrumental Edition)/08.m4a", 100, None),
            # an old YouTube-style title, in this album's folder
            ("paul kelly", "paul kelly made for me", "Paul Kelly - Made For Me (Official Audio)",
             "PK/Seventy/made.m4a", 102, None),
            # the same song on a compilation: that compilation's copy
            ("paul kelly", "happy birthday ada mae", None,
             "PK/Songs From The South/07.m4a", 100, None))

        view = await catalog.artist_view(PAUL_KELLY)
        self.assertEqual([r["tracks"] for r in view["releases"]], [None, None])
        self.assertEqual(view["prefetch"]["total"], 2)
        await asyncio.wait_for(catalog._prefetch.task, 5)
        self.assertEqual(sum(1 for c in fake.calls if c[0] == "get_album"), 2)

        view = await catalog.artist_view(PAUL_KELLY)
        album, single = view["releases"]
        self.assertEqual([t["library"] for t in album["tracks"]],
                         ["have", "have", "have", None, None, "have", "elsewhere"])
        self.assertEqual(album["tracks"][6]["library_folder"], "PK/Songs From The South")
        library = album["library"]
        self.assertEqual((library["have"], library["elsewhere"], library["total"],
                          library["new"]), (4, 1, 7, 3))
        self.assertEqual(library["folders"], [{"folder": "PK/Seventy", "tracks": 4}])
        # The single's song is on the album — but the single is its own release.
        self.assertEqual(single["tracks"][0]["library"], "elsewhere")
        self.assertEqual(single["library"]["new"], 1)
        self.assertEqual(single["contained_in"], ["MPREb_a0"])
        self.assertFalse(view["prefetch"]["running"])
        # Everything is cached now: drawing the view again asks YouTube nothing.
        calls = len(fake.calls)
        await catalog.artist_view(PAUL_KELLY)
        self.assertEqual(len(fake.calls), calls)

    async def annotate(self, album, category="album", names=("Paul Kelly",)):
        index = await catalog._index_for([album], list(names))
        return catalog._annotate_album(album, index, names[0], category)["tracks"]

    async def test_a_live_album_is_not_owned_through_studio_copies(self):
        await self.add_library(
            ("paul kelly the messengers", "to her door", None, "PK/Under The Sun/01.m4a",
             100, None),
            ("paul kelly the messengers", "dumb things", None, "PK/Gossip/02.m4a", 100, None))
        live = catalog.album_from_raw(raw_album("Live, May 1992", [
            ("v1", "To Her Door", None), ("v2", "Dumb Things (Live)", None)]), "MPREb_l")
        self.assertEqual([t["library"] for t in await self.annotate(live, "live")],
                         [None, None])
        studio = catalog.album_from_raw(raw_album("Under The Sun", [
            ("v3", "To Her Door", None)]), "MPREb_s")
        self.assertEqual((await self.annotate(studio))[0]["library"], "have")

    async def test_an_instrumental_edition_matches_its_old_youtube_copies(self):
        await self.add_library(
            ("hilltop hoods", "hilltop hoods exit sign",
             "Hilltop Hoods - Exit Sign (Official Instrumental Edition) ft. Illy, Ecca Vandal",
             "HH/The Great Expanse (Instrumental Edition)/06.m4a", 100, None),
            ("hilltop hoods", "hilltop hoods exit sign",
             "Hilltop Hoods - Exit Sign (Official Audio) ft. Illy, Ecca Vandal",
             "HH/The Great Expanse/06.m4a", 100, None))
        instrumental = catalog.album_from_raw(raw_album(
            "The Great Expanse (Instrumental Edition)",
            [("v1", "Exit Sign (Instrumental)", None)], artists=("Hilltop Hoods",)), "MPREb_i")
        vocal = catalog.album_from_raw(raw_album(
            "The Great Expanse", [("v2", "Exit Sign (feat. Illy & Ecca Vandal)", None)],
            artists=("Hilltop Hoods",)), "MPREb_v")
        [i] = await self.annotate(instrumental, names=("Hilltop Hoods",))
        [v] = await self.annotate(vocal, names=("Hilltop Hoods",))
        self.assertEqual((i["library"], i["library_path"]),
                         ("have", "HH/The Great Expanse (Instrumental Edition)/06.m4a"))
        self.assertEqual((v["library"], v["library_path"]),
                         ("have", "HH/The Great Expanse/06.m4a"))

    async def test_old_uploads_get_slack_for_their_padding(self):
        await self.add_library(
            ("hilltop hoods", "hilltop hoods never coming home",
             "Hilltop Hoods - Never Coming Home (Official Video)", "HH/X/a.m4a", 122, None),
            ("hilltop hoods", "hilltop hoods show business", None, "HH/X/b.m4a", 122, None))
        album = catalog.album_from_raw(raw_album("X", [
            ("v1", "Never Coming Home", None), ("v2", "Show Business", None)],
            artists=("Hilltop Hoods",)), "MPREb_x")
        upload, clean = await self.annotate(album, names=("Hilltop Hoods",))
        self.assertEqual((upload["library"], upload["library_upload"]), ("have", True))
        self.assertIsNone(clean["library"])    # 22s off and no excuse

    async def test_the_gate_rule_still_flags_a_copy_that_looks_different(self):
        await self.add_library(
            ("the devil wears prada", "outnumbered", "Outnumbered",
             "TDWP/Zombie EP/03 - Outnumbered.opus", 265, None),
            ("the devil wears prada", "chemical", None, "TDWP/Zombie EP/04.opus", 230, None),
            ("the devil wears prada", "escape", None, "TDWP/Dead & Alive/03.opus", 200, None))
        album = catalog.album_from_raw(raw_album("Zombie EP", [
            ("v1", "Outnumbered", None), ("v2", "Chemical", None), ("v3", "Escape", None)],
            artists=("The Devil Wears Prada",)), "MPREb_z")
        for t, length in zip(album["tracks"], (295, 231, 200)):
            t["duration_sec"] = length
        out, chem, esc = await self.annotate(album, "ep", names=("The Devil Wears Prada",))
        self.assertEqual((out["library"], out["library_mismatch"]), ("have", True))
        self.assertEqual((chem["library"], chem["library_mismatch"]), ("have", False))
        self.assertEqual(esc["library"], "elsewhere")

    async def plan_setup(self):
        self.use(FakeClient(albums={
            "MPREb_a": raw_album("Seventy", [
                ("v1", "Owned", None), ("v2", "New One", None),
                (None, "Unplayable", None), ("v4", "New Two", None)]),
            "MPREb_full": raw_album("Old", [("w1", "Owned", None)]),
        }))
        await self.add_library(
            ("paul kelly", "owned", None, "PK/Seventy/01.m4a", 100, None),
            ("paul kelly", "owned", None, "PK/Old/01.m4a", 100, None))

    async def test_queue_plan_leaves_owned_tracks_out(self):
        await self.plan_setup()
        whole, ticked, full, gone = await catalog.plan_queue([
            {"browse_id": "MPREb_a", "video_ids": None, "artist": "Paul Kelly"},
            {"browse_id": "MPREb_a", "video_ids": ["v1", "v2"], "artist": "Paul Kelly"},
            {"browse_id": "MPREb_full", "video_ids": None, "artist": "Paul Kelly"},
            {"browse_id": "MPREb_gone", "video_ids": None, "artist": "Paul Kelly"},
        ])
        self.assertEqual((whole["video_ids"], whole["tracks"], whole["left_out"]),
                         (["v2", "v4"], 2, 1))
        self.assertEqual(whole["force_ids"], [])
        # Ticking an owned track is choosing it: kept, and forced past the gate.
        self.assertEqual((ticked["video_ids"], ticked["force_ids"], ticked["left_out"]),
                         (["v1", "v2"], ["v1"], 0))
        self.assertEqual((full["tracks"], full["left_out"]), (0, 1))
        self.assertTrue(gone["error"])

    async def test_queue_plan_with_force_keeps_everything(self):
        await self.plan_setup()
        [plan] = await catalog.plan_queue(
            [{"browse_id": "MPREb_a", "video_ids": None, "artist": "Paul Kelly"}],
            include_owned=True)
        self.assertEqual((plan["video_ids"], plan["tracks"], plan["left_out"]), (None, 3, 0))

    async def test_a_failing_tracklist_is_not_retried_on_every_refresh(self):
        page = paul_kelly_page()
        page["albums"] = {"results": [card("MPREb_bad", "Broken")]}
        page["singles"] = {"results": []}
        fake = self.use(FakeClient(page))
        fake.albums = mock.MagicMock()
        fake.albums.__getitem__.side_effect = Exception("Server returned HTTP 404: Not Found.")
        await catalog.artist_view(PAUL_KELLY)
        await asyncio.wait_for(catalog._prefetch.task, 5)
        self.assertEqual(catalog._prefetch.failed, 1)
        view = await catalog.artist_view(PAUL_KELLY)
        self.assertFalse(view["prefetch"]["running"])
        self.assertEqual(sum(1 for c in fake.calls if c[0] == "get_album"), 1)

    async def test_opening_another_artist_stops_the_last_prefetch(self):
        page = paul_kelly_page()
        page["albums"] = {"results": [card(f"MPREb_a{i}", f"A{i}") for i in range(5)]}
        page["singles"] = {"results": []}
        self.use(FakeClient(page, albums={
            f"MPREb_a{i}": raw_album(f"A{i}", [("v", "t", None)]) for i in range(5)}))
        with mock.patch.object(catalog.settings, "catalog_prefetch_delay_min", 5), \
                mock.patch.object(catalog.settings, "catalog_prefetch_delay_max", 5):
            await catalog.artist_view(PAUL_KELLY)
            first = catalog._prefetch
            await asyncio.sleep(0.05)
            catalog.ensure_prefetch("UCother_____________xx", ["MPREb_z"])
            await asyncio.gather(first.task, return_exceptions=True)
            self.assertTrue(first.task.cancelled() or first.finished)
            self.assertEqual(catalog._prefetch.channel_id, "UCother_____________xx")
            await self.stop_prefetch()


class WorkerExpansionTestCase(unittest.IsolatedAsyncioTestCase):
    """A partial selection has to survive the swap to album-audio ids."""

    async def asyncSetUp(self):
        from app import worker as worker_module
        self.worker_module = worker_module
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Database()
        patches = [
            mock.patch.object(worker_module.settings, "data_dir", Path(self._tmp.name)),
            mock.patch.object(worker_module, "db", self.db),
            mock.patch.object(worker_module.bus, "publish"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.worker = worker_module.Worker()

    async def add_job(self, track_filter=None):
        with mock.patch.object(self.worker, "wake"):
            job_id = await self.worker.enqueue(
                "https://music.youtube.com/browse/MPREb_x", title="Seventy",
                artist="Paul Kelly", track_filter=track_filter)
        return await self.db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))

    def resolved(self):
        # Album-audio ids (a*) replaced the shown ids (v*) during expansion.
        return [{"provider_track_id": f"a{i}", "source_video_id": f"v{i}",
                 "title": f"T{i}", "artist": "Paul Kelly"} for i in range(1, 5)]

    async def test_only_the_ticked_tracks_are_queued(self):
        job = await self.add_job(json.dumps(["v2", "v4"]))
        self.assertEqual(job["title"], "Seventy")
        with mock.patch.object(self.worker, "_resolve",
                               mock.AsyncMock(return_value=("Seventy", "Paul Kelly",
                                                            self.resolved()))):
            await self.worker._expand_job(job)
        rows = await self.db.fetchall(
            "SELECT provider_track_id, position FROM tracks ORDER BY position")
        self.assertEqual([(r["provider_track_id"], r["position"]) for r in rows],
                         [("a2", 1), ("a4", 2)])
        job = await self.db.fetchone("SELECT * FROM jobs WHERE id = ?", (job["id"],))
        self.assertEqual((job["status"], job["total_tracks"]), (JobStatus.DOWNLOADING, 2))

    async def test_no_filter_means_the_whole_release(self):
        job = await self.add_job()
        with mock.patch.object(self.worker, "_resolve",
                               mock.AsyncMock(return_value=("S", "P", self.resolved()))):
            await self.worker._expand_job(job)
        row = await self.db.fetchone("SELECT COUNT(*) AS n FROM tracks")
        self.assertEqual(row["n"], 4)

    async def test_selected_tracks_that_vanished(self):
        job = await self.add_job(json.dumps(["gone"]))
        with mock.patch.object(self.worker, "_resolve",
                               mock.AsyncMock(return_value=("S", "P", self.resolved()))):
            await self.worker._expand_job(job)
        job = await self.db.fetchone("SELECT * FROM jobs WHERE id = ?", (job["id"],))
        self.assertEqual(job["status"], JobStatus.FAILED)
        self.assertIn("selected tracks", job["error_message"])

    async def test_throttling_puts_the_job_back_rather_than_failing_it(self):
        job = await self.add_job()
        error = CatalogError("slow down", 503, ErrorClass.RATE_LIMITED)
        with mock.patch.object(self.worker, "_resolve", mock.AsyncMock(side_effect=error)):
            await self.worker._expand_job(job)
        job = await self.db.fetchone("SELECT * FROM jobs WHERE id = ?", (job["id"],))
        self.assertEqual((job["status"], job["error_code"]),
                         (JobStatus.QUEUED, ErrorClass.RATE_LIMITED))

    async def test_album_resolution_uses_the_cached_tracklist(self):
        view = catalog.album_from_raw(raw_album("Seventy", [
            ("v1", "One", None), ("v2", "Two", None)]), "MPREb_x")
        with mock.patch.object(self.worker_module.catalog, "album",
                               mock.AsyncMock(return_value=view)) as album, \
                mock.patch.object(self.worker, "_use_album_audio", mock.AsyncMock()):
            title, artist, tracks = await self.worker._resolve_ytm_album("MPREb_x")
        album.assert_awaited_once_with("MPREb_x", priority=BACKGROUND,
                                       max_age=catalog.WORKER_ALBUM_MAX_AGE)
        self.assertEqual((title, artist), ("Seventy", "Paul Kelly"))
        self.assertEqual([t["provider_track_id"] for t in tracks], ["v1", "v2"])

    def test_track_filter_parsing(self):
        f = self.worker_module.track_filter
        forced = self.worker_module.forced_tracks
        self.assertIsNone(f({"id": "j", "track_filter": None}))
        self.assertEqual(f({"id": "j", "track_filter": '["a", "b"]'}), {"a", "b"})
        self.assertIsNone(f({"id": "j", "track_filter": "not json"}))
        self.assertIsNone(f({"id": "j"}))
        spec = {"id": "j", "track_filter": '{"keep": ["a", "b"], "force": ["b"]}'}
        self.assertEqual((f(spec), forced(spec)), ({"a", "b"}, {"b"}))
        whole = {"id": "j", "track_filter": '{"keep": null, "force": ["b"]}'}
        self.assertEqual((f(whole), forced(whole)), (None, {"b"}))
        self.assertEqual(forced({"id": "j", "track_filter": '["a"]'}), set())

    async def test_owned_tracks_ticked_on_purpose_skip_the_dedup_gate(self):
        job = await self.add_job(json.dumps({"keep": ["v2", "v3"], "force": ["v3"]}))
        with mock.patch.object(self.worker, "_resolve",
                               mock.AsyncMock(return_value=("S", "P", self.resolved()))):
            await self.worker._expand_job(job)
        rows = await self.db.fetchall("SELECT provider_track_id, force FROM tracks ORDER BY position")
        self.assertEqual([(r["provider_track_id"], r["force"]) for r in rows],
                         [("a2", self.worker_module.FORCE_NONE),
                          ("a3", self.worker_module.FORCE_GUARDS)])


class QueueEndpointTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app import api
        self.api = api
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Database()
        self.enqueued = []

        async def enqueue(url, force=False, **kw):
            job_id = f"job{len(self.enqueued)}"
            self.enqueued.append((url, force, kw))
            await self.db.execute(
                "INSERT INTO jobs (id, source_url, provider, source_type, status, "
                "track_filter, created_at) VALUES (?,?,?,?,?,?,?)",
                (job_id, url, "youtube_music", "album", JobStatus.QUEUED,
                 kw.get("track_filter"), "t"))
            return job_id

        self.plans, self.plan_calls = {}, []

        async def plan_queue(picks, include_owned=False, force_ticked=True):
            self.plan_calls.append(include_owned)
            return [self.plans.get(p["browse_id"], {
                "browse_id": p["browse_id"],
                "video_ids": sorted(set(p["video_ids"])) if p["video_ids"] else None,
                "tracks": len(set(p["video_ids"] or [1, 2, 3])),
                "left_out": 0, "error": None}) for p in picks]

        patches = [
            mock.patch.object(api.settings, "data_dir", Path(self._tmp.name)),
            mock.patch.object(api, "db", self.db),
            mock.patch.object(api.worker, "enqueue", new=enqueue),
            mock.patch.object(api.catalog, "plan_queue", new=plan_queue),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)

    async def queue(self, *picks, force=False):
        body = self.api.CatalogQueueIn(releases=list(picks), force=force)
        return await self.api.catalog_queue(body)

    async def test_one_job_per_release_in_order(self):
        result = await self.queue({"browse_id": "MPREb_a", "title": "A", "artist": "P"},
                                  {"browse_id": "MPREb_b", "video_ids": ["y", "x", "y"]},
                                  force=True)
        self.assertEqual([q["browse_id"] for q in result["queued"]], ["MPREb_a", "MPREb_b"])
        url, force, kw = self.enqueued[1]
        self.assertEqual(url, "https://music.youtube.com/browse/MPREb_b")
        self.assertTrue(force)
        self.assertEqual(kw["track_filter"], '["x", "y"]')
        self.assertEqual(self.enqueued[0][2]["title"], "A")
        self.assertEqual(self.plan_calls, [True])

    async def test_owned_tracks_are_left_out_and_owned_releases_skipped(self):
        self.plans = {
            "MPREb_a": {"browse_id": "MPREb_a", "video_ids": ["v2"], "tracks": 1,
                        "left_out": 3, "error": None},
            "MPREb_b": {"browse_id": "MPREb_b", "video_ids": [], "tracks": 0,
                        "left_out": 5, "error": None},
            "MPREb_c": {"browse_id": "MPREb_c", "error": "YouTube asked us to slow down"},
        }
        result = await self.queue({"browse_id": "MPREb_a"}, {"browse_id": "MPREb_b"},
                                  {"browse_id": "MPREb_c"})
        self.assertEqual(self.plan_calls, [False])
        self.assertEqual(result["queued"], [{"browse_id": "MPREb_a", "job_id": "job0",
                                             "tracks": 1, "left_out": 3, "forced": 0}])
        self.assertEqual(self.enqueued[0][2]["track_filter"], '["v2"]')
        self.assertEqual([(x["browse_id"], x["reason"]) for x in result["skipped"]],
                         [("MPREb_b", "already in your library"),
                          ("MPREb_c", "YouTube asked us to slow down")])
        self.assertEqual(result["left_out"], 8)

    async def test_owned_tracks_ticked_on_purpose_are_stored_as_forced(self):
        self.plans = {"MPREb_a": {"browse_id": "MPREb_a", "video_ids": ["v2", "v1"],
                                  "force_ids": ["v1"], "tracks": 2, "left_out": 0,
                                  "error": None}}
        await self.queue({"browse_id": "MPREb_a", "video_ids": ["v1", "v2"]})
        self.assertEqual(json.loads(self.enqueued[0][2]["track_filter"]),
                         {"keep": ["v1", "v2"], "force": ["v1"]})

    async def test_the_same_selection_is_not_queued_twice(self):
        await self.queue({"browse_id": "MPREb_a"})
        result = await self.queue({"browse_id": "MPREb_a"},
                                  {"browse_id": "MPREb_a", "video_ids": ["x"]})
        self.assertEqual(result["skipped"], [{"browse_id": "MPREb_a",
                                              "reason": "already in the queue"}])
        self.assertEqual(len(result["queued"]), 1)

    async def test_rejects_junk(self):
        result = await self.queue({"browse_id": "UCnotanalbum"},
                                  {"browse_id": "MPREb_a", "video_ids": []})
        self.assertEqual(result["queued"], [])
        self.assertEqual(len(result["skipped"]), 2)


if __name__ == "__main__":
    unittest.main()
