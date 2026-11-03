"""What is the same: one recording, one album, one artist, one path, and which
of two copies of a file is the better one. Every part of the app asks here.

About twenty matchers used to answer these questions each its own way (the
dedup gate, the video picker, the resolver, the duplicates finder, the merge
planner, the Skipped list, Discover, the Plex mirror), and they disagreed:
the gate kept a copy 30 s off as the track, the picker took a live take for
the studio song, a `[Single]` folder stood in for its album. The rules:

* **A recording** is the same when MusicBrainz says so (both recording IDs
  known); otherwise when a title key is shared, the versions agree (live,
  instrumental, remix…, read from the title and from the album and folder
  names), and so do the lengths: 5 s, or 30 for an old YouTube upload. Two
  tracks of one release are two recordings, however alike.
* **An album** is the same when both release groups are known and equal
  (and, by edition, the edition words too); otherwise by name, through every
  folder convention, with the year, scene tags, YouTube junk and remasters
  stripped, and edition words and `[Single]`/`[EP]` kept (in any edition,
  a bracketed qualifier is dropped too; the release type still counts).
* **Unsure counts as not the same.** A missed duplicate is harmless; a false
  one deletes a file.

Pure: the stdlib and engine.matcher.normalize, no database and no I/O.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from .engine.matcher import normalize

LOSSLESS_FORMATS = {"flac", "wav", "alac", "aiff", "aif", "ape", "wv"}

# Duration difference that still counts as "the same recording". Wider than
# the matcher's ±3s: these are two rips of the same track from different
# sources, not a match against a known reference duration.
DURATION_TOLERANCE = 5.0
# Old YouTube uploads run long — a few seconds of silence, a video's intro —
# so a copy with a YouTube-style title gets this much slack instead.
UPLOAD_TOLERANCE = 30.0


@dataclass(frozen=True)
class Verdict:
    """same: True, False, or None when nothing says either way, which every
    caller takes as "not the same"; reason: why, for logs and the UI."""
    same: bool | None
    reason: str

    def __bool__(self) -> bool:
        return self.same is True


# ------------------------------------------------------------------ text and paths
_YOUTUBE_JUNK = re.compile(
    r"\b(official\s*(music\s*)?(video|audio|visuali[sz]er)|lyric[s]?\s*video"
    r"|full\s*album|audio\s*only|hd|hq|4k|m/v|mv)\b", re.I)

_DIR_NOISE = [
    re.compile(r"^\s*[\(\[\{]?\s*(19|20)\d{2}\s*[\)\]\}]?\s*[-–—.]+\s*"),  # (2013) - / 2013 -
    re.compile(r"[\[\{][^\]\}]*[\]\}]"),                                    # [FLAC 24bit]
    re.compile(r"\((?:19|20)\d{2}\)"),                                      # (2017)
    re.compile(r"\b(flac|mp3|m4a|aac|opus|wav|web|webrip|cd\d?|cdrip|vinyl|"
               r"\d{2,3}\s?kbps|\d{3}k|v0|v2|\d{2}\s?bit|\d{2}(\.\d)?\s?khz|"
               r"hi\s?res|lossless|obey|scene)\b", re.I),
]


def strict_key(s: str) -> str:
    """Punctuation- and space-insensitive identity key. `AC_DC`, `AC/DC` and
    `ACDC` are one artist; `3 Doors Down` and `3doorsdown` are one artist."""
    s = unicodedata.normalize("NFKD", s or "").casefold()
    return re.sub(r"[^a-z0-9]+", "", s)


def fold(text: str | None) -> str:
    """Case- and accent-blind text to search in: "Måneskin" -> "maneskin"."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def path_key(path: str | None) -> str:
    """One library path however it is spelled: /library is a case-insensitive
    share (`SAM SMITH/` is `Sam Smith/`), and a name may come in either
    Unicode form."""
    return unicodedata.normalize("NFC", path or "").casefold()


def key_text(text: str | None) -> str:
    """A title as compared letter by letter: normalized, a remaster suffix
    cut, its version words kept, and `&` and `/` read as words ("The Sun &
    the Rainfall" is "The Sun and the Rainfall", "Create/Destroy" is
    "Create / Destroy")."""
    text = re.sub(r"\s*/\s*", " ", re.sub(r"\s*&\s*", " and ", text or ""))
    return song_key(normalize(text))


# ------------------------------------------------------------------ artists
# A release with several artists is filed under the whole credit
# ("Will Sparks, AnGy KoRe/Describe") but listed under the one scanned.
_CREDIT_SPLIT = re.compile(
    r"\s+(?:x|and|&|feat\.?|ft\.?|featuring|with)\s+|\s*[,;/]\s*", re.I)


