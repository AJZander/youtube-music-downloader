"""Which release is this album folder? Cheapest evidence first:

  1. MusicBrainz release ids already in the files' tags
  2. the release Plex matched the album to (its Guid carries the MBID)
  3. ISRCs in the files, looked up on MusicBrainz
  4. a MusicBrainz search on the album title and artist

Every candidate is fetched in full and scored against the files: how many
files find their track (same title, same version, length within 5s — 30 for
an old YouTube upload), how much of the release that covers, and whether the
album title and artist agree. The best scoring candidate wins; its score is
the confidence the rest of the manager acts on.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from ..config import settings
from ..engine.matcher import normalize
from ..library import album_identity, strict_key, title_looks_like_junk
from ..ownership import album_markers, library_keys, title_keys, version_markers
from . import musicbrainz as mb
from .units import folder_of

log = logging.getLogger("mdl.resolver")

UPLOAD_TOLERANCE = 30.0
TRACK_TOLERANCE = 5.0
MAX_FETCH = 5
# Releases scoring within this of each other are told apart by _preference.
TIE = 0.01
# A release none of whose artists is ours keeps this much of its score.
NO_ARTIST_FACTOR = 0.9
_CLEAN = re.compile(r"\b(clean|edited|censored)\b", re.I)


@dataclass
class Resolution:
    status: str                      # resolved | review | unresolved | pending
    release: dict | None = None
    score: float = 0.0
    source: str | None = None
    track_map: dict[str, dict] = field(default_factory=dict)
    candidates: list[dict] = field(default_factory=list)
    error: str | None = None


# ------------------------------------------------------------------ search terms
_FEAT = re.compile(r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s[^\)\]]*[\)\]]"
                   r"|\s+(?:feat\.?|ft\.?|featuring)\s.*$", re.I)
_SPLIT_CREDIT = re.compile(r"\s*(?:,|;|&|\+|/|\bx\b|\band\b|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b)\s*",
                           re.I)


def clean_title(name: str) -> str:
    """An album name as MusicBrainz would know it: no year prefix, no
    bracketed scene/quality tags, no featured credits, no YouTube junk."""
    from ..library import _YOUTUBE_JUNK
    name = re.sub(r"^\s*[\(\[]?\d{4}[\)\]]?\s*[-–—.]?\s*", "", name or "")
    name = re.sub(r"\s*[\[\{][^\]\}]*[\]\}]\s*", " ", name)
    name = _FEAT.sub("", name)
    name = _YOUTUBE_JUNK.sub(" ", name)
    name = re.sub(r"\(\s*\)", "", name)
    return re.sub(r"\s+", " ", name).strip(" -–—") or (name or "").strip()


def primary_artist(credit: str | None) -> str | None:
    if not credit:
        return None
    first = _SPLIT_CREDIT.split(credit, maxsplit=1)[0].strip()
    return first or credit


def search_terms(rows: list[dict]) -> tuple[str, str | None]:
    album = Counter(r.get("album") for r in rows if r.get("album")).most_common(1)
    album_name = album[0][0] if album else folder_of(rows[0]).rsplit("/", 1)[-1]
    artists = Counter(r.get("album_artist") or r.get("artist") for r in rows
                      if r.get("album_artist") or r.get("artist")).most_common(1)
    artist_name = artists[0][0] if artists else rows[0].get("artist_dir")
    artist_name = re.sub(r"\s*-\s*Topic$", "", artist_name or "") or None
    # "(2013) - Ariana Grande - Yours Truly": the artist inside the folder name
    title = clean_title(album_name)
    if artist_name:
        title = re.sub(r"^\s*" + re.escape(artist_name) + r"\s*[-–—]\s*", "", title, flags=re.I)
    return title or album_name, artist_name


# ------------------------------------------------------------------ scoring
def _track_side(track: dict) -> dict:
    if "_keys" not in track:
        track["_keys"] = title_keys(track["title"])
        track["_marks"] = version_markers(track["title"])
    return track


def _with_context(rel: dict, tracks: list[dict]) -> list[dict]:
    """What a track is without saying so: MusicBrainz titles a live album's
    songs plainly, so "Song (Live)" from a live album, or from its "Live at
    the Crocodile Cafe" disc, is still that track."""
    group = rel.get("release-group") or {}
    live = "Live" in (group.get("secondary-types") or [])
    whole = album_markers(rel.get("title"), live=live)
    for track in tracks:
        track["_context"] = whole | album_markers(track.get("medium_title"))
    return tracks


