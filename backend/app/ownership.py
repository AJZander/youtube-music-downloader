"""Is this track already in the library? One answer for the whole app.

A track is in the library when the same song, in the same version, is there
on the same album. The download dedup gate, the artist browser and the
Discover page all ask, and they have to agree — otherwise the browser promises
a download that the gate then skips.

"The same album" is the rule, not a detail. A song on a compilation, a single
or another edition does not stand in for it on the album being downloaded:
every release arrives whole. A copy on another album is reported as
`elsewhere` — worth showing, never a reason to skip.

"The same song" is wider than an exact artist + title, because a real library
is messier than that:

  - old YouTube-style titles — `Hilltop Hoods - Parade Of The Dead (Official Audio)`
  - track numbers from rips — `04 - Bicycle Race (Live)`
  - other credits — `Paul Kelly & The Messengers` for `Paul Kelly`
  - remasters — `- 2011 Remaster`

held back by two guards: the version must agree (instrumental, live, remix and
the like, read from the title and from the album or folder name), and so must
the length — 5 seconds, or 30 for an old YouTube upload, which carries a
video's intro or trailing silence.
"""
import re

from .db import db
from .engine.matcher import normalize
from .library import (DURATION_TOLERANCE, album_identity, row_album_identity,
                      title_looks_like_junk)

# A live album, by its title. Release classification uses it too.
LIVE_RE = re.compile(
    r"[\(\[]\s*live\b"                    # Goin' Your Way (Live)
    r"|\blive\s+(?:at|in|from|on)\b"      # Live at Sydney Opera House
    r"|^\s*live\s*[,:\-–—]"               # Live, May 1992
    r"|[-–—]\s*live\b"                    # Something - Live
    r"|\bunplugged\b|\bin\s+concert\b"
    r"|(?<!long )\blive\s*$",             # Paul Kelly Live, not Long Live
    re.I)

# Words that make a recording a different recording. A track is only matched
# by a copy carrying the same set — the instrumental edition of an album is
# not the album.
_VERSION_RE = re.compile(
    r"\b(live|acoustic|demo|instrumental|karaoke|remix(?:ed)?|unplugged"
    r"|a\s?cappella|acapella|cover|sped\s?up|slowed|nightcore|orchestral)\b", re.I)
_MARKER_ALIASES = {"remixed": "remix", "acappella": "acapella"}
# The markers an album's title gives every track on it.
_ALBUM_WIDE = frozenset({"instrumental", "karaoke", "acapella", "remix", "acoustic",
                         "orchestral"})
# Applied to normalized text, where "- 2011 Remaster" is " 2011 remaster".
_REMASTER_RE = re.compile(
    r"\s(?:\d{4}\s)?(?:digital(?:ly)?\s)?remaster(?:ed)?(?:\s(?:version|\d{4}))*$")
_TRACK_NUMBER_RE = re.compile(r"^\d{1,3}\s")
# A bracketed version tag — "(Instrumental)", "(Live at the Palais)" — cut
# from a title before comparing, since the markers are compared separately.
_VERSION_TAG_RE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(?:live|acoustic|demo|instrumental|karaoke|remix(?:ed)?"
    r"|unplugged|a\s?cappella|acapella|sped\s?up|slowed|nightcore|orchestral)\b"
    r"[^\)\]]*[\)\]]", re.I)
# The same, once normalization has flattened "(Live)" into a trailing word.
_TRAILING_VERSION_RE = re.compile(
    r"\s(?:live|acoustic|demo|instrumental|karaoke|remix|unplugged|acapella)$")
# Old YouTube uploads run long — a few seconds of silence, a video's intro —
# so a copy with a YouTube-style title gets this much slack instead.
UPLOAD_TOLERANCE = 30.0

_LIKE_ESCAPE = str.maketrans({"%": r"\%", "_": r"\_", "\\": r"\\"})
_COLUMNS = ("norm_artist, norm_title, title, album, norm_album, album_artist, "
            "artist_dir, path, dirpath, duration_sec, provider_track_id")


def version_markers(text: str | None) -> frozenset[str]:
    found = set()
    for m in _VERSION_RE.findall(text or ""):
        key = re.sub(r"\s", "", m.lower())
        found.add(_MARKER_ALIASES.get(key, key))
    return frozenset(found)


def album_markers(album_title: str | None, live: bool = False) -> frozenset[str]:
    marks = set(version_markers(album_title) & _ALBUM_WIDE)
    if live or LIVE_RE.search(album_title or ""):
        marks.add("live")
    return frozenset(marks)


def song_key(normalized_title: str) -> str:
    """A normalized title with the remaster suffix gone — a remaster is the
    same recording."""
    return _REMASTER_RE.sub("", normalized_title or "").strip()


def title_keys(title: str | None) -> set[str]:
    """Keys a release's track title is looked up by: as written, and with any
    version tag cut (`Exit Sign (Instrumental)` -> `exit sign`)."""
    keys = {song_key(normalize(title or "")),
            song_key(normalize(_VERSION_TAG_RE.sub("", title or "")))}
    keys.discard("")
    return keys


def library_keys(norm_title: str, artist_keys) -> set[str]:
    """Every reading of a library title that could be the song's name.

    Each reading is kept, not just the stripped one, so a song really called
    `7 Rings` still matches itself."""
    keys = set()
    norm_title = norm_title or ""
    readings = {norm_title, _TRACK_NUMBER_RE.sub("", norm_title),
                _TRAILING_VERSION_RE.sub("", norm_title)}
    for t in readings:
        keys.add(song_key(t))
        for a in artist_keys:
            if a and t.startswith(a + " "):
                keys.add(song_key(t[len(a) + 1:]))
    keys.discard("")
    return keys