def split_credit(credit: str | None) -> list[str]:
    """The artists one credit names, in order: `Will Sparks, AnGy KoRe`,
    `Paul Kelly & The Messengers`, `SALMO feat. ENIGMA`."""
    return [p.strip() for p in _CREDIT_SPLIT.split(credit or "") if p and p.strip()]


def artist_keys(credit: str | None) -> set[str]:
    """The whole credit's key and each named artist's."""
    if not credit:
        return set()
    keys = {strict_key(credit)} | {strict_key(p) for p in split_credit(credit)}
    keys.discard("")
    return keys


def credits(norm_artist: str, key: str) -> bool:
    """Whether a normalized artist credit names this artist as a whole word
    run: `paul kelly the messengers` credits `paul kelly`."""
    return (norm_artist == key or norm_artist.startswith(key + " ")
            or norm_artist.endswith(" " + key) or f" {key} " in norm_artist)


# ------------------------------------------------------------------ track titles
_FEAT_TAG = re.compile(r"\s*[\(\[]\s*(?:feat|ft|featuring)\b\.?[^\)\]]*[\)\]]", re.I)


def title_keys(title: str | None, artist_hint: str | None = None,
               artists=()) -> frozenset[str]:
    """Comparison keys for one track title.

    v1 wrote whole YouTube video titles into the title tag — `Ariana Grande -
    Baby I (Official Video)` sits next to a clean `Baby I`. Rather than guess
    which side of a dash is the title, emit both readings and call two tracks
    the same when *any* key matches. Matching on a set intersection instead of
    string equality is what lets a junk-tagged folder group with a clean one.
    """
    if not title:
        return frozenset()
    # a bracketed credit goes first: normalize() cuts everything after
    # "feat.", which would take "(Wideboys Remix)" after it along too
    raw = _FEAT_TAG.sub("", _YOUTUBE_JUNK.sub(" ", title))
    keys = {normalize(raw)}
    if artist_hint:
        prefix = re.compile(r"^\s*" + re.escape(artist_hint) + r"\s*[-–—]\s*", re.I)
        keys.add(normalize(prefix.sub("", raw)))
    if " - " in raw or " – " in raw:
        left, right = re.split(r"\s+[-–—]\s+", raw, maxsplit=1)
        # "Artist - Title" from a video, never "Title - Live" or "Kapitel 2 -
        # Chronicles, Vol. 1": what follows the dash there is shared by
        # every track of the album
        left_key = strict_key(left)
        names = {strict_key(a) for a in (artist_hint, *artists) if a}
        if title_looks_like_junk(title) or any(
                n == left_key or (len(n) >= 4 and n in left_key) for n in names if n):
            keys.add(normalize(right))
            if re.search(r"\b(?:feat|ft)\b", left, re.I):
                # "SALMO FEAT. ENIGMA - MUNCHIES": normalize() cuts at the
                # credit, leaving only the artist as the whole title's key
                keys.discard(normalize(raw))
                if artist_hint:
                    keys.discard(normalize(prefix.sub("", raw)))
    return frozenset(k for k in keys if k)


def keys_for(row: dict) -> frozenset[str]:
    """title_keys for a scanned row, memoized on the row itself.

    Album grouping compares every pair of folders an artist owns, so an
    un-memoized call recomputes the same regex work thousands of times for a
    prolific artist. The cache lives on the dict, which is per-analysis."""
    cached = row.get("_keys")
    if cached is None:
        cached = row["_keys"] = title_keys(
            row.get("title"), row.get("album_artist") or row.get("artist_dir"),
            (row.get("artist"), row.get("album_artist"), row.get("artist_dir")))
    return cached


def title_looks_like_junk(title: str | None) -> bool:
    """True for a title tag that is really a YouTube video title."""
    if not title:
        return True
    return bool(_YOUTUBE_JUNK.search(title)) or bool(
        re.search(r"\b(ft\.?|feat\.?)\b", title, re.I) and " - " in title)


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
    r"\b(live|acoustic|demo|instrumental|instr(?=\.)|karaoke|remix(?:ed)?|unplugged"
    r"|a\s?cappella|acapella|cover|sped\s?up|slowed|nightcore|orchestral)\b", re.I)
_MARKER_ALIASES = {"remixed": "remix", "acappella": "acapella", "instr": "instrumental"}
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
# A qualifier that names some of an album's tracks, not what all of them are:
# "While the World Was Burning (Includes "Roses (Imanbek Remix)")", "Dog 'n'
# Bone (With Additional Instrumental & A Cappella Versions Of Nobody)". A
# bare "with" is a credit: "Middle of Love (with Dean Lewis acoustic)" is
# the acoustic single, "(With You Remix)" a remix. One level of brackets
# may nest in it.
_SOME_TRACKS = re.compile(
    r"[\(\[]\s*(?:(?:includes?|including|incl|plus)\b"
    r"|with\s+(?:additional|bonus|extra)\b)"
    r"(?:[^\(\)\[\]]|[\(\[][^\(\)\[\]]*[\)\]])*[\)\]]", re.I)


