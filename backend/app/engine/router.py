"""URL routing: pure functions that classify a pasted URL. Expansion into
individual tracks happens in the worker (network calls live there)."""
import re
from dataclasses import dataclass

SPOTIFY_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}(?:-[A-Z]{2})?/)?(track|album|playlist|artist)/([A-Za-z0-9]+)"
)
YT_WATCH_RE = re.compile(r"(?:youtube\.com/watch\?|youtu\.be/|music\.youtube\.com/watch\?)")
YT_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})")
YT_PLAYLIST_ID_RE = re.compile(r"[?&]list=([A-Za-z0-9_-]+)")
YT_BROWSE_ALBUM_RE = re.compile(r"music\.youtube\.com/browse/(MPREb_[A-Za-z0-9_-]+)")
YT_CHANNEL_RE = re.compile(r"(?:music\.)?youtube\.com/(channel/(UC[A-Za-z0-9_-]+)|@([A-Za-z0-9_.-]+))")


@dataclass
class RoutedURL:
    provider: str      # spotify | youtube_music | youtube
    source_type: str   # track | album | playlist | artist
    entity_id: str
    canonical_url: str


class UnsupportedURL(ValueError):
    pass


def route(url: str) -> RoutedURL:
    url = url.strip()

    m = SPOTIFY_RE.search(url)
    if m:
        kind, sid = m.group(1), m.group(2)
        return RoutedURL("spotify", kind, sid, f"https://open.spotify.com/{kind}/{sid}")

    m = YT_BROWSE_ALBUM_RE.search(url)
    if m:
        return RoutedURL("youtube_music", "album", m.group(1),
                         f"https://music.youtube.com/browse/{m.group(1)}")

    if YT_WATCH_RE.search(url):
        vid = YT_VIDEO_ID_RE.search(url)
        if vid:
            video_id = vid.group(1)
            # A watch URL with a list param is still routed as the single video —
            # radio/playlist context params caused triple extractions in v1.
            provider = "youtube_music" if "music.youtube.com" in url else "youtube"
            return RoutedURL(provider, "track", video_id,
                             f"https://music.youtube.com/watch?v={video_id}")

    m = YT_PLAYLIST_ID_RE.search(url)
    if m and "watch" not in url:
        list_id = m.group(1)
        # OLAK5uy_ = auto-generated album playlist; RD* = radio (rejected)
        if list_id.startswith("RD"):
            raise UnsupportedURL("Radio/mix playlists are endless — queue the song or album instead")
        source_type = "album" if list_id.startswith("OLAK5uy_") else "playlist"
        return RoutedURL("youtube_music", source_type, list_id,
                         f"https://music.youtube.com/playlist?list={list_id}")

    m = YT_CHANNEL_RE.search(url)
    if m:
        entity = m.group(2) or m.group(3)
        return RoutedURL("youtube_music", "artist", entity, url)

    raise UnsupportedURL(f"Unrecognized URL: {url[:120]}")