def credits(norm_artist: str, key: str) -> bool:
    """Whether a normalized artist credit names this artist as a whole word
    run: `paul kelly the messengers` credits `paul kelly`."""
    return (norm_artist == key or norm_artist.startswith(key + " ")
            or norm_artist.endswith(" " + key) or f" {key} " in norm_artist)


def _folder(row: dict) -> str:
    return row.get("dirpath") or row["path"].rsplit("/", 1)[0]


def _info(row: dict) -> dict:
    folder = _folder(row)
    return {
        "path": row["path"],
        "folder": folder,
        "duration": row.get("duration_sec"),
        "markers": version_markers(row.get("title") or row.get("norm_title"))
        | album_markers(folder.rsplit("/", 1)[-1]),
        "upload": bool(row.get("title")) and title_looks_like_junk(row["title"]),
        "albums": row_album_identity(row),
    }


def no_match() -> dict:
    return {"library": None, "library_path": None, "library_duration": None,
            "library_markers": [], "library_upload": False, "library_mismatch": False,
            "library_folder": None}


def _match(state: str, info: dict, mismatch: bool = False) -> dict:
    return {"library": state, "library_path": info["path"],
            "library_duration": info["duration"],
            "library_markers": sorted(info["markers"]),
            # The copy is an old YouTube upload, not an album track.
            "library_upload": info["upload"],
            # The copy looks like another recording: other version or length.
            "library_mismatch": mismatch,
            "library_folder": info["folder"]}


class LibraryIndex:
    """Library files for a set of artists, indexed to answer `lookup`."""

    def __init__(self, rows: list[dict], artist_names) -> None:
        self.names = sorted({k for k in (normalize(n or "") for n in artist_names) if k})
        self.by_id: dict[str, list[dict]] = {}
        self.exact: dict[tuple[str, str], list[dict]] = {}
        self.fuzzy: dict[str, list[dict]] = {}
        for row in rows:
            info = _info(row)
            if row.get("provider_track_id"):
                self.by_id.setdefault(row["provider_track_id"], []).append(info)
            self.exact.setdefault((row["norm_artist"], row["norm_title"]), []).append(info)
            if any(credits(row["norm_artist"], n) for n in self.names):
                prefixes = {row["norm_artist"], *self.names}
                keys = library_keys(row["norm_title"], prefixes)
                if row.get("title"):
                    keys |= library_keys(normalize(_VERSION_TAG_RE.sub("", row["title"])),
                                         prefixes)
                for key in keys:
                    self.fuzzy.setdefault(key, []).append(info)

    def lookup(self, *, title: str | None, credit: str | None, album: str | None,
               video_id: str | None = None, length: float | None = None,
               live_album: bool = False) -> dict:
        """have      — this song, this version, on this album: leave it out.
        elsewhere — this song is in the library on another album: download it.
        None      — not in the library.

        With no album to go on (a bare video link, before download), only the
        very same video counts."""
        markers = version_markers(title) | album_markers(album, live=live_album)

        def gap(info):
            if not length or not info["duration"]:
                return 0.0
            return abs(info["duration"] - length)

        def agrees(info):
            return info["markers"] == markers and gap(info) <= (
                UPLOAD_TOLERANCE if info["upload"] else DURATION_TOLERANCE)

        same_video = self.by_id.get(video_id) or [] if video_id else []
        if not album:
            return _match("have", same_video[0]) if same_video else no_match()

        # The old gate's rule — same video, or same artist + title — finds the
        # same song even where the version or length disagree; the wider
        # matching only counts copies that agree on both.
        exact = same_video + self.exact.get(
            (normalize(credit or ""), normalize(title or "")), [])
        fuzzy = sorted({id(c): c for key in title_keys(title)
                        for c in self.fuzzy.get(key, []) if agrees(c)}.values(), key=gap)
        wanted = album_identity(album)

        for info in exact:
            if info["albums"] & wanted:
                return _match("have", info, mismatch=not agrees(info))
        for info in fuzzy:
            if info["albums"] & wanted:
                return _match("have", info)
        elsewhere = [i for i in exact if agrees(i)] + fuzzy + exact
        if elsewhere:
            return _match("elsewhere", elsewhere[0], mismatch=not agrees(elsewhere[0]))
        return no_match()


async def load_index(credits_: set[str], artist_names, video_ids=()) -> LibraryIndex:
    """Index the library files that could match tracks by these credits and
    artists: the gate's exact credits, every credit naming the artist, and
    any file downloaded from one of these videos."""
    rows: dict[str, dict] = {}
    ids = sorted({v for v in video_ids if v})
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in await db.fetchall(
                f"SELECT {_COLUMNS} FROM library_index WHERE "
                f"provider_track_id IN ({','.join('?' * len(chunk))})", tuple(chunk)):
            rows[r["path"]] = dict(r)
    keys = sorted({normalize(c) for c in credits_ if c} - {""})
    names = sorted({normalize(n) for n in artist_names if n} - {""})
    where = [f"norm_artist IN ({','.join('?' * len(keys))})"] if keys else []
    params: list = list(keys)
    for name in names:
        where.append("norm_artist LIKE ? ESCAPE '\\'")
        params.append(f"%{name.translate(_LIKE_ESCAPE)}%")
    if where:
        for r in await db.fetchall(
                f"SELECT {_COLUMNS} FROM library_index WHERE {' OR '.join(where)}",
                tuple(params)):
            rows[r["path"]] = dict(r)
    return LibraryIndex(list(rows.values()), artist_names)