def version_markers(text: str | None) -> frozenset[str]:
    found = set()
    for m in _VERSION_RE.findall(text or ""):
        key = re.sub(r"\s", "", m.lower())
        found.add(_MARKER_ALIASES.get(key, key))
    return frozenset(found)


def album_markers(album_title: str | None, live: bool = False) -> frozenset[str]:
    title = _SOME_TRACKS.sub(" ", album_title or "")
    marks = set(version_markers(title) & _ALBUM_WIDE)
    if live or LIVE_RE.search(title):
        marks.add("live")
    return frozenset(marks)


# What a release-group type says of every track on the release.
_TYPE_MARKERS = {"Live": "live", "Remix": "remix"}


def release_context(title: str | None, secondary_types=(), medium_title: str | None = None
                    ) -> frozenset[str]:
    """What a release's tracks are without their titles saying so: the
    album-wide versions its title names, its release-group type (Live,
    Remix: MusicBrainz titles a live album's songs plainly), and its
    medium's title (a "Live at the Crocodile Cafe" disc)."""
    marks = set(album_markers(title)) | {m for t, m in _TYPE_MARKERS.items()
                                         if t in (secondary_types or ())}
    return frozenset(marks | album_markers(medium_title))


def song_key(normalized_title: str) -> str:
    """A normalized title with the remaster suffix gone — a remaster is the
    same recording."""
    return _REMASTER_RE.sub("", normalized_title or "").strip()


def release_title_keys(title: str | None) -> set[str]:
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


def recording_keys(row: dict) -> frozenset[str]:
    """Every key a track or file is known by, from all the readings above:
    a release's title, a file's YouTube-style title, a rip's track number.
    Memoized on the row."""
    cached = row.get("_recording_keys")
    if cached is None:
        title = row.get("title") or ""
        hints = {normalize(h) for h in (row.get("album_artist"), row.get("artist"),
                                        row.get("artist_dir")) if h}
        cached = frozenset(
            keys_for(row) | release_title_keys(title) | library_keys(normalize(title), hints)
            | library_keys(normalize(_VERSION_TAG_RE.sub("", title)), hints))
        row["_recording_keys"] = cached
    return cached


# ------------------------------------------------------------------ versions and lengths
_CLEAN_WORDS = frozenset({"clean", "edited", "censored"})


def context_markers(*names: str | None) -> frozenset[str]:
    """What an album's names say of every track on it: album-wide versions
    (`The Great Expanse (Instrumental Edition)`, `Disc 2 - Live`), and a
    clean album. Clean is read from a name's qualifiers only (`X [Clean]`):
    a song may be called Clean."""
    marks: set[str] = set()
    for name in names:
        marks |= album_markers(name)
    if edition_words(*names) & _CLEAN_WORDS:
        marks.add("clean")
    return frozenset(marks)


def row_markers(row: dict) -> frozenset[str]:
    """The version a track or file is: its title's version words and its
    album's and folder's. Memoized on the row."""
    cached = row.get("_markers")
    if cached is None:
        path = row.get("path") or ""
        folder = row.get("dirpath") or (path.rsplit("/", 1)[0] if "/" in path else "")
        cached = row["_markers"] = (
            version_markers(row.get("title") or row.get("norm_title"))
            | context_markers(row.get("album"), folder.rsplit("/", 1)[-1] if folder else None))
    return cached


def markers_agree(a: frozenset, b: frozenset, a_allows: frozenset = frozenset(),
                  b_allows: frozenset = frozenset()) -> bool:
    """Whether two sides are one version: what each says, the other says
    too, or allows. An allowance is what a side's release is without every
    title saying so: a live release titles its songs plainly, so "Song
    (Live)" is still its track, and a plain "Song" is still its "Song - Live
    at Budokan" when it is a file of that very release."""
    return a <= b | b_allows and b <= a | a_allows


def lengths_agree(a: float | None, b: float | None, youtube_upload: bool = False) -> bool:
    """Two lengths of one recording: within 5 s, or 30 when one is an old
    YouTube upload. An unknown length agrees."""
    if not a or not b:
        return True
    return abs(a - b) <= (UPLOAD_TOLERANCE if youtube_upload else DURATION_TOLERANCE)


def same_length(a: dict, b: dict) -> bool:
    """lengths_agree for two files: an edited vinyl cut, a cover in the
    artist's folder or another mix runs longer or shorter. A YouTube upload
    (by its title) gets slack for its intro."""
    return lengths_agree(a.get("duration_sec"), b.get("duration_sec"),
                         youtube_upload=title_looks_like_junk(a.get("title"))
                         or title_looks_like_junk(b.get("title")))


