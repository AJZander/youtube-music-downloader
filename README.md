# Music Downloader v2

Self-hosted music downloader feeding a Plex library. Paste a YouTube / YouTube
Music / Spotify link (song, album, artist, or playlist) and correctly tagged,
best-quality audio lands in the right place in the Plex music library.

Full rebuild of the v1 `youtube-music-downloader` — see `../downloader` (frozen legacy).

## Architecture

| Service | Role |
|---|---|
| `mdl-app` | FastAPI API + download/import worker (yt-dlp + ytmusicapi matcher + mutagen) |
| `mdl-web` | React UI served by nginx; proxies `/api/` to mdl-app (single origin, no CORS) |
| `mdl-pot` | bgutil PO-token provider sidecar for yt-dlp's YouTube anti-bot tokens |

Key design points:

- **Staging-first**: downloads land in `./staging` (local disk) as flat id-keyed
  files; only the importer writes to the library, building `Artist/Album/NN - Title.ext`
  paths in Python from *verified tags*. Missing tags = quarantined, never "Unknown Album".
- **Quality**: format `141/774/251/140` — YouTube Premium 256k AAC first (needs
  cookies), stream-copied (never transcoded) into native `.m4a`/`.opus`.
- **Spotify links** = metadata source only. Tracks are matched to YouTube Music
  with an ISRC + duration (±3s) + title/artist verification gate; audio downloads
  from YT Music; tags/cover come from Spotify; synced lyrics from LRCLIB as `.lrc`.
- **Rate safety**: single global governor — 1 concurrent download, jittered
  5–20s spacing, long pause every 25 tracks, 60/hour + 300/day caps, typed
  429/403/bot detection, exponential global backoff + circuit breaker.
- **Dedup** via a `library_index` table scanned from the real library — not
  yt-dlp's download archive.
- **CIFS-aware importing**: dot-prefixed temp file + fsync + checksum + rename,
  EIO retry ladder, and a `.mdl-library-online` sentinel so a dropped NAS mount
  pauses imports instead of writing into a hole. Plex partial scan per album
  (CIFS has no inotify), then verification badge.
- **yt-dlp freshness**: nightly channel, upgraded in a persistent venv volume at
  every container start + daily in-app check with idle restart.

## Operating

```bash
docker compose up -d --build      # start everything
docker compose logs -f mdl-app    # worker logs
```

UI at `http://<host>:3000` (put your authenticating reverse proxy in front).

### YouTube cookies (Premium quality + bot-check immunity)

1. Open a **private/incognito** window, log in to youtube.com.
2. In that same tab open `https://www.youtube.com/robots.txt`.
3. Export cookies **for youtube.com only** (e.g. "Get cookies.txt LOCALLY" extension).
4. Save as `data/cookies/youtube-cookies.txt`, then close the private window
   (this stops YouTube rotating the exported cookies).
5. `docker compose restart mdl-app` — health check should show `youtube_authenticated: true`.

The engine only ever hands yt-dlp a working *copy*; the exported file is never modified.

### CLI (inside the container)

```bash
docker compose exec mdl-app /opt/engine-venv/bin/python -m app.cli canary          # extractor + cookie health
docker compose exec mdl-app /opt/engine-venv/bin/python -m app.cli download <url>  # end-to-end single URL
docker compose exec mdl-app /opt/engine-venv/bin/python -m app.cli index --rebuild # rebuild dedup index from library
docker compose exec mdl-app /opt/engine-venv/bin/python -m app.cli import-legacy /app/data/legacy/download_archive.txt
```

### Config

All in `.env` (see `.env.example`). Plex token comes from
`Preferences.xml` (`PlexOnlineToken`). DB backups: `data/backups/` (nightly, keep 14).

### Reverse proxy notes

Point a single vhost at port 3000. Disable response buffering for
`/api/v1/events` (SSE) or live progress will stall.
