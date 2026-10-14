"""MusicBrainz release-editor seeding: for an album MusicBrainz doesn't
have, a pre-filled "add release" form built from the files (and the
YouTube Music source, when we downloaded it). Submitting it is the user's
choice; once the release exists, the next check matches it — and so does
Plex, which reads MusicBrainz too.

https://musicbrainz.org/doc/Development/Release_Editor_Seeding
"""
from __future__ import annotations

from collections import Counter

from .units import folder_of

ADD_URL = "https://musicbrainz.org/release/add"


def seed_fields(rows: list[dict], artist_name: str | None = None) -> dict:
    album = Counter(r.get("album") for r in rows if r.get("album")).most_common(1)
    album_name = album[0][0] if album else folder_of(rows[0]).rsplit("/", 1)[-1]
    artist = artist_name or (Counter(r.get("album_artist") or r.get("artist") for r in rows
                                     if r.get("album_artist") or r.get("artist"))
                             .most_common(1) or [(rows[0].get("artist_dir"), 0)])[0][0]
    fields: dict[str, str] = {
        "name": album_name,
        "artist_credit.names.0.name": artist or "",
        "artist_credit.names.0.artist.name": artist or "",
        "status": "official",
        "type": "single" if len(rows) <= 3 else ("ep" if len(rows) <= 6 else "album"),
        "edit_note": "Seeded from a local library by Music Downloader; "
                     "track titles and lengths come from the files.",
    }
    year = Counter((r.get("year") or "")[:4] for r in rows if r.get("year")).most_common(1)
    if year and year[0][0].isdigit():
        fields["date.year"] = year[0][0]
    discs: dict[int, list[dict]] = {}
    for r in rows:
        discs.setdefault(r.get("disc_number") or 1, []).append(r)
    for m, disc in enumerate(sorted(discs)):
        fields[f"mediums.{m}.format"] = "Digital Media"
        tracks = sorted(discs[disc], key=lambda r: (r.get("track_number") or 999, r["path"]))
        for i, r in enumerate(tracks):
            fields[f"mediums.{m}.track.{i}.name"] = r.get("title") or ""
            fields[f"mediums.{m}.track.{i}.number"] = str(r.get("track_number") or i + 1)
            if r.get("duration_sec"):
                fields[f"mediums.{m}.track.{i}.length"] = str(int(r["duration_sec"] * 1000))
            if r.get("artist") and r.get("artist") != artist:
                fields[f"mediums.{m}.track.{i}.artist_credit.names.0.name"] = r["artist"]
    video = next((r.get("provider_track_id") for r in rows if r.get("provider_track_id")), None)
    if video:
        fields["urls.0.url"] = f"https://music.youtube.com/watch?v={video}"
        fields["urls.0.link_type"] = "980"   # streaming page
    return {"action": ADD_URL, "fields": fields}