def two_tracks(a: dict, b: dict) -> bool:
    """Two tracks of one release, however alike their titles: the release
    lists both (a clean and an explicit take, "ON" and "ON" feat. Sia)."""
    if a.get("mb_albumid") and a.get("mb_albumid") == b.get("mb_albumid") \
            and a.get("mb_releasetrackid") and b.get("mb_releasetrackid"):
        return a["mb_releasetrackid"] != b["mb_releasetrackid"]
    if unit_dir(a.get("dirpath") or "") == unit_dir(b.get("dirpath") or "") \
            and a.get("dirpath") and a.get("track_number") and b.get("track_number"):
        return ((a.get("disc_number") or 1, a["track_number"])
                != (b.get("disc_number") or 1, b["track_number"]))
    return False


def _recording_id(row: dict) -> str | None:
    return row.get("mb_trackid") or row.get("recording_mbid") or None


def same_recording(a: dict, b: dict) -> Verdict:
    """Whether two tracks or files hold one recording. Each side is a row:
    title, duration_sec, album, path (or dirpath), and any MusicBrainz ids;
    `live` for a release whose type is Live though its name doesn't say so."""
    if two_tracks(a, b):
        return Verdict(False, "two tracks of one release")
    ra, rb = _recording_id(a), _recording_id(b)
    if ra and rb:
        return Verdict(ra == rb, "one recording ID" if ra == rb else "two recording IDs")
    if not recording_keys(a) & recording_keys(b):
        return Verdict(False, "different titles")
    ma, mb = row_markers(a), row_markers(b)
    live = frozenset({"live"})
    if not markers_agree(ma, mb, live if a.get("live") else frozenset(),
                         live if b.get("live") else frozenset()):
        return Verdict(False, f"different versions ({', '.join(sorted(ma ^ mb))})")
    if not same_length(a, b):
        return Verdict(False, f"lengths differ by {abs(a['duration_sec'] - b['duration_sec']):.0f} s")
    return Verdict(True, "same title, version and length")


# ------------------------------------------------------------------ album names
def album_key(folder_or_album: str | None, artist_hint: str | None = None) -> str:
    """Normalized album identity from a folder name or an album tag, with the
    release-year prefixes, scene tags and repeated artist name stripped."""
    if not folder_or_album:
        return ""
    name = folder_or_album
    if artist_hint:
        name = re.sub(r"^\s*" + re.escape(artist_hint) + r"\s*[-–—]\s*", " ", name, flags=re.I)
    for pattern in _DIR_NOISE:
        name = pattern.sub(" ", name)
    if artist_hint:  # the year prefix may have been hiding the artist name
        name = re.sub(r"^\s*" + re.escape(artist_hint) + r"\s*[-–—]\s*", " ", name, flags=re.I)
    return strict_key(name)


# A remaster is the same album; a deluxe or anniversary edition is not.
_ALBUM_REMASTER = re.compile(
    r"\s*[\(\[][^\)\]]*\bremaster(?:ed)?\b[^\)\]]*[\)\]]"
    r"|\s*[-–—:]\s*(?:\d{4}\s+)?(?:digital(?:ly)?\s+)?remaster(?:ed)?\b.*$", re.I)

# Words in a name's brackets or dash suffix that make it another edition of
# the album. Remasters and reissues are the same album, so they are not here.
_EDITION_WORDS = re.compile(
    r"\b(deluxe|expanded|special|complete|anniversary|collector|super|platinum|bonus"
    r"|international|instrumental|acoustic|remix(?:es)?|uncut|live|japan(?:ese)?"
    r"|extended|tour|clean|edited|censored|mono|limited)\b", re.I)
# A release's type, said in its name: `[Single]`, `(EP)`, `- Single`.
# (only a qualifier that is just the type: "[2019 Limited Edition, Bonus
# Gold Single]" names a bonus disc, not a single)
_TYPE_WORD = re.compile(r"^\s*(single|ep)\s*$", re.I)
_TRAILING_TYPE = re.compile(r"\s(single|ep)\s*$", re.I)
_TYPE_QUALIFIER = re.compile(r"\s*[\(\[\{]\s*(?:single|ep)\s*[\)\]\}]"
                             r"|\s+[-–—]\s+(?:single|ep)\s*$", re.I)
