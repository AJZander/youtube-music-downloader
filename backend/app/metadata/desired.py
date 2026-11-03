"""The desired state of an album and its artist: what the tags should say,
where the files should live, and how Plex should describe it.

House rules (the user's):
  * album artist = the primary artist (MusicBrainz's first credited artist),
    one name per artist: the one the library's folders already use, else
    MusicBrainz's ("Eskimo Callboy" is Electric Callboy); the track artist is the credit as
    released ("Drake feat. Rihanna"); a Various Artists release is
    "Various Artists". Typographic punctuation is made plain ("Wu‐Tang"
    with a Unicode hyphen would be a second artist beside "Wu-Tang").
  * layout: Artist/(Original Year) Album [Edition]/NN - Title.ext, the
    edition only when the release is a later reissue or remaster. A folder
    that already exists keeps its spelling: the share ignores case.
  * genres: MusicBrainz genre names, from the release group (then release),
    then Last.fm tags that are MusicBrainz genres, then the artist's, then
    whatever Plex already has. Up to METADATA_MAX_GENRES.
  * moods: Plex's own where it has them; otherwise Last.fm tags mapped onto
    Plex's mood vocabulary, then the artist's.
  * anything pinned by the user (md_overrides) wins over all of it.
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path

from ..config import settings
from ..db import db
from ..engine import tagio
from ..library import strict_key
from ..paths import PathBuildError, album_folder_name, build_track_relpath, sanitize_component
from . import lastfm
from . import musicbrainz as mb
from .plexsync import jlist
from .resolver import Resolution
from .vocab import display_genre, genres_from_tags, moods_from_tags

log = logging.getLogger("mdl.desired")

_REISSUE_WORDS = re.compile(r"remaster|reissue|re-release|anniversary|expanded|legacy", re.I)
# A variant worth its own folder: different content, or a different market's.
# Pressing notes ("mono, 1st press", "one EMI box", "Ltd.") describe a copy
# the files can't show they are, so they stay off folder names.
_VARIANT_WORDS = re.compile(r"\b(?:deluxe|expanded|edition|version|bonus|special\b|international|"
                            r"instrumental|acoustic|remix|collector|platinum|uncut|"
                            r"anniversary)", re.I)
# What rides along in a disambiguation without naming an edition: formats,
# content flags, a "with …" aside ("re-release with Ultra catalog number").
_EDITION_NOISE = re.compile(r"\b\d+\s*bit\b|\b\d+(?:\.\d+)?\s*khz\b|\bexplicit\b|\bclean\b"
                            r"|\s(?:with|on|from|for|by)\s.*$|\(\w{2}\)", re.I)
_IGNORED_DISAMBIGUATION = re.compile(r"^\s*(explicit|clean|censored|uncensored|"
                                     r"digital|digital media|cd|vinyl)\s*$", re.I)


async def overrides_for(entity: str, ref: str) -> dict:
    rows = await db.fetchall(
        "SELECT field, value FROM md_overrides WHERE entity = ? AND ref = ?", (entity, ref))
    return {r["field"]: json.loads(r["value"]) for r in rows}


_PLAIN = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
                        "\u2014": "-", "\u2015": "-", "\u2212": "-", "\u2018": "'",
                        "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u02bc": "'",
                        "\u2032": "'", "\u201c": '"', "\u201d": '"', "\u201e": '"',
                        "\u2033": '"', "\u2026": "...", "\u00a0": " "})


def plain(text: str | None) -> str | None:
    """MusicBrainz's typographic punctuation as everyone types it ("25ᵗʰ"
    or a full-width colon as plain characters), and no invisible format
    characters ("La\U000e0031ufey" is Laufey)."""
    if not text:
        return text
    text = unicodedata.normalize("NFKC", text.translate(_PLAIN))
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def _fold(name: str) -> str:
    return unicodedata.normalize("NFC", plain(name) or "").casefold()


def _words(name: str) -> str:
    """An artist folder's name as words: "SAM SMITH", "Art vs Science",
    "Art vs. Science" and "AC_DC" (for AC/DC) are one artist's; a run-together
    "ARTVSSCIENCE" or "3doorsdown" is not a name to keep."""
    return " ".join(re.findall(r"[^\W_]+", _fold(name)))


_artist_dirs: dict = {"at": 0.0, "by_words": {}}


async def _existing_artists() -> dict[str, tuple[str, int]]:
    """The library's artist folders and how many tracks each holds, by _words()."""
    if time.monotonic() - _artist_dirs["at"] > 300:
        by_words: dict[str, tuple[str, int]] = {}
        for r in await db.fetchall("SELECT artist_dir, COUNT(*) AS n FROM library_index "
                                   "WHERE artist_dir IS NOT NULL GROUP BY artist_dir"):
            key = _words(r["artist_dir"])
            if key not in by_words or r["n"] > by_words[key][1]:
                by_words[key] = (r["artist_dir"], r["n"])
        _artist_dirs["by_words"] = by_words
        _artist_dirs["at"] = time.monotonic()
    return _artist_dirs["by_words"]