def _file_keys(row: dict) -> set[str]:
    title = row.get("title") or ""
    hints = {normalize(row.get("album_artist") or ""), normalize(row.get("artist") or ""),
             normalize(row.get("artist_dir") or "")}
    keys = set(title_keys(title)) | library_keys(normalize(title), hints)
    # "Artist - Title (Official Video)": the half after the dash
    if " - " in title:
        keys |= title_keys(title.split(" - ", 1)[1])
    return {k for k in keys if k}


def match_tracks(rows: list[dict], tracks: list[dict]) -> dict[str, dict]:
    """path -> release track, one file per track, best evidence first."""
    pairs = []
    for i, row in enumerate(rows):
        keys = _file_keys(row)
        marks = version_markers(row.get("title"))
        duration = row.get("duration_sec")
        tolerance = UPLOAD_TOLERANCE if title_looks_like_junk(row.get("title")) else TRACK_TOLERANCE
        number = row.get("track_number")
        disc = row.get("disc_number") or 1
        for j, track in enumerate(tracks):
            _track_side(track)
            gap = (abs(duration - track["length"])
                   if duration and track.get("length") else None)
            same_number = number is not None and number == track["number"] and disc == track["disc"]
            # the file's version (live, remix…) is the track's, or the release's
            allowed = track["_marks"] | track.get("_context", frozenset())
            if keys & track["_keys"] and track["_marks"] <= marks <= allowed and (
                    gap is None or gap <= tolerance):
                quality = 3.0 + (1.0 if same_number else 0.0) - (gap or 0) / 10
            elif same_number and marks <= allowed and gap is not None and gap <= 2.5:
                # mistitled, but in its place and its length; a title that
                # says "Remix" is not a mistitled original
                quality = 1.5 - gap / 10
            else:
                continue
            pairs.append((quality, i, j))
    pairs.sort(reverse=True)
    used_rows, used_tracks = set(), set()
    out: dict[str, dict] = {}
    for _quality, i, j in pairs:
        if i in used_rows or j in used_tracks:
            continue
        used_rows.add(i)
        used_tracks.add(j)
        out[rows[i]["path"]] = tracks[j]
    return out


def _title_similarity(rows: list[dict], release_title: str, hints: set[str]) -> float:
    wanted = album_identity(release_title, hints)
    have: set[str] = set()
    for row in rows:
        have |= album_identity(row.get("album"), hints)
    have |= album_identity(folder_of(rows[0]).rsplit("/", 1)[-1] if rows else "", hints)
    if wanted & have:
        return 1.0
    a = set(normalize(release_title).split())
    best = 0.0
    for name in {r.get("album") for r in rows if r.get("album")} | {
            folder_of(rows[0]).rsplit("/", 1)[-1]} if rows else set():
        b = set(normalize(name).split())
        if a and b:
            best = max(best, len(a & b) / len(a | b))
    return best


