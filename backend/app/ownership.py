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
from . import identity
from .db import db
from .engine.matcher import normalize
# The rules are identity's; these names stay for the callers that have
# always asked here.
from .identity import (  # noqa: F401
    DURATION_TOLERANCE, LIVE_RE, UPLOAD_TOLERANCE, _VERSION_TAG_RE, album_markers, credits,
    library_keys, song_key, title_looks_like_junk, version_markers)
from .identity import release_title_keys as title_keys

_LIKE_ESCAPE = str.maketrans({"%": r"\%", "_": r"\_", "\\": r"\\"})
_COLUMNS = ("norm_artist, norm_title, title, album, norm_album, album_artist, "
            "artist_dir, path, dirpath, duration_sec, provider_track_id")


def _folder(row: dict) -> str:
    return row.get("dirpath") or row["path"].rsplit("/", 1)[0]


def _info(row: dict) -> dict:
    folder = _folder(row)
    return {
        "path": row["path"],
        "folder": folder,
        "duration": row.get("duration_sec"),
        # its title's version, and its album tag's and folder's
        "markers": identity.row_markers(row),
        "upload": bool(row.get("title")) and title_looks_like_junk(row["title"]),
        "albums": identity.row_album_id(row),
    }


def no_match() -> dict:
    return {"library": None, "library_path": None, "library_duration": None,
            "library_markers": [], "library_upload": False, "library_mismatch": False,
            "library_folder": None, "library_via": None, "library_same_album": False}


def _match(state: str, info: dict, mismatch: bool = False, via: str | None = None,
           same_album: bool = True) -> dict:
    return {"library": state, "library_path": info["path"],
            "library_duration": info["duration"],
            "library_markers": sorted(info["markers"]),
            # The copy is an old YouTube upload, not an album track.
            "library_upload": info["upload"],
            # The copy looks like another recording: other version or length.
            "library_mismatch": mismatch,
            "library_folder": info["folder"],
            # How it was found: "video", "exact title" or "fuzzy".
            "library_via": via,
            # The copy is on this album (an "elsewhere" one is then another
            # version or length of the track).
            "library_same_album": same_album}


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
                    # "SALMO FEAT. ENIGMA - MUNCHIES": the half after the credit
                    keys |= identity.title_keys(row["title"], row.get("album_artist")
                                                or row.get("artist_dir"),
                                                (row.get("album_artist"), row.get("artist_dir")))
                for key in keys:
                    self.fuzzy.setdefault(key, []).append(info)

    def lookup(self, *, title: str | None, credit: str | None, album: str | None,
               video_id: str | None = None, length: float | None = None,
               live_album: bool = False, kind: str | None = None) -> dict:
        """have      — this song, this version, on this album: leave it out.
        elsewhere — this song is in the library on another album, or in
                    another version or length on this one: download it.
        None      — not in the library.

        `live_album`: the release is live though its name may not say so;
        `kind`: the release's type when the source says ("single", "ep",
        "album"…), None when nothing does (a Spotify track, a pasted link).
        With no album to go on (a bare video link, before download), only
        the very same video counts."""
        markers = version_markers(title) | identity.context_markers(album)
        # a live album titles its songs plainly: a plain copy there is its track
        allows = frozenset({"live"}) if live_album else frozenset()

        def gap(info):
            if not length or not info["duration"]:
                return 0.0
            return abs(info["duration"] - length)

        def agrees(info):
            return identity.markers_agree(markers, info["markers"], allows) \
                and identity.lengths_agree(info["duration"], length,
                                           youtube_upload=info["upload"])

        same_video = self.by_id.get(video_id) or [] if video_id else []
        if not album:
            return _match("have", same_video[0], via="video") if same_video else no_match()

        # The same video is the identical audio: on this album it is the
        # track, whatever its tags say. The same artist + title is the track
        # only when the version and length agree too (a copy 30 s off, the
        # disc 2 live take, is another recording: the album's one downloads),
        # and so is the wider matching.
        same_title = self.exact.get((normalize(credit or ""), normalize(title or "")), [])
        exact = same_video + same_title
        fuzzy = sorted({id(c): c for key in title_keys(title)
                        for c in self.fuzzy.get(key, []) if agrees(c)}.values(), key=gap)
        wanted = identity.album_id(album, kind=kind)
        # Its very video in a [Single] or [EP] folder of its name may be that
        # single when nothing says what type the release is (a Spotify id is
        # one release's track); a release known to be an album still isn't
        # its single (YouTube Music lists one video on both).
        video_album = identity.album_id(album, kind="single") if kind is None else wanted

        def via(info):
            if any(info is c for c in same_video):
                return "video"
            return "exact title" if any(info is c for c in same_title) else "fuzzy"

        def on_album(info, as_=wanted) -> bool:
            # by name, in any edition: what another edition of the release
            # group holds is not downloaded again (a [Single] is not its album)
            return bool(identity.same_album(as_, info["albums"], "release_group"))

        for info in exact:
            if via(info) == "video" and on_album(info, video_album) \
                    or on_album(info) and agrees(info):
                return _match("have", info, mismatch=not agrees(info), via=via(info))
        for info in fuzzy:
            if on_album(info):
                return _match("have", info, via="fuzzy")
        elsewhere = [i for i in exact if agrees(i)] + fuzzy + exact
        if elsewhere:
            return _match("elsewhere", elsewhere[0], mismatch=not agrees(elsewhere[0]),
                          via=via(elsewhere[0]), same_album=on_album(elsewhere[0]))
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