async def on_disk(folder: str, other_names: tuple[str, ...] = ()) -> str:
    """`folder` as the library already spells it. The share ignores case,
    so "Sam Smith/…" would land in the existing "SAM SMITH" folder anyway,
    under a path nothing here knows about. A release credited to "Alanis"
    files with "Alanis Morissette" (`other_names`: the artist's listed
    name) when that folder exists. A folder the index doesn't know yet is
    spelled as the share has it (relocate.on_disk, which the importer
    uses too)."""
    from .. import relocate
    artist, _, album = folder.partition("/")
    known = await _existing_artists()
    existing = (known.get(_words(name)) for name in (artist, *other_names))
    artist = next((hit[0] for hit in existing if hit), artist)
    for r in await db.fetchall("SELECT DISTINCT dirpath FROM library_index WHERE artist_dir = ?",
                               (artist,)):
        parts = r["dirpath"].split("/")
        if len(parts) >= 2 and _fold(parts[1]) == _fold(album):
            return f"{artist}/{parts[1]}"
    return await relocate.on_disk(f"{artist}/{album}")


def _said_in(label: str, title: str) -> bool:
    """Whether the album's own title already carries its edition: "The
    Better Life (20th anniversary deluxe)" needs no "[2021 20th Anniversary,
    Deluxe]", and "Big Overdrive (Deluxe)" no "[2023 Reissue]"."""
    words = strict_key(re.sub(r"\b(?:19|20)\d\d\b|\breissue\b", "", label, flags=re.I))
    if not words:
        return bool(_VARIANT_WORDS.search(title) or _REISSUE_WORDS.search(title))
    return words in strict_key(title)


def _title_case(text: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in text.split())


def _edition_words(dis: str) -> str:
    """The parts of a disambiguation that name an edition: "20th anniversary
    reissue, translucent yellow" is the 20th anniversary reissue; matrix
    numbers, colours and stores are pressing notes."""
    keep = []
    for part in re.split(r"[,;/]|\s+-\s+|\.\s", dis):
        part = re.sub(r"\s+", " ", _EDITION_NOISE.sub(" ", part)).strip(" .-_:;")
        if part and (_REISSUE_WORDS.search(part) or _VARIANT_WORDS.search(part)):
            keep.append(part)
    return ", ".join(keep)


def edition_of(rel: dict, original_year: str | None) -> tuple[str | None, str | None]:
    """(edition for the [brackets], qualifier for (parentheses) after the
    album name). A reissue gets an edition so it is told apart from the
    original; other release variants (a country edition, say) get a
    qualifier, which keeps them a different album to the duplicate finder."""
    # "2005 digitally remastered, explicit": the explicit/clean marker is a
    # content flag, not an edition
    dis = ", ".join(p.strip() for p in re.split(r"[,/]", rel.get("disambiguation") or "")
                    if p.strip() and not _IGNORED_DISAMBIGUATION.match(p.strip()))
    year = (rel.get("date") or "")[:4]
    reissue = bool(year and original_year and year.isdigit() and original_year.isdigit()
                   and int(year) > int(original_year))
    if dis and _IGNORED_DISAMBIGUATION.match(dis):
        dis = ""
    dis = _edition_words(dis) if dis else ""
    qualifier = _title_case(dis) if dis and _VARIANT_WORDS.search(dis) else None
    if reissue:
        if dis and _REISSUE_WORDS.search(dis):
            label = _title_case(dis)
            if not re.search(r"\b(?:19|20)\d\d\b", label):    # "2001 Remaster" says when
                label = f"{year} {label}"
            return label, None
        return f"{year} Reissue", qualifier
    if dis and _REISSUE_WORDS.search(dis):
        return _title_case(dis), None
    return None, qualifier