def _artist_similarity(rows: list[dict], rel: dict, mapping: dict | None = None) -> float:
    credits = rel.get("artist-credit") or []
    if any((c.get("artist") or {}).get("id") == mb.VARIOUS_ARTISTS for c in credits):
        return 0.8
    # the release's credit, and the matched tracks' own ("Song (feat. Us)"
    # on someone else's album is still ours)
    every = [credits, *((t.get("credits") or []) for t in (mapping or {}).values())]
    names = set()
    for cs in every:
        names |= {strict_key(n) for n in mb.credit_names(cs)}
        names |= {strict_key((c.get("artist") or {}).get("name") or "") for c in cs}
        names |= {strict_key(mb.credit_string(cs))}
    names.discard("")
    # The files' own artist tags, when they have any: a cover filed in the
    # original artist's folder is not the original.
    tagged, folder = set(), set()
    for row in rows:
        for value, into in ((row.get("album_artist"), tagged), (row.get("artist"), tagged),
                            (row.get("artist_dir"), folder)):
            if value:
                into.add(strict_key(value))
                for part in re.split(r"\s+(?:x|and|&|feat\.?|ft\.?|featuring|with)\s+|\s*[,;/]\s*",
                                     value, flags=re.I):
                    into.add(strict_key(part))
    tagged.discard("")
    folder.discard("")
    ours = tagged or folder
    if names & ours:
        return 1.0
    for n in names:
        for o in ours:
            if len(n) >= 4 and len(o) >= 4 and (n in o or o in n):
                return 0.7
    return 0.0


def score_release(rows: list[dict], rel: dict) -> tuple[float, dict[str, dict]]:
    tracks = _with_context(rel, mb.release_tracks(rel))
    if not tracks or not rows:
        return 0.0, {}
    mapping = match_tracks(rows, tracks)
    hints = {rows[0].get("artist_dir") or ""} | {r.get("album_artist") or "" for r in rows}
    hints |= set(mb.credit_names(rel.get("artist-credit")))
    # A second copy of a track that did match (a re-download, a "(ft. …)"
    # variant beside the plain one) is a duplicate, not evidence against the
    # release: it counts as explained, though it stays unmatched.
    matched_keys = set()
    for track in mapping.values():
        matched_keys |= track["_keys"]
    explained = len(mapping) + sum(1 for row in rows if row["path"] not in mapping
                                   and _file_keys(row) & matched_keys)
    cover_files = explained / len(rows)
    cover_release = len(mapping) / len(tracks)
    title = _title_similarity(rows, rel.get("title") or "", {h for h in hints if h})
    artist = _artist_similarity(rows, rel, mapping)
    score = 0.45 * cover_files + 0.15 * cover_release + 0.25 * title + 0.15 * artist
    if artist == 0.0:
        # Same titles, same lengths, nobody we know: a namesake song or a
        # cover. At most 0.765, so it is never applied without a look.
        score *= NO_ARTIST_FACTOR
    return round(score, 4), mapping


def summarize(rel: dict, score: float, source: str, matched: int) -> dict:
    media = rel.get("media") or []
    formats = Counter(m.get("format") or "?" for m in media)
    fmt = " + ".join(f"{n}×{f}" if n > 1 else f for f, n in formats.items())
    return {
        "release_mbid": rel.get("id"),
        "title": rel.get("title"),
        "artist": mb.credit_string(rel.get("artist-credit")),
        "date": rel.get("date"),
        "country": rel.get("country"),
        "track_count": sum(m.get("track-count") or len(m.get("tracks") or []) for m in media),
        "format": fmt,
        "disambiguation": rel.get("disambiguation") or "",
        "status": rel.get("status"),
        "releasegroup_mbid": (rel.get("release-group") or {}).get("id"),
        "score": score, "source": source, "matched_tracks": matched,
        "url": f"https://musicbrainz.org/release/{rel.get('id')}",
    }


def _date_rank(date: str | None) -> int:
    """Earlier ranks higher; a vaguer date ("1963-05") after an exact one of
    the same period, no date last."""
    parts = re.findall(r"\d+", date or "")
    if not parts or len(parts[0]) != 4:
        return -99_999_999
    year, month, day = (int(parts[i]) if len(parts) > i else 99 for i in range(3))
    return -(year * 10_000 + month * 100 + day)


def _reissue(rel: dict) -> bool:
    first = ((rel.get("release-group") or {}).get("first-release-date") or "")[:4]
    year = (rel.get("date") or "")[:4]
    return first.isdigit() and year.isdigit() and int(year) > int(first)