TYPE_WORDS = frozenset({"single", "ep"})
_QUALIFIERS = re.compile(r"[\(\[]([^\)\]]*)[\)\]]|\s[-–—]\s+([^-–—]*)$")
# "Wild Ones (feat. Sia)": a featured credit names a single, not its album
_CREDIT = re.compile(r"^\s*(?:feat|ft|featuring)\b", re.I)
# A bracketed qualifier that names an edition or a release type: kept, as
# if it were in parentheses, where scene and quality tags are dropped.
_SQUARE = re.compile(r"[\[\{]([^\]\}]*)[\]\}]")


def _keep_square(m: re.Match) -> str:
    inner = m.group(1)
    return f" ({inner}) " if _EDITION_WORDS.search(inner) or _TYPE_WORD.search(inner) else " "


def album_keys(name: str | None, artist_hints=(), *, qualifiers: str = "keep") -> frozenset[str]:
    """Keys an album name is known by — two names are the same album when
    any key is shared.

    One key per artist hint, since a folder may or may not repeat the artist
    (`(2013) - Ariana Grande - Yours Truly`). YouTube junk (`Full Album`) and
    remaster tags go first, and so do the year, scene and quality tags.
    Edition words stay, because `Seventy (Deluxe Edition)` is a different
    album from `Seventy`, and so does a release type: `The Boys Light Up
    [Single]` is not the album. qualifiers="strip" drops every bracket, as
    resolution and the YouTube Music search always have."""
    if not name:
        return frozenset()
    base = _ALBUM_REMASTER.sub("", _YOUTUBE_JUNK.sub(" ", name))
    if qualifiers == "keep":
        base = _SQUARE.sub(_keep_square, base)
    keys = {album_key(base)} | {album_key(base, h) for h in artist_hints if h}
    keys.discard("")
    return frozenset(keys)


def album_identity(name: str | None, artist_hints=()) -> frozenset[str]:
    """album_keys with every bracketed qualifier dropped: what resolution and
    the YouTube Music search compare names by."""
    return album_keys(name, artist_hints, qualifiers="strip")


def _row_album_names(row: dict, qualifiers: str) -> frozenset[str]:
    path = row.get("path") or ""
    top = path.split("/", 1)[0]
    hints = {row.get("artist_dir"), row.get("album_artist"), top}
    folder = row.get("dirpath") or path.rsplit("/", 1)[0]
    keys = album_keys(row.get("album"), hints, qualifiers=qualifiers) | album_keys(
        row.get("norm_album"), hints, qualifiers=qualifiers)
    if folder and folder != top:   # a loose file's folder is the artist
        keys |= album_keys(folder.rsplit("/", 1)[-1], hints, qualifiers=qualifiers)
    return keys


def row_album_keys(row: dict) -> frozenset[str]:
    """What album a library file is on: its album tag and, unless it sits
    loose in the artist folder, the folder it is in. Memoized on the row."""
    cached = row.get("_album_keys")
    if cached is None:
        cached = row["_album_keys"] = _row_album_names(row, "keep")
    return cached


def row_album_identity(row: dict) -> frozenset[str]:
    """row_album_keys with every bracketed qualifier dropped. Memoized."""
    cached = row.get("_albums")
    if cached is None:
        cached = row["_albums"] = _row_album_names(row, "strip")
    return cached


_DISC_DIR = re.compile(r"^(?:cd|disc|disk)\s*\d+\b", re.I)


def unit_dir(dirpath: str) -> str:
    """The album folder a file folder belongs to: a disc subfolder's parent
    (never an artist folder: `Artist/CD1` stays its own)."""
    parent, _, leaf = (dirpath or "").rpartition("/")
    return parent if "/" in parent and _DISC_DIR.match(leaf) else dirpath


def _qualifiers(names, hints):
    for name in names:
        name = _DIR_NOISE[0].sub("", fold(name))
        for hint in hints:
            if hint:
                name = re.sub(r"^\s*" + re.escape(fold(hint)) + r"\s*[-–—]\s*", "", name)
        yield from _QUALIFIERS.finditer(name)


def edition_words(*names: str | None, hints=()) -> frozenset[str]:
    """What edition a name says it is: `Album (Deluxe Edition)` and `(2017)
    Album [Deluxe]` say deluxe, `Album [2011 Remaster]` says nothing. Only
    qualifiers count, so a title's own words (`Live Through This`, `1988 -
    Live at Wembley`) never do."""
    found = set()
    for m in _qualifiers(names, hints):
        if _CREDIT.match(m.group(1) or ""):
            found.add("single")
        for word in _EDITION_WORDS.findall(m.group(1) or m.group(2) or ""):
            word = word.lower()
            found.add("remix" if word.startswith("remix")
                      else "japan" if word.startswith("japan") else word)
    return frozenset(found)