def album_artist_of(rel: dict) -> dict:
    credits = rel.get("artist-credit") or []
    if not credits or any((c.get("artist") or {}).get("id") == mb.VARIOUS_ARTISTS
                          for c in credits):
        return {"name": "Various Artists", "mbid": mb.VARIOUS_ARTISTS,
                "sort": "Various Artists", "various": True,
                "credit": mb.credit_string(credits) or "Various Artists"}
    first = credits[0].get("artist") or {}
    # MusicBrainz's name for the artist, not the release's credit: "Eskimo
    # Callboy" and "Madilyn" are Electric Callboy and Madilyn Bailey, one
    # artist in Plex. library_name() then prefers the name the library uses.
    listed = plain(first.get("name")) or ""
    credited = plain(credits[0].get("name")) or listed
    return {"name": listed or credited, "mbid": first.get("id"),
            "sort": plain(first.get("sort-name") or listed or credited),
            "listed": listed or credited, "credited": credited,
            "various": False, "credit": plain(mb.credit_string(credits))}


async def library_name(aa: dict) -> dict:
    """One name per artist, the one the library already files them under:
    of MusicBrainz's name, the release's credit and the artist's other names,
    the one whose artist folder holds the most ("Kanye West", 900 tracks,
    over a "Ye" folder of seven, though MusicBrainz lists him as Ye now);
    otherwise MusicBrainz's name."""
    if aa["various"]:
        return aa
    known = await _existing_artists()
    if not known:
        return aa
    names = [(aa["listed"], aa["sort"]), (aa.get("credited"), None)]
    if aa.get("mbid"):
        try:
            aliases = await mb.artist_aliases(aa["mbid"])
        except mb.MusicBrainzError:
            aliases = []
        names += [(plain(a["name"]), plain(a.get("sort-name"))) for a in aliases]
    best = None
    for name, sort in names:
        hit = known.get(_words(sanitize_component(name))) if name else None
        if hit and (best is None or hit[1] > best[2]):
            best = (name, sort, hit[1])
    if best is None:
        return aa
    return {**aa, "name": best[0], "sort": best[1] or best[0]}


async def artist_profile(mbid: str | None, name: str, plex_artist: dict | None = None) -> dict:
    """Genres and moods for an artist, by the same fallback rules."""
    limit = settings.metadata_max_genres
    genres: list[str] = []
    source = None
    entity = None
    if mbid and mbid != mb.VARIOUS_ARTISTS:
        try:
            entity = await mb.artist(mbid)
        except mb.MusicBrainzError:
            entity = None
        genres = [display_genre(g) for g, _ in mb.top_genres(entity)][:limit]
        source = "musicbrainz" if genres else None
    lfm_tags = await lastfm.artist_tags(name) if name and name != "Various Artists" else []
    if not genres and lfm_tags:
        genres = [display_genre(g) for g in await genres_from_tags(lfm_tags, limit)]
        source = "lastfm" if genres else None
    if not genres and plex_artist:
        genres = [g for g in jlist(plex_artist.get("genres")) if not tagio.junk_genre(g)][:limit]
        source = "plex" if genres else None
    moods = jlist(plex_artist.get("moods")) if plex_artist else []
    mood_source = "plex" if moods else None
    if not moods and lfm_tags:
        moods = await moods_from_tags(lfm_tags, settings.metadata_max_moods)
        mood_source = "lastfm" if moods else None
    return {"mbid": mbid, "entity": entity, "genres": genres, "genre_source": source,
            "moods": moods, "mood_source": mood_source}


# The tags a file carries as one track of its release; the rest are the album's.
TRACK_FIELDS = ("title", "artist", "artists", "tracknumber", "totaltracks", "discnumber",
                "isrc", "mb_artistid", "mb_trackid", "mb_releasetrackid")


def track_tags(album_tags: dict, rel: dict, t: dict, row: dict) -> dict:
    """A file's tags as track `t` (a mb.release_tracks entry) of `rel`: the
    album's tags, and the track's own."""
    credits = t["credits"] or rel.get("artist-credit") or []
    isrc = row.get("isrc") if row.get("isrc") in t["isrcs"] else (
        t["isrcs"][0] if t["isrcs"] else None)
    return {
        **album_tags,
        "title": [plain(t["title"])],
        "artist": [plain(mb.credit_string(credits))],
        "artists": [plain(n) for n in mb.credit_names(credits)],
        "tracknumber": [str(t["number"])],
        "totaltracks": [str(t["disc_tracks"])] if t.get("disc_tracks") else [],
        "discnumber": [str(t["disc"])],
        "isrc": [isrc] if isrc else [],
        "mb_artistid": mb.credit_ids(credits),
        "mb_trackid": [t["recording_id"]] if t.get("recording_id") else [],
        "mb_releasetrackid": [t["track_id"]] if t.get("track_id") else [],
    }