def _tied(a: dict, b: dict) -> bool:
    """Releases the files can't tell apart: the same tracks matched, and
    either the same tracklist length or only a few songs of a bigger album
    (one song is on the 13-track and the 14-track pressing alike)."""
    if abs(a["score"] - b["score"]) > TIE or a["matched_tracks"] != b["matched_tracks"]:
        return False
    return a["track_count"] == b["track_count"] or (
        2 * a["matched_tracks"] < a["track_count"] and 2 * b["matched_tracks"] < b["track_count"])


def _preference(candidate: dict, file_ids: set[str], plex_id: str | None, n_rows: int):
    """Tie-break between releases the files can't tell apart (pressings of
    one album). The plain original wins: a later pressing would put
    "[1969 Reissue]" or "(Mono, One EMI Box)" on a folder for no reason the
    files can show, and Plex's own pick is often just such a pressing."""
    dis = candidate.get("disambiguation") or ""
    return (
        candidate["release_mbid"] in file_ids,
        not _CLEAN.search(dis),                 # YouTube serves the explicit one
        candidate["track_count"] == n_rows,
        (candidate.get("status") or "").lower() == "official",
        not dis,
        _date_rank(candidate.get("date")),
        candidate["release_mbid"] == plex_id,
        candidate.get("country") in ("XW", "XE"),
    )