def type_words(*names: str | None, hints=()) -> frozenset[str]:
    """The release type a name says it is: `[Single]`, `(EP)`, `- Single`,
    `Night Thinker EP`. A featured credit doesn't count here (edition_words
    reads `(feat. X)` as a single, for the duplicates finder)."""
    found = {w.lower() for m in _qualifiers(names, hints)
             for w in _TYPE_WORD.findall(m.group(1) or m.group(2) or "")}
    for name in names:
        m = _TRAILING_TYPE.search(name or "")
        if m:
            found.add(m.group(1).lower())
    return frozenset(found)


def row_edition(row: dict) -> frozenset[str]:
    """The edition a file's album is, by its tag and its album folder, and
    the release type they say (a `[Single]` folder tagged plainly is the
    single)."""
    cached = row.get("_edition")
    if cached is None:
        path = row.get("path") or ""
        artist_dir = row.get("artist_dir") or path.split("/", 1)[0]
        # a file the importer indexed has no dirpath until the next scan
        folder = unit_dir(row.get("dirpath") or (path.rsplit("/", 1)[0] if "/" in path else ""))
        leaf = folder.rsplit("/", 1)[-1] if folder and folder != artist_dir else ""
        hints = (artist_dir, row.get("album_artist"))
        types = row["_types"] = type_words(row.get("album"), leaf, hints=hints)
        cached = row["_edition"] = edition_words(row.get("album"), leaf, hints=hints) | types
    return cached


def row_types(row: dict) -> frozenset[str]:
    """The release type a file's album tag or album folder says."""
    row_edition(row)
    return row["_types"]


# ------------------------------------------------------------------ albums
@dataclass(frozen=True)
class AlbumId:
    """One album as a matcher sees it: the keys its names give, the edition
    (and release type) they say, the release type they say (`types`), its
    release group when known, its type when the source says (a YouTube
    Music single), and the keys with every bracketed qualifier dropped
    (`names`: what it is called in any edition)."""
    keys: frozenset = frozenset()
    edition: frozenset = frozenset()
    group: str | None = None
    kind: str | None = None
    types: frozenset = frozenset()
    names: frozenset = frozenset()


def album_id(name: str | None, hints=(), *, group: str | None = None,
             kind: str | None = None) -> AlbumId:
    """An album known by a name: a YouTube Music release, an album tag. A
    release known to be a single or EP answers to its `[Single]` folder too."""
    kind = (kind or "").lower() or None
    kind = kind if kind in TYPE_WORDS else None
    keys = album_keys(name, hints)
    names = album_identity(name, hints)
    if kind:
        # "WHO" is its "WHO [EP]" folder, and "The Lost and Damned [EP]" its
        # plain one
        plain = _TYPE_QUALIFIER.sub("", name or "")
        keys |= {k + t for k in keys for t in TYPE_WORDS} | album_keys(plain, hints)
        names |= {k + t for k in names for t in TYPE_WORDS} | album_identity(plain, hints)
    types = type_words(name, hints=hints)
    return AlbumId(keys, edition_words(name, hints=hints) | types, group or None, kind, types,
                   names)


def row_album_id(row: dict) -> AlbumId:
    """The album a library file is on (row_album_keys, row_edition)."""
    return AlbumId(row_album_keys(row), row_edition(row), row.get("mb_releasegroupid") or None,
                   None, row_types(row), row_album_identity(row))


def _as_album(side) -> AlbumId:
    if isinstance(side, AlbumId):
        return side
    if isinstance(side, dict):
        return row_album_id(side) if "path" in side else album_id(
            side.get("name"), side.get("hints") or (), group=side.get("group"),
            kind=side.get("kind"))
    return album_id(side)


def _editions_agree(a: AlbumId, b: AlbumId) -> bool:
    """The same edition words. A release known to be a single or EP is
    filed under its plain name or as `[Single]`/`[EP]`, so its type words
    don't count; one only named so has to meet its like."""
    if a.kind or b.kind:
        return a.edition - TYPE_WORDS == b.edition - TYPE_WORDS
    return a.edition == b.edition


def _types_agree(a: AlbumId, b: AlbumId) -> bool:
    return bool(a.kind or b.kind) or a.types == b.types


def same_album(a, b, policy: str = "edition") -> Verdict:
    """Whether two sides are one album. Each is an AlbumId, a name, or a
    library row.

      release_group  one release group is one album, whatever the edition;
                     by name, a bracketed qualifier is dropped too
                     (`Sempiternal [Deluxe Edition]` is filed as
                     `Sempiternal`)
      edition        …only in the same edition (`Rolling Papers` is not
                     `Rolling Papers (Deluxe Version)`)

    Both release groups known decide it (a single and the album named
    after it are two groups); else the names do, and a single or EP by name
    is only ever its like."""
    a, b = _as_album(a), _as_album(b)
    if a.group and b.group:
        if a.group != b.group:
            return Verdict(False, "different release groups")
        if policy == "release_group" or a.edition - TYPE_WORDS == b.edition - TYPE_WORDS:
            return Verdict(True, "one release group")
        return Verdict(False, "another edition of the release group")
    if not a.keys or not b.keys:
        return Verdict(None, "no album name to compare")
    if not (a.keys & b.keys or policy == "release_group" and a.names & b.names):
        return Verdict(False, "different album names")
    if not _types_agree(a, b):
        return Verdict(False, "a single or EP is not the album")
    if policy == "edition" and not _editions_agree(a, b):
        return Verdict(False, "another edition")
    return Verdict(True, "one album name")


