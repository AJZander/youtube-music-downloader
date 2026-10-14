"""Run with: python -m unittest discover backend/tests"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import catalog, discover, ownership
from app.db import Database

PK = "UCZwSJivYN8aF7lTZw9N2nfA"
HH = "UCTqqx2yRasEUzMYVP2_hi_A"
POWDERFINGER = "UCEYb0pUFLvpeb-PtpHgeTVQ"
CHISEL = "UCaj-Ivq6dqo9RoTDL0uXi3w"


def search_row(channel_id, name, subtitle):
    return {"musicResponsiveListItemRenderer": {
        "navigationEndpoint": {"browseEndpoint": {"browseId": channel_id}},
        "flexColumns": [
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": name}]}}},
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [
                {"text": "Artist"}, {"text": " • "}, {"text": subtitle}]}}}]}}


def artist_page(name, albums, singles=(), related=()):
    card = lambda b, t, kind, year: {"browseId": b, "title": t, "type": kind, "year": year}
    return {
        "name": name,
        "albums": {"results": [card(b, t, "Album", y) for b, t, y in albums]},
        "singles": {"results": [card(b, t, "Single", y) for b, t, y in singles]},
        "related": {"results": [{"browseId": c, "title": n} for c, n in related]},
    }


class FakeClient:
    def __init__(self):
        self.searches, self.pages, self.albums = {}, {}, {}
        self.calls = []
        self.genre_page = {}

    def _send_request(self, endpoint, body):
        self.calls.append((endpoint, body.get("query") or body.get("browseId")))
        if endpoint == "search":
            return {"contents": [self.searches.get(body["query"], [])]}
        return self.genre_page

    def get_artist(self, channel_id):
        self.calls.append(("get_artist", channel_id))
        return self.pages[channel_id]

    def get_album(self, browse_id):
        self.calls.append(("get_album", browse_id))
        return self.albums[browse_id]

    def get_charts(self, country):
        self.calls.append(("get_charts", country))
        return {"countries": {"options": ["AU", "ZZ"]},
                "artists": [{"title": "Taylor Swift", "browseId": "UCPC0L1d253x-KuMNwa05TpA",
                             "rank": "1", "trend": "up", "subscribers": "63M"}],
                "videos": [{"title": "Top 100 Music Videos Australia",
                            "playlistId": "PL4fGSI1pDJn44"}]}

    def get_mood_categories(self):
        self.calls.append(("get_mood_categories",))
        return {"Genres": [{"title": "Metal", "params": "metal-params"}],
                "Moods & moments": [{"title": "Chill", "params": "chill-params"}]}


class PickArtistTestCase(unittest.TestCase):
    def test_audience(self):
        self.assertEqual(discover.audience("649K monthly audience"), 649_000)
        self.assertEqual(discover.audience("1.26K subscribers"), 1_260)
        self.assertEqual(discover.audience("2.28M"), 2_280_000)
        self.assertEqual(discover.audience(None), 0)

    def test_the_biggest_artist_by_that_exact_name(self):
        results = [{"name": "Paul Kelly", "channel_id": "small", "audience": "1.26K subscribers"},
                   {"name": "Paul Kelly", "channel_id": "big", "audience": "210K monthly audience"},
                   {"name": "Paul Kelly Project", "channel_id": "other", "audience": "9M"}]
        self.assertEqual(discover.pick_artist("Paul Kelly", results)["channel_id"], "big")
        self.assertEqual(discover.pick_artist("AC_DC", [
            {"name": "AC/DC", "channel_id": "acdc", "audience": "1M"}])["channel_id"], "acdc")

    def test_no_exact_name_is_no_match(self):
        self.assertIsNone(discover.pick_artist("Hilltop Hoods and Nyassa", [
            {"name": "Hilltop Hoods", "channel_id": "x", "audience": "1M"}]))


class GenreParseTestCase(unittest.TestCase):
    def page(self):
        def run(text, browse=None):
            r = {"text": text}
            if browse:
                r["navigationEndpoint"] = {"browseEndpoint": {"browseId": browse}}
            return r

        def col(*runs):
            return {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": list(runs)}}}

        song = {"musicResponsiveListItemRenderer": {
            "playlistItemData": {"videoId": "lKVZKG7CcMU"},
            "flexColumns": [col(run("Joseph")),
                            col(run("Falling In Reverse", "UCMk4O9QsqpW55Ml21cW0Ylg"),
                                run(", "), run("Corey Taylor"), run(" • "), run("5.1M views")),
                            col(run("Joseph", "MPREb_snFfLEk7mb7"))]}}
        album = {"musicTwoRowItemRenderer": {
            "title": {"runs": [run("Metallica")]},
            "subtitle": {"runs": [run("Album"), run(" • "),
                                  run("Metallica", "UCGexNm_Kw4rdQjLxmpb2EKw")]},
            "navigationEndpoint": {"browseEndpoint": {"browseId": "MPREb_AmmVchA41UA"}}}}
        playlist = {"musicTwoRowItemRenderer": {
            "title": {"runs": [run("Harder Than Hell")]},
            "subtitle": {"runs": [run("Slipknot, Linkin Park")]},
            "navigationEndpoint": {"browseEndpoint": {"browseId": "VLRDCLAK5uy_lFJG"}}}}

        def shelf(title, items):
            return {"musicCarouselShelfRenderer": {
                "header": {"musicCarouselShelfBasicHeaderRenderer": {"title": {"runs": [run(title)]}}},
                "contents": items}}

        return {"contents": {"x": {"sectionListRenderer": {"contents": [
            shelf("Songs", [song]), shelf("Featured playlists", [playlist]),
            shelf("Community playlists", [playlist]), shelf("Albums", [album])]}}}}

    def test_reads_songs_albums_and_featured_playlists(self):
        doc = discover.parse_genre(self.page())
        [song] = doc["songs"]
        self.assertEqual((song["rank"], song["title"], song["artist"], song["views"]),
                         (1, "Joseph", "Falling In Reverse, Corey Taylor", "5.1M views"))
        self.assertEqual((song["album"], song["album_id"]), ("Joseph", "MPREb_snFfLEk7mb7"))
        self.assertEqual(doc["albums"][0]["artist"], "Metallica")
        self.assertEqual([p["playlist_id"] for p in doc["playlists"]], ["RDCLAK5uy_lFJG"])


class DiscoverDatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Database()
        self.fake = FakeClient()
        patches = [
            mock.patch.object(catalog.settings, "data_dir", Path(self._tmp.name)),
            mock.patch.object(catalog.settings, "catalog_prefetch_delay_min", 0.0),
            mock.patch.object(catalog.settings, "catalog_prefetch_delay_max", 0.0),
            mock.patch.object(catalog.settings, "discover_suggested_artists", 5),
            mock.patch.object(catalog, "db", self.db),
            mock.patch.object(ownership, "db", self.db),
            mock.patch.object(discover, "db", self.db),
            mock.patch.object(catalog, "pacer", catalog.Pacer()),
            mock.patch.object(catalog, "client", return_value=self.fake),
            mock.patch.object(catalog.governor, "backoff_remaining", return_value=0),
            mock.patch.object(discover.bus, "publish"),
            mock.patch.object(discover, "_owned", {"at": 0.0, "keys": frozenset()}),
            mock.patch.object(discover, "_listings", {}),
            mock.patch.object(discover, "scanner", discover.Scanner()),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)

    async def library(self, *paths):
        await self.db.executemany(
            "INSERT INTO library_index (norm_artist, norm_title, path, dirpath, added_at) "
            "VALUES (?,?,?,?,?)",
            [(p.split("/")[0].lower(), p.rsplit("/", 1)[-1].lower(), p,
              p.rsplit("/", 1)[0], "t") for p in paths])

    def setup_artists(self):
        self.fake.searches = {
            "Paul Kelly": [search_row(PK, "Paul Kelly", "210K monthly audience"),
                           search_row("UCsmall", "Paul Kelly", "1.26K subscribers")],
            "Hilltop Hoods": [search_row(HH, "Hilltop Hoods", "649K monthly audience")],
        }
        year = time.localtime().tm_year
        self.fake.pages = {
            PK: artist_page("Paul Kelly",
                            [("MPREb_seventy", "Seventy", str(year - 1)),
                             ("MPREb_gossip", "Gossip", "1986")],
                            [("MPREb_rita", "Rita Wrote A Letter", str(year))],
                            related=[(POWDERFINGER, "Powderfinger"), (CHISEL, "Cold Chisel"),
                                     (HH, "Hilltop Hoods")]),
            HH: artist_page("Hilltop Hoods", [("MPREb_fall", "Fall From The Light", str(year))],
                            related=[(POWDERFINGER, "Powderfinger")]),
            POWDERFINGER: artist_page("Powderfinger", [("MPREb_pf", "Unreleased", str(year))]),
            CHISEL: artist_page("Cold Chisel", [("MPREb_cc", "Old", "1980")]),
        }

    async def scan(self, steps=20):
        did = []
        for _ in range(steps):
            if not await discover.scanner.step():
                break
            did.append(True)
        return len(did)

    async def test_a_scan_matches_folders_then_reads_their_pages_biggest_first(self):
        self.setup_artists()
        await self.library(*[f"Paul Kelly/Seventy/{i:02d}.m4a" for i in range(1, 13)],
                           "Hilltop Hoods/State Of The Art/01.opus",
                           "Various Artists/Hits/01.mp3", ".mdl-trash/x/01.mp3")
        await self.scan()
        order = [c for c in self.fake.calls if c[0] in ("search", "get_artist")]
        self.assertEqual(order[:2], [("search", "Paul Kelly"), ("get_artist", PK)])
        names = {r["name"]: r["channel_id"] for r in await self.db.fetchall(
            "SELECT * FROM discover_names")}
        self.assertEqual(names, {"Paul Kelly": PK, "Hilltop Hoods": HH})
        status = await discover.status()
        self.assertEqual((status["library_artists"], status["matched"], status["checked"]),
                         (2, 2, 2))
        # Powderfinger is recommended by both, and so was scanned as well.
        self.assertIn(("get_artist", POWDERFINGER), self.fake.calls)
        self.assertEqual(await self.scan(), 0)    # nothing is due any more

    async def test_new_releases_skip_old_years_and_albums_already_here(self):
        self.setup_artists()
        await self.library(*[f"Paul Kelly/Seventy/{i:02d}.m4a" for i in range(1, 13)],
                           "Hilltop Hoods/State Of The Art/01.opus")
        await self.scan()
        titles = [r["title"] for r in await discover.new_releases()]
        self.assertNotIn("Seventy", titles)           # in the library
        self.assertNotIn("Gossip", titles)             # 1986
        self.assertIn("Rita Wrote A Letter", titles)   # a single is its own release
        self.assertIn("Unreleased", titles)            # from a recommended artist
        mine = [r["title"] for r in await discover.new_releases(source="library")]
        self.assertNotIn("Unreleased", mine)
        owned = await discover.new_releases(include_owned=True)
        self.assertTrue(next(r for r in owned if r["title"] == "Seventy")["in_library"])
        self.assertFalse(any(r["is_new"] for r in owned))   # all seen on the first look

    async def test_clean_and_explicit_editions_are_one_row(self):
        self.setup_artists()
        year = str(time.localtime().tm_year)
        self.fake.pages[PK]["albums"]["results"] += [
            {"browseId": "MPREb_bully_clean", "title": "BULLY", "type": "Album", "year": year},
            {"browseId": "MPREb_bully", "title": "BULLY", "type": "Album", "year": year,
             "isExplicit": True}]
        await self.library(*[f"Paul Kelly/Seventy/{i:02d}.m4a" for i in range(1, 13)])
        await self.scan()
        bully = [r for r in await discover.new_releases() if r["title"] == "BULLY"]
        self.assertEqual([r["browse_id"] for r in bully], ["MPREb_bully"])

    async def test_dismissed_releases_go_with_both_editions_and_come_back(self):
        self.setup_artists()
        year = str(time.localtime().tm_year)
        self.fake.pages[PK]["albums"]["results"] += [
            {"browseId": "MPREb_bully_clean", "title": "BULLY", "type": "Album", "year": year},
            {"browseId": "MPREb_bully", "title": "BULLY", "type": "Album", "year": year,
             "isExplicit": True}]
        await self.library(*[f"Paul Kelly/Seventy/{i:02d}.m4a" for i in range(1, 13)])
        await self.scan()
        titles = lambda rows: {r["title"] for r in rows}
        self.assertEqual(await discover.dismiss_releases(["MPREb_bully", "MPREb_rita"]), 2)
        shown = titles(await discover.new_releases())
        self.assertNotIn("BULLY", shown)                  # not the clean one either
        self.assertNotIn("Rita Wrote A Letter", shown)
        self.assertIn("Fall From The Light", shown)
        self.assertEqual(await discover.dismiss_releases(["MPREb_bully"], dismissed=False), 1)
        bully = [r for r in await discover.new_releases() if r["title"] == "BULLY"]
        self.assertEqual([r["browse_id"] for r in bully], ["MPREb_bully"])

    async def test_a_release_filed_under_a_joint_credit_is_owned(self):
        self.setup_artists()
        await self.library(*[f"Paul Kelly/Seventy/{i:02d}.m4a" for i in range(1, 13)])
        await self.scan()
        # Downloaded since, credited to both artists.
        await self.library("Paul Kelly, Dan Sultan/Rita Wrote A Letter/01.m4a")
        titles = [r["title"] for r in await discover.new_releases()]
        self.assertNotIn("Rita Wrote A Letter", titles)

    async def test_a_downloaded_release_is_hidden_whatever_its_folder(self):
        self.setup_artists()
        await self.library(*[f"Paul Kelly/Seventy/{i:02d}.m4a" for i in range(1, 13)])
        await self.scan()
        await self.db.execute(
            "INSERT INTO jobs (id, source_url, provider, source_type, status, created_at) "
            "VALUES ('j1', 'https://music.youtube.com/browse/MPREb_rita', 'youtube', 'album', "
            "'completed', 't')")
        self.assertNotIn("Rita Wrote A Letter",
                         [r["title"] for r in await discover.new_releases()])
        rita = next(r for r in await discover.new_releases(include_owned=True)
                    if r["title"] == "Rita Wrote A Letter")
        self.assertEqual((rita["downloaded"], rita["in_library"]), (True, False))

    async def test_a_page_is_filtered_and_counted_on_the_server(self):
        self.setup_artists()
        await self.library(*[f"Paul Kelly/Seventy/{i:02d}.m4a" for i in range(1, 13)])
        await self.scan()
        first = await discover.release_page(categories={"album"}, limit=1)
        self.assertEqual((first["total"], len(first["releases"])), (2, 1))
        # Every category's total, whichever are picked; owned ones are hidden.
        self.assertEqual(first["counts"], {"album": 2, "single": 1})
        self.assertEqual(first["owned"], 1)                    # Seventy
        second = await discover.release_page(categories={"album"}, offset=1, limit=1)
        self.assertEqual({first["releases"][0]["title"], second["releases"][0]["title"]},
                         {"Fall From The Light", "Unreleased"})
        rita = await discover.release_page(artist=PK, q="RITA")
        self.assertEqual([r["title"] for r in rita["releases"]], ["Rita Wrote A Letter"])
        every = await discover.release_page(years=0, artist=PK, include_owned=True)
        self.assertEqual({r["title"] for r in every["releases"]},
                         {"Seventy", "Gossip", "Rita Wrote A Letter"})

    async def test_the_sidebar_counts_each_artists_releases(self):
        self.setup_artists()
        await self.library(*[f"Paul Kelly/Seventy/{i:02d}.m4a" for i in range(1, 13)])
        await self.scan()
        counts = {a["name"]: a["releases"] for a in await discover.release_artists()}
        self.assertEqual(counts, {"Hilltop Hoods": 1, "Paul Kelly": 1, "Powderfinger": 1})
        owned = {a["name"]: a["releases"]
                 for a in await discover.release_artists(include_owned=True)}
        self.assertEqual(owned["Paul Kelly"], 2)
        singles = await discover.release_artists(categories={"single"})
        self.assertEqual([(a["name"], a["source"]) for a in singles], [("Paul Kelly", "library")])

    async def test_a_release_that_appears_later_is_new(self):
        self.setup_artists()
        await self.library(*[f"Paul Kelly/Seventy/{i:02d}.m4a" for i in range(1, 13)])
        await self.scan()
        # Two hours pass before the next look.
        await self.db.execute("UPDATE discover_artists SET first_checked = first_checked - 7200, "
                              "next_check = 0 WHERE channel_id = ?", (PK,))
        await self.db.execute("UPDATE discover_releases SET first_seen = first_seen - 7200 "
                              "WHERE channel_id = ?", (PK,))
        year = str(time.localtime().tm_year)
        self.fake.pages[PK]["singles"]["results"].append(
            {"browseId": "MPREb_new", "title": "Brand New", "type": "Single", "year": year})
        await self.scan()
        new = {r["title"]: r["is_new"] for r in await discover.new_releases()}
        self.assertTrue(new["Brand New"])
        self.assertFalse(new["Rita Wrote A Letter"])

    async def test_suggestions_count_who_recommends_them(self):
        self.setup_artists()
        await self.library(*[f"Paul Kelly/Seventy/{i:02d}.m4a" for i in range(1, 13)],
                           "Hilltop Hoods/State Of The Art/01.opus")
        await self.scan()
        found = await discover.suggested_artists()
        names = [s["name"] for s in found]
        self.assertEqual(names[0], "Powderfinger")
        self.assertNotIn("Hilltop Hoods", names)       # already in the library
        self.assertEqual(found[0]["because"], ["Paul Kelly", "Hilltop Hoods"])
        self.assertEqual(found[0]["recent_releases"], 1)
        await discover.dismiss_artist(POWDERFINGER, "Powderfinger")
        self.assertNotIn("Powderfinger", [s["name"] for s in await discover.suggested_artists()])

    async def test_charts_and_genres_are_cached(self):
        doc = await discover.charts("au")
        self.assertEqual((doc["country"], doc["artists"][0]["rank"]), ("AU", 1))
        await discover.charts("AU")
        self.assertEqual(sum(1 for c in self.fake.calls if c[0] == "get_charts"), 1)
        listing = await discover.genres()
        self.assertEqual([g["title"] for g in listing["genres"]], ["Metal"])

    async def test_chart_songs_queue_through_their_album_by_title(self):
        self.fake.albums["MPREb_j"] = {
            "title": "Joseph", "artists": [{"name": "Falling In Reverse"}],
            "tracks": [{"videoId": "atv1", "title": "Joseph", "isAvailable": True,
                        "artists": [{"name": "Falling In Reverse"}]}]}
        picks, loose = await discover.plan_songs([
            {"video_id": "omv1", "title": "Joseph", "artist": "Falling In Reverse",
             "album_id": "MPREb_j"},
            {"video_id": "omv2", "title": "Not On It", "artist": "x", "album_id": "MPREb_j"},
            {"video_id": "omv3", "title": "No Album", "artist": "x", "album_id": None}])
        self.assertEqual(picks, [{"browse_id": "MPREb_j", "video_ids": ["atv1"],
                                  "title": "Joseph", "artist": "Falling In Reverse"}])
        self.assertEqual(sorted(s["video_id"] for s in loose), ["omv2", "omv3"])


if __name__ == "__main__":
    unittest.main()