def album_tags_of(desired: dict) -> dict:
    """The album-level tags of an album's desired state: any of its files'
    tags without the track's own (a matched file's first; every file
    carries them)."""
    ordered = sorted((desired.get("tracks") or {}).values(), key=lambda t: not t.get("matched"))
    for t in ordered:
        if t.get("tags"):
            return {k: v for k, v in t["tags"].items() if k not in TRACK_FIELDS}
    return {}


async def build_album(rows: list[dict], res: Resolution, *, plex_album: dict | None,
                      plex_artist: dict | None, overrides: dict) -> dict:
    rel = res.release
    rg_id = (rel.get("release-group") or {}).get("id")
    rg = None
    if rg_id:
        try:
            rg = await mb.release_group(rg_id)
        except mb.MusicBrainzError:
            rg = None
    aa = await library_name(album_artist_of(rel))
    album_title = plain(rel.get("title") or "")
    original_date = ((rg or {}).get("first-release-date")
                     or (rel.get("release-group") or {}).get("first-release-date")
                     or rel.get("date") or "")
    original_year = original_date[:4] or None
    edition, qualifier = edition_of(rel, original_year)
    qualifier = None if qualifier is None or _said_in(qualifier, album_title) else plain(qualifier)
    edition = (None if edition is None or _said_in(edition, f"{album_title} {qualifier or ''}")
               else plain(edition))
    group = rel.get("release-group") or rg or {}
    types = [t.lower() for t in [group.get("primary-type"), *(group.get("secondary-types") or [])]
             if t]
    compilation = aa["various"] or "compilation" in types
    # a label the tag writer would strip as junk ("Siccness.net", "nugs.net")
    # is never wanted: written, it would be planned again every check
    label = next((li.get("label", {}).get("name") for li in rel.get("label-info") or []
                  if (li.get("label") or {}).get("name")
                  and li["label"]["name"].lower() not in ("[no label]", "no label")
                  and not tagio.spam(li["label"]["name"])), None)
    media = rel.get("media") or []

    # ---- genres
    limit = settings.metadata_max_genres
    counts: Counter = Counter()
    for name, votes in mb.top_genres(rg) + mb.top_genres(rel):
        counts[name] += votes or 1
    genres = [display_genre(n) for n, _ in counts.most_common(limit)]
    genre_source = "musicbrainz" if genres else None
    lfm_album = []
    if aa["name"] and not aa["various"]:
        lfm_album = await lastfm.album_tags(aa["name"], album_title)
    if not genres and lfm_album:
        genres = [display_genre(g) for g in await genres_from_tags(lfm_album, limit)]
        genre_source = "lastfm" if genres else None
    profile = None
    if not genres or not (plex_album and jlist(plex_album.get("moods"))):
        profile = await artist_profile(aa["mbid"], aa["name"], plex_artist)
    if not genres and profile and profile["genres"]:
        genres, genre_source = profile["genres"], "artist"
    if not genres and plex_album:
        genres = [g for g in jlist(plex_album.get("genres")) if not tagio.junk_genre(g)][:limit]
        genre_source = "plex" if genres else None

    # ---- moods
    moods = jlist(plex_album.get("moods")) if plex_album else []
    mood_source = "plex" if moods else None
    if not moods and lfm_album:
        moods = await moods_from_tags(lfm_album, settings.metadata_max_moods)
        mood_source = "lastfm" if moods else None
    if not moods and profile and profile["moods"]:
        moods, mood_source = profile["moods"], "artist"

    # ---- overrides
    if "genres" in overrides:
        genres, genre_source = list(overrides["genres"]), "override"
    if "moods" in overrides:
        moods, mood_source = list(overrides["moods"]), "override"
    if overrides.get("album"):
        album_title = overrides["album"]
    if overrides.get("album_artist"):
        aa = {**aa, "name": overrides["album_artist"], "sort": overrides["album_artist"]}
    if overrides.get("year"):
        original_year = str(overrides["year"])[:4]

    folder_album = f"{album_title} ({qualifier})" if qualifier else album_title
    try:
        artist_folder = sanitize_component(aa["name"])
        album_folder = album_folder_name(folder_album, original_year, edition)
        others = (sanitize_component(aa["listed"]),) if aa.get("listed") else ()
        folder = await on_disk(f"{artist_folder}/{album_folder}", others)
    except PathBuildError:
        folder = None

    album_tags = {
        "album": [album_title], "albumartist": [aa["name"]],
        "albumartistsort": [aa["sort"]] if aa.get("sort") else [],
        # a release without a date keeps the first release's: players show
        # the date tag's year, and the index reads the year from
        # originaldate when a file has no date, so "no date" never sticks
        "date": [rel.get("date") or original_date] if rel.get("date") or original_date else [],
        "originaldate": [original_date] if original_date else [],
        "genre": genres, "label": [label] if label else [],
        "releasetype": types, "compilation": ["1"] if compilation else [],
        "totaldiscs": [str(len(media))] if len(media) > 1 else [],
        "mb_albumid": [rel["id"]], "mb_releasegroupid": [rg_id] if rg_id else [],
        "mb_albumartistid": [aa["mbid"]] if aa.get("mbid") else [],
    }

    tracks: dict[str, dict] = {}
    multi_disc = len(media) > 1
    for row in rows:
        path = row["path"]
        ext = Path(path).suffix
        t = res.track_map.get(path)
        if t is None:
            # On the album but not on this release: album-level fields only,
            # and it moves with the album under its own name.
            target = f"{folder}/{Path(path).name}" if folder else None
            tracks[path] = {"matched": False, "target": target,
                            "tags": dict(album_tags)}
            continue
        tags = track_tags(album_tags, rel, t, row)
        title, credit = tags["title"][0], tags["artist"][0]
        try:
            name = build_track_relpath(
                aa["name"], folder_album, title, ext, t["number"],
                t["disc"] if multi_disc else None, year=original_year, edition=edition).name
            target = f"{folder}/{name}" if folder else None
        except PathBuildError:
            target = None
        tracks[path] = {
            "matched": True, "target": target, "title": title,
            "artist": credit, "tracknumber": t["number"],
            "discnumber": t["disc"], "recording_mbid": t.get("recording_id"),
            "releasetrack_mbid": t.get("track_id"), "tags": tags,
        }

    return {
        "release_mbid": rel["id"], "releasegroup_mbid": rg_id,
        "album": album_title, "albumartist": aa["name"], "albumartist_mbid": aa.get("mbid"),
        "albumartistsort": aa.get("sort"), "credit": aa.get("credit"),
        "various": aa["various"],
        "date": rel.get("date"), "original_date": original_date,
        "original_year": original_year, "edition": edition, "qualifier": qualifier,
        "releasetype": types, "compilation": compilation, "label": label,
        "totaldiscs": len(media), "genres": genres, "genre_source": genre_source,
        "moods": moods, "mood_source": mood_source, "folder": folder,
        "score": res.score, "source": res.source, "tracks": tracks,
        "country": rel.get("country"), "status": rel.get("status"),
    }