# What a folder's files may disagree on and still be one album: an edition
# or disc suffix ("Against All Oddz" / "Against All Oddz (Collector's
# Edition)", "Album (Disc 2)").
_SUFFIX = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$|\s*[-–—:,]?\s*\b(?:disc|cd|disk)\s*\d+\s*$", re.I)
# …but not one that names other recordings: a live album, an instrumental
# edition, the remixes, a re-recording ("1989 (Taylor's Version)"). Unless
# the tag names a disc: "Bad 25 (CD 3: Live At Wembley…)" is one disc of the
# one release.
_OTHER_RECORDINGS = re.compile(
    r"\b(?:live|instrumental|karaoke|a\s?cappella|acapella|remix(?:es|ed)?|acoustic"
    r"|orchestral|unplugged)\b|\btaylor\W?s\s+version\b", re.I)
_A_DISC = re.compile(r"\b(?:disc|cd|disk)\s*\d+", re.I)


def split_key(album: str | None, hint: str | None) -> str:
    """Which album of a folder a file's album tag puts it in."""
    name, kept, disc = album or "", "", False
    for _ in range(3):
        m = _SUFFIX.search(name)
        stripped = name[:m.start()].strip() if m else name
        if not m or not stripped:
            break
        disc = disc or bool(_A_DISC.search(m.group(0)))
        if _OTHER_RECORDINGS.search(m.group(0)):
            kept = m.group(0) + kept
        name = stripped
    return album_key(name, hint) + ("" if disc else strict_key(kept))


# ------------------------------------------------------------------ recordings of a release
def plain_recordings(rel: dict | None) -> set[str]:
    """The recordings of a release that MusicBrainz knows in one version
    only. One it tells apart from others ("original mono studio mix", "2017
    stereo mix", "live") may be any of them in our files: when the release
    was picked among pressings the files can't tell apart (mono or stereo,
    the same tracklist), the recording is a guess, and no guess is a reason
    to call two files one."""
    out = set()
    for medium in (rel or {}).get("media") or []:
        for t in medium.get("tracks") or []:
            rec = t.get("recording") or {}
            if rec.get("id") and not (rec.get("disambiguation") or "").strip():
                out.add(rec["id"])
    return out


def matched_recordings(desired: dict) -> dict[str, list[str]]:
    """recording MBID -> the files of an identified album holding it."""
    out: dict[str, list[str]] = {}
    for path, t in (desired.get("tracks") or {}).items():
        if t.get("matched") and t.get("recording_mbid"):
            out.setdefault(t["recording_mbid"], []).append(path)
    return out


# ------------------------------------------------------------------ file quality
def format_quality(row: dict) -> int:
    fmt = (row.get("format") or "").lower()
    if fmt in LOSSLESS_FORMATS:
        score = 1000
        if (row.get("bit_depth") or 16) > 16:
            score += 60
        if (row.get("sample_rate") or 44100) > 48000:
            score += 30
        return score
    return min(row.get("bitrate_kbps") or 0, 400)


def tag_quality(row: dict) -> int:
    score = 0
    if row.get("album_artist"):
        score += 40
    if row.get("album"):
        score += 40
    if row.get("track_number"):
        score += 30
    if row.get("year"):
        score += 10
    if not title_looks_like_junk(row.get("title")):
        score += 40
    return score