# ------------------------------------------------------------------ resolve
async def resolve(rows: list[dict], *, plex_mbid: str | None = None,
                  manual: str | None = None) -> Resolution:
    """Identify the release behind `rows`. `manual` is a release the user
    picked: it is scored like any other but always chosen."""
    if not rows:
        return Resolution(status="unresolved", error="no files")
    if not mb.enabled():
        return Resolution(status="pending", error="MusicBrainz lookups are off "
                                                  "(set MUSICBRAINZ_CONTACT)")
    file_ids = {r["mb_albumid"] for r in rows if r.get("mb_albumid")}
    ordered: list[tuple[str, str]] = []

    def offer(mbid: str | None, source: str) -> None:
        if mbid and mbid not in {m for m, _ in ordered}:
            ordered.append((mbid, source))

    if manual:
        offer(manual, "manual")
    counts = Counter(r["mb_albumid"] for r in rows if r.get("mb_albumid"))
    if counts:
        mbid, n = counts.most_common(1)[0]
        if n >= max(1, len(rows) // 2):
            offer(mbid, "files")
    offer(plex_mbid, "plex")

    fetched: dict[str, tuple[dict, float, dict, str]] = {}
    # A release merged into another on MusicBrainz still answers to its old
    # id, with the surviving release's: everything is keyed by the id it
    # answers with, and this remembers what the old ones became.
    current: dict[str, str] = {}

    async def evaluate(mbid: str, source: str) -> float:
        mbid = current.get(mbid, mbid)
        if mbid in fetched or len(fetched) >= MAX_FETCH + (1 if manual else 0):
            return fetched[mbid][1] if mbid in fetched else 0.0
        try:
            rel = await mb.release(mbid)
        except mb.MusicBrainzError as exc:
            log.info("release %s: %s", mbid, exc)
            return 0.0
        if not rel:
            return 0.0
        rid = current[mbid] = rel.get("id") or mbid
        if rid not in fetched:
            score, mapping = score_release(rows, rel)
            fetched[rid] = (rel, score, mapping, source)
        return fetched[rid][1]

    for mbid, source in ordered:
        await evaluate(mbid, source)

    def settled(at_least: float) -> bool:
        # A good fit that is the files' own tagged release, or an original
        # pressing. Plex's pick alone isn't enough when it is a reissue: the
        # original may fit as well, and it is the one to file the album as.
        return any(score >= at_least and (source == "files" or not _reissue(rel))
                   for rel, score, _, source in fetched.values())

    if not settled(0.9) and not manual:
        isrcs = list(dict.fromkeys(r["isrc"] for r in rows if r.get("isrc")))[:3]
        for isrc in isrcs:
            try:
                ids = await mb.isrc_releases(isrc)
            except mb.MusicBrainzError:
                ids = []
            for mbid in ids[:8]:
                offer(mbid, "isrc")
        album_name, artist_name = search_terms(rows)
        found: list[dict] = []
        seen_ids: set[str] = set()
        # Full credit first, then the primary artist alone ("3%, Fred Leone"
        # is two artists), then the title alone — every hit is still scored
        # against the files, so a looser search can't make a wrong match.
        attempts = [(album_name, artist_name)]
        primary = primary_artist(artist_name)
        if primary and primary != artist_name:
            attempts.append((album_name, primary))
        bare = re.sub(r"\s*\([^)]*\)", "", album_name).strip()
        if bare and bare != album_name:
            # "Lonely (UK Instrumental Download Single)"
            attempts.append((bare, primary or artist_name))
        attempts.append((album_name, None))
        for title, who in attempts:
            try:
                hits = await mb.search_releases(title, who, tracks=len(rows))
            except mb.MusicBrainzError as exc:
                log.info("search failed for %s: %s", title, exc)
                hits = []
            for h in hits:
                if h.get("id") and h["id"] not in seen_ids and int(h.get("score") or 0) >= 60:
                    seen_ids.add(h["id"])
                    found.append(h)
            if len(found) >= 3:
                break
        # cheap pre-rank: MB's own score, then closeness of track count
        found.sort(key=lambda r: (-(int(r.get("score") or 0)),
                                  abs((r.get("track-count") or 0) - len(rows))))
        for rel in found[:8]:
            offer(rel.get("id"), "search")
        for mbid, source in ordered:
            if current.get(mbid, mbid) in fetched:
                continue
            if len(fetched) >= MAX_FETCH:
                break
            await evaluate(mbid, source)
            if settled(0.95):
                break

    if not fetched:
        return Resolution(status="unresolved", error="no candidate release found")

    candidates = [summarize(rel, score, source, len(mapping))
                  for rel, score, mapping, source in fetched.values()]
    file_ids = {current.get(i, i) for i in file_ids}
    plex_id = current.get(plex_mbid, plex_mbid)
    manual = current.get(manual, manual)
    candidates.sort(key=lambda c: c["score"], reverse=True)
    tied = [c for c in candidates if _tied(c, candidates[0])]
    best_pick = max(tied, key=lambda c: _preference(c, file_ids, plex_id, len(rows)))
    candidates.remove(best_pick)
    candidates.insert(0, best_pick)
    chosen_id = manual if manual and manual in fetched else best_pick["release_mbid"]
    rel, score, mapping, source = fetched[chosen_id]
    for c in candidates:
        c["selected"] = c["release_mbid"] == chosen_id
    if manual:
        status = "resolved"
    elif score >= settings.metadata_auto_confidence:
        status = "resolved"
    elif score >= settings.metadata_review_confidence:
        status = "review"
    else:
        status = "unresolved"
    return Resolution(status=status, release=rel, score=score, source=source,
                      track_map=mapping, candidates=candidates[:8])


async def search_candidates(rows: list[dict], query: str | None = None) -> list[dict]:
    """Interactive search for the match picker: MusicBrainz results scored
    against the album's files."""
    if query:
        found = await mb.search_free(query)
    else:
        name, artist = search_terms(rows)
        found = await mb.search_releases(name, artist, tracks=len(rows), limit=15)
        if len(found) < 3 and primary_artist(artist) != artist:
            found += await mb.search_releases(name, primary_artist(artist), tracks=len(rows),
                                              limit=10)
    out = []
    for item in found[:10]:
        try:
            rel = await mb.release(item["id"])
        except mb.MusicBrainzError:
            continue
        if not rel:
            continue
        score, mapping = score_release(rows, rel)
        out.append(summarize(rel, score, "search", len(mapping)))
    out.sort(key=lambda c: -c["score"])
    return out