async def build_artist(artist_dir: str, albums: list[dict], plex_artist: dict | None,
                       overrides: dict) -> dict:
    """The artist's desired record, from what its albums resolved to."""
    ids = Counter(a.get("albumartist_mbid") for a in albums
                  if a.get("albumartist_mbid") and not a.get("various"))
    mbid = ids.most_common(1)[0][0] if ids else None
    name = None
    if mbid:
        name = next((a["albumartist"] for a in albums if a.get("albumartist_mbid") == mbid),
                    None)
    elif mb.enabled():
        from .resolver import primary_artist
        # "3%, Fred Leone" is a folder named for a collaboration: the artist
        # is the first name in it.
        for candidate in dict.fromkeys([artist_dir, primary_artist(artist_dir)]):
            if not candidate:
                continue
            try:
                found = await mb.search_artists(candidate)
            except mb.MusicBrainzError:
                found = []
            key = strict_key(candidate)
            exact = [a for a in found if (strict_key(a.get("name") or "") == key
                                          or any(strict_key(al.get("name") or "") == key
                                                 for al in a.get("aliases") or []))
                     and int(a.get("score") or 0) >= 90]
            if len(exact) == 1:
                mbid, name = exact[0]["id"], exact[0]["name"]
                break
    profile = await artist_profile(mbid, name or artist_dir, plex_artist)
    entity = profile.pop("entity", None) or {}
    out = {
        "name": name or (plex_artist or {}).get("title") or artist_dir,
        "mbid": mbid, "sort": entity.get("sort-name"),
        "country": entity.get("country") or (entity.get("area") or {}).get("name"),
        "status": "resolved" if mbid else ("unknown" if mb.enabled() else "pending"),
        "musicbrainz_url": f"https://musicbrainz.org/artist/{mbid}" if mbid else None,
        **profile,
    }
    if "genres" in overrides:
        out["genres"], out["genre_source"] = list(overrides["genres"]), "override"
    if "moods" in overrides:
        out["moods"], out["mood_source"] = list(overrides["moods"]), "override"
    return out