def file_quality(row: dict, reference: float | None = None, *,
                 tags: bool = True) -> tuple[int, list[str]]:
    """Rank one copy of a track. Returns (score, human-readable reasons).

    Quality beats tags: lossless outranks any bitrate, and the only thing
    that outranks lossless is evidence the file is truncated (shorter than
    `reference`, the other copies' length, by over 10 s). `tags` adds what
    the tags are worth, which breaks a tie: the merge of copies leaves them
    out, since the copy kept gets the album's tags anyway.

    The reasons are the point: the UI has to be able to say *why* it wants to
    delete the file you spent bandwidth on, or it will not be trusted.
    """
    reasons: list[str] = []
    fmt = (row.get("format") or "?").lower()
    quality = format_quality(row)
    if fmt in LOSSLESS_FORMATS:
        detail = f"lossless {fmt}"
        if (row.get("bit_depth") or 0) > 16:
            detail += f" {row['bit_depth']}-bit"
        if (row.get("sample_rate") or 0) > 48000:
            detail += f" {row['sample_rate'] // 1000}kHz"
        reasons.append(detail)
    elif row.get("bitrate_kbps"):
        reasons.append(f"{row['bitrate_kbps']} kbps {fmt}")
    else:
        reasons.append(fmt)

    score = quality
    duration = row.get("duration_sec")
    if reference and duration:
        delta = duration - reference
        if delta < -DURATION_TOLERANCE * 2:
            score -= 900
            reasons.append(f"{abs(round(delta))}s shorter than the other copies "
                           "— likely truncated")
        elif delta > 20:
            reasons.append(f"{round(delta)}s longer (extended version or trailing silence)")

    if not tags:
        return score, reasons
    score += tag_quality(row)
    missing = [name for name, key in
               (("album artist", "album_artist"), ("album", "album"),
                ("track number", "track_number")) if not row.get(key)]
    if missing:
        reasons.append("missing " + ", ".join(missing))
    if title_looks_like_junk(row.get("title")):
        reasons.append("title is a YouTube video title")
    return score, reasons


# ------------------------------------------------------------------ the assignment matcher
# Mistitled tracks: in their place and their length ("Track 3"), or a title
# all but the same ("Came" for "Come"), within these.
PLACE_TOLERANCE = 2.5
NEAR_TITLE = 0.75
_NUMERAL = re.compile(r"^(?:\d+|i{1,3}|iv|vi{0,3}|ix|xi{0,3}|xiv|xvi{0,3}|xix|xx)$")


def side(title: str | None, length: float | None = None, *, number: int | None = None,
         disc: int | None = 1, keys=None, marks=None, upload: bool = False,
         context=()) -> dict:
    """One track as assign() sees it: a release's track (`context`: what
    the release is without saying so in the title, a Live type or a medium
    titled "Live at …"), a file, or a YouTube Music track."""
    return {"keys": frozenset(release_title_keys(title) if keys is None else keys),
            "marks": version_markers(title) if marks is None else frozenset(marks),
            "text": key_text(title), "length": length or None, "number": number,
            "disc": disc or 1, "upload": upload, "context": frozenset(context)}


def near_identical(a: str, b: str) -> bool:
    """`Come` and `Came`, `All Bout U` and `All About U`: as many words, at
    least three letters in four alike, and no number told apart (`Part I`
    is not `Part II`)."""
    if not a or not b:
        return False
    ta, tb = a.split(), b.split()
    if len(ta) != len(tb):
        return False
    if any(x != y and (_NUMERAL.match(x) or _NUMERAL.match(y)) for x, y in zip(ta, tb)):
        return False
    return SequenceMatcher(None, a, b).ratio() >= NEAR_TITLE


def assign(wanted: list[dict], held: list[dict]) -> list[tuple[int, int]]:
    """(wanted index, held index) for each held track that is a wanted one,
    one to one, best evidence first (sides from side()).

    The versions agree at every rank: neither says a version (live,
    remix…) the other doesn't, unless the wanted track's release allows it
    (a Live release, a "Live at …" disc). Then, best first:

      A  a title key shared and the lengths agree (5 s, 30 for an upload);
         the same place on the release counts for it
      B  the same place and length (2.5 s): mistitled, but in its place
      C  a near-identical title and the length (5 s)
    """
    pairs = []
    for i, h in enumerate(held):
        for j, w in enumerate(wanted):
            # both sides are of the release: its context allows either way
            if not markers_agree(w["marks"], h["marks"], w["context"], w["context"]):
                continue
            gap = (abs(h["length"] - w["length"])
                   if h["length"] and w["length"] else None)
            same_number = (h["number"] is not None and h["number"] == w["number"]
                           and h["disc"] == w["disc"])
            if h["keys"] & w["keys"] and lengths_agree(h["length"], w["length"],
                                                       youtube_upload=h["upload"]):
                quality = 3.0 + (1.0 if same_number else 0.0) - (gap or 0) / 10
            elif same_number and gap is not None and gap <= PLACE_TOLERANCE:
                quality = 1.5 - gap / 10
            elif gap is not None and gap <= DURATION_TOLERANCE \
                    and near_identical(w["text"], h["text"]):
                quality = 1.0 - gap / 10
            else:
                continue
            pairs.append((quality, i, j))
    pairs.sort(reverse=True)
    used_held, used_wanted = set(), set()
    out = []
    for _quality, i, j in pairs:
        if i in used_held or j in used_wanted:
            continue
        used_held.add(i)
        used_wanted.add(j)
        out.append((j, i))
    return out
