# Music Downloader

A self-hosted music downloader for a personal library. Find music in Discover —
search, browse an artist's whole discography, or paste a YouTube, YouTube Music
or Spotify link — and correctly tagged, best-quality audio lands in your
library as `Artist/Album/NN - Title`, ready for Plex or any other music server.

It runs as three Docker containers with a web UI, and is built to be gentle
with YouTube: one download at a time, paced, with backoff the moment YouTube
pushes back.

## Features

- **Discover** — one place to find music. Search what it already knows as you
  type (artists, releases, songs on loaded tracklists) and YouTube Music on
  Enter; open any artist's *complete* discography (not just the ten albums on
  their YouTube Music page), grouped into albums, EPs, singles, live albums,
  compilations and soundtracks, with tracklists and what is already in your
  library; follow new releases from your artists and artists like them,
  recommendations ("because you have…"), country charts and the top songs and
  albums of every genre. Pick whole releases or single tracks.
- **Paste anything** — into Discover's search box: YouTube or YouTube Music
  songs, albums, playlists and artists; Spotify tracks, albums, playlists and
  artists (Spotify supplies the metadata, YouTube Music the audio).
- **Proper files** — tags from YouTube Music or Spotify, cover art, synced
  lyrics (`.lrc`) from LRCLIB when it has them, stream-copied audio (never
  transcoded), validated length, imported atomically.
- **No duplicates, no gaps** — a track is skipped only when the same track is
  already in the library *on the same album*, so every release arrives whole.
- **Library** — one page for the whole library: scroll every artist, open an
  album, see its tracks, and fix what is wrong with it in place. Duplicates,
  split artists, bad tags and Plex mismatches are flagged where they are and
  queued in a sidebar of things to deal with.
- **Metadata manager** — identifies every album on MusicBrainz, writes clean
  tags (MusicBrainz ids, real multi-value genres, the primary artist as album
  artist), files it as `Artist/(Year) Album/NN - Title`, and keeps Plex
  matched and described the same way — genres, moods and cover art included.
  Report-only until you turn on auto-apply; every change can be undone.
- **Plex integration** (optional) — a partial library scan per imported album,
  a check that the exact file shows up, and the metadata manager above.

## Requirements

- Docker with Docker Compose v2.
- A folder for your music library — local disk or a network mount.
- Optional:
  - **Plex Media Server**, to rescan as albums arrive.
  - A **Spotify developer app** (free), for Spotify links.
  - A **YouTube Premium** account's cookies, for 256 kbps AAC and fewer bot
    checks. Without them downloads still work, at YouTube's free quality.

## Quick start

```bash
git clone https://github.com/AJZander/youtube-music-downloader.git
cd youtube-music-downloader
cp .env.example .env && chmod 600 .env
# Edit .env: set MUSIC_LIBRARY_PATH, and Plex / Spotify if you use them.
docker compose up -d --build
```

Open `http://<host>:3000`.

> **There is no login.** Anyone who can reach port 3000 can queue downloads and
> manage (and trash) files in your library. Keep it on a trusted network, or put
> it behind a reverse proxy that does authentication.

```bash
docker compose logs -f mdl-app    # worker logs
docker compose ps                 # health
```

## Configuration

Everything is set in `.env`; [`.env.example`](.env.example) documents every
option. The main ones:

| Setting | Default | What it does |
|---|---|---|
| `MUSIC_LIBRARY_PATH` | — (required) | Host folder of your music library |
| `PLEX_URL` / `PLEX_TOKEN` / `PLEX_SECTION_ID` | — | Plex server, token (`PlexOnlineToken` in Plex's `Preferences.xml`) and music library section |
| `PLEX_LIBRARY_ROOT` | `/data/Music` | The library folder as the Plex container sees it |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | — | Spotify app credentials |
| `SPOTIFY_REDIRECT_URI` | — | `http://<host>:3000/api/v1/spotify/callback`, to list your playlists |
| `TRACKS_PER_HOUR` / `TRACKS_PER_DAY` | 60 / 300 | Download caps (0 = unlimited) |
| `CATALOG_REQUESTS_PER_HOUR` | 600 | Budget for YouTube Music metadata (browsing) |
| `DISCOVER_ENABLED` | true | Background scan for new releases |
| `DISCOVER_REQUEST_INTERVAL` | 12 | Seconds between that scan's requests |
| `DISCOVER_COUNTRY` | `ZZ` | Starting chart country (`AU`, `US`, `GB`…; `ZZ` is global) |
| `LIBRARY_SCAN_WORKERS` | 32 | Processes reading tags during a library scan |
| `MUSICBRAINZ_CONTACT` | — | Your email or a URL; MusicBrainz requires it, and album identification is off without it |
| `LASTFM_API_KEY` | — | Genres and moods for albums MusicBrainz has none for |
| `METADATA_INTERVAL_MIN` | 30 | How often new and changed albums are checked |
| `METADATA_WRITE_FILES` / `METADATA_MOVE_FILES` / `METADATA_WRITE_PLEX` | true | Hard limits on what the metadata manager may change |

### YouTube cookies (optional)

1. Open a **private/incognito** window and log in to youtube.com.
2. In that same tab open `https://www.youtube.com/robots.txt`.
3. Export cookies **for youtube.com only** (e.g. the "Get cookies.txt LOCALLY"
   browser extension).
4. Save them as `data/cookies/youtube-cookies.txt`, then close the private
   window — that stops YouTube rotating the exported cookies.
5. `docker compose restart mdl-app`. The health check should show
   `youtube_authenticated: true`.

yt-dlp is only ever given a working *copy*; your exported file is never
modified. The cookies log in as you — treat the file like a password.

### Your data stays local

Everything the app keeps is under `data/` (database, cookies, Spotify token,
logs, nightly database backups) and `staging/` (downloads in progress). Both,
and `.env`, are git-ignored. Don't share them; they hold your tokens.

## Using it

### Downloading

Paste one or more links (one per line) into the search box in **Discover** and
press Enter, or pick releases and songs there. A job is created per link or
release and works through its tracks one at a time:
download → validate (length, format) → tag → import into the library. Anything
that fails validation lands in **Needs Review** with the file kept, to retry,
discard or import anyway. Tracks already in the library are skipped and listed
under **Skipped**, with what they matched; *Force download* fetches one anyway.

Pasting an artist or album link opens it in Discover instead, so you can choose.

### Discover

Discover is one page: a rail on the left and a pane for whatever is picked.
The rail has a search box, the views that are not an artist (**New releases**,
**Recommended artists**, **Charts & genres**), and every artist Discover
follows — yours and the recommended ones — plus anyone opened recently. Filter
it to yours, recommended or recent, and sort it by name, what just appeared,
what you don't have, the latest release found, or how much of them you own.
Where you are is in the address bar (`#/discover/artist/…`), so Back works and
an artist can be bookmarked.

**Search.** Typing filters the rail's artists and shows, in the pane, what
Discover already knows that matches: releases in its catalogue (any year, ones
you have included) and songs on the tracklists already loaded, each with what
is in your library. Nothing is asked of YouTube until you press Enter (or the
button): then one YouTube Music search adds artists, songs and albums, with
*More* for each. Searches are remembered for a week. A song queues through its
album where it is on one, so it arrives with album audio and tags.

### Browsing an artist

Open an artist from the rail, a search, a link, or another artist's *Fans also
like*, and the pane shows everything they have on YouTube Music.

An artist's YouTube Music page is a stack of carousels, each holding at most
ten releases; the full lists sit behind each carousel's "More" link, and the
browser follows them. (Paul Kelly's page shows 10 albums and 10 singles; there
are 41 and 21.)

Releases are grouped as albums, EPs, singles, live albums, compilations & box
sets, and soundtracks. YouTube Music files all of those as "Album", so the
grouping is read from titles (`Live at…`, `Greatest Hits`, `Original Motion
Picture Soundtrack`) and tracklists (mostly live tracks; 40+ tracks is a box
set). It is a guess, and can be wrong.

Each release shows what is **already in your library** — green when complete,
amber with a count when partly there — and **owned tracks are left out of the
download**. Owned means the same track *on that album*: a song you have on a
compilation or a single is shown as *on another album* and still downloads, so
the album arrives complete. Tick an owned track by hand to download it anyway;
*Re-download owned* does that for everything selected.

Matching a song is wider than an exact artist + title, because real libraries
are messy:

- **YouTube-style titles** — `Hilltop Hoods - Parade Of The Dead (Official
  Audio)` is `Parade Of The Dead`; rip track numbers (`04 - Bicycle Race`) go
  too.
- **Other credits** — `Paul Kelly & The Messengers` owns a song credited to
  `Paul Kelly`.
- **Remasters** are the same recording.

Two guards stop it claiming songs you don't have: **versions must agree**
(instrumental, live, acoustic, remix, demo… read from the track title and from
the album or folder name), and **lengths must agree** within 5 seconds (30 for
an old YouTube upload, which carries a video's intro or silence). **Album
names** are compared across folder conventions — `(2013) - Ariana Grande -
Yours Truly`, `Yours Truly [FLAC 24bit]`, `Yours Truly (Full Album)` are one
album. A remaster is the same album; a deluxe or anniversary edition is not.
The same rules decide ownership in the browser, Discover and the download
dedup check (`backend/app/ownership.py`), so what the browser promises is what
happens.

Overlapping releases are labelled — a single *also on* an album, an edition
whose tracks are all *also on* a bigger edition. **Select new** picks
everything you don't have, minus singles whose songs you already have or are
taking on an album, and editions a bigger selected edition contains. Tick any
of those yourself and it downloads whole.

Tracklists load in the background a few seconds apart; expanding a release
loads it immediately. Nothing is fetched twice for no reason: a discography is
shown from cache however old it is — Discover's scan keeps its artists' copies
fresh, and anyone else's is re-read quietly behind it once it is a week old
(*Refresh* re-reads it now) — and a tracklist is kept for good. Opening one of
your artists costs no requests at all.

Releases that just appeared are listed first. *Not interested* (✕) hides a
release from Discover — undo it from the message, or with *Show N not
interested* and the ↶ on the release. A recommended artist says who
recommended them, with *Not interested in this artist*.

### What Discover watches

**New releases.** A background scan matches each artist folder in your library
to its YouTube Music artist (by exact name; of several artists sharing a name,
the one with the biggest audience), then reads their whole discography — the
artist page and the full lists behind its "More" links — into the same cache
the artist view uses. It is slow on purpose — one request every 12 seconds,
about 300 an hour, biggest artists first; an artist with "More" lists takes
two or three — so a first pass over a library of a thousand-odd artists takes
the best part of a day, with results appearing as it goes. After that, artists
are re-checked every two days (ten or more of their tracks in the library) or
weekly. Releases met for the first time that the artist page itself doesn't
show are back catalogue, not new.

YouTube Music gives a release's year, not its date, so the list shows this year
and last (or up to every year), and — once an artist has been checked before —
*Just appeared* for anything new since. It is filtered and paged on the server,
so a page of singles is a full page of singles. Albums you already have — or
downloaded here — are hidden behind *Show N already downloaded*; clean and
explicit editions show once; *Not interested* hides a release for good.

**Artists you might like.** Every artist page lists related artists. Counted
across your library, weighted by how much of each artist you own, the ones you
don't have are the recommendations — shown with who recommended them. The top
40 are scanned for new releases too.

**Charts.** A country's top artists and music-video charts (about 70 countries
and a global chart), and every genre and mood's top 50 songs, top albums and
curated playlists. A chart lists a song by its music video, so queueing one
finds it on its album and downloads the album track instead — album audio,
tags and folder — falling back to the video only if it can't.

### The Library page

Everything about the library is on one page:

- **Artists** on the left — every artist folder, searchable and sortable,
  with a badge for anything that needs attention. Filter to artists with
  issues, duplicates, an unmatched Plex entry, or no MusicBrainz entry.
- **The artist** in the middle — genres and moods, how MusicBrainz and Plex
  know them, and their albums with cover art. Each album is flagged for what
  is wrong with it (duplicate, unmatched in Plex, tags to fix, a move pending).
  Click an album to see its tracks.
- **Needs attention** on the right — every open issue, grouped by kind, for
  the whole library or just this artist. Kinds that can be fixed automatically
  have an *Apply all*; *Download missing*, *Merge copies* and *Move to Trash*
  (never automatic) have one of their own. *Review* opens the kind's list:
  tick issues (Shift-click for a range, or *Select all … that match* for every
  page), narrow it with the search box and the kind's filters, and act on them
  all at once — apply, dismiss, or a call of the kind's own (see below).
  Below it, the activity log: every change made, each with *Undo*.
- **The detail pane** at the bottom (drag to resize, like the download pane)
  — for the selected album: a three-way comparison of what the files say,
  what MusicBrainz says and what Plex says; the tracks; match candidates (and
  "Add to MusicBrainz" for albums it doesn't have); its duplicates; where its
  files are and where they will go; and its history.

Run **Rescan** first; the first scan reads every file's tags and can take a
while on a network share (around an hour for 60,000 files over SMB); later
scans only re-read files that changed.

#### Duplicates

A duplicate is **the same track on the same album** — the same song on a
compilation, a single or another edition is that release's copy, never a
spare.

- **Duplicate albums** — folders whose names agree (by tag or folder, across
  naming conventions) *and* whose tracks agree. Each copy is scored —
  **lossless outranks any bitrate**, tags break ties, signs of truncation
  outrank everything — and the best is pre-selected to keep.
- **Duplicate tracks** — one album's track held twice. Lengths must agree —
  two mixes are not a duplicate.
- **Split artists** — one artist under several folder spellings (`AC_DC` /
  `ACDC`). Merging moves the albums *and* rewrites the `albumartist` tag, since
  Plex groups artists by tag, not folder.

Every group can be marked **Not duplicates**; that sticks across scans and is
listed (and undoable) under **Ignored**.

#### Metadata

**Full run** checks every album; the background check (every 30 minutes, and
a few minutes after each download) only looks at what is new or changed. For
each album it:

1. **Identifies the release** — from MusicBrainz ids already in the tags, the
   release Plex matched, ISRCs, or a MusicBrainz search — and scores it
   against the files (titles, versions, lengths, track count). 85% or better
   is acted on; 55–85% is offered for review; below that the album is
   reported as not identified (with a pre-filled *Add to MusicBrainz* form).
2. **Builds the desired record** — album artist is the primary artist (the
   full credit stays on each track: `Drake feat. Rihanna`), Various Artists
   for compilations, MusicBrainz genre names (from the release, then Last.fm,
   then the artist, then Plex), and moods from Plex's own vocabulary.
3. **Compares** it with the files and with Plex, and files an issue for each
   difference: tags to write, files to move, track or disc numbers that don't
   match the release (in the files or in Plex), Plex not having the album, an
   unmatched or wrongly matched Plex album, genres, moods, missing cover art.
4. **Checks the album is complete.** Tracks of the release that are not in the
   library — counted across every folder of the release, so a two-disc set in
   `CD1`/`CD2` folders is one album — are listed as *Missing tracks*.
   *Download missing* finds the album on YouTube Music and queues exactly those
   tracks; they arrive through the normal downloader, straight into the
   album's own folder (whatever YouTube Music calls the album), and the next
   check tags and numbers them. Downloads are never started automatically.
   Tracks YouTube Music doesn't have stay listed until you *Accept incomplete*.

The calls only you can make have bulk buttons in *Review*:

- **Check match** — *Accept match*, or *Not on MusicBrainz* (the files keep
  their own tags and are not asked about again; *Look up again* on the album's
  Match tab undoes it). Filters split them into *Files settle it* (every file
  matched a track and the artist is yours), *Someone else's song* (the best
  match is another artist's song of the same name — usually a single
  MusicBrainz doesn't have) and *Needs a look*; *Settle them* does the first
  two in one go.
- **Move blocked** — another copy of the album is already where it belongs.
  *Merge copies* makes them one album: tracks the destination lacks move in;
  where both have a track, the better file stays (lossless over lossy, then
  bitrate, a cut-off copy last) and the other goes to Trash. Only files
  identified as a track of the release are ever paired, so a bonus track or
  a demo with a clashing name stays where it is. A box set's albums stay in
  the box set, and another album with the same name (a single named like its
  album, a deluxe beside the standard) gets a folder of its own instead.
- **Album held more than once** — one album (a CD, a vinyl rip, a remaster, a
  deluxe edition) in several folders: copies of one MusicBrainz release group
  that share a recording. *Merge copies* makes it one album in the most
  complete edition's folder, keeping the best file of each recording; the
  rest go to Trash. A recording MusicBrainz knows in several versions (an
  original mono mix, a 2017 stereo mix, live) is never merged, since which
  version a file is can't be proven, and nor is a pair whose lengths
  disagree. Box sets keep their own copies.
- **Damaged file** — files nothing can read (a zeroed download). *Move to
  Trash* re-reads each one first; then *Download missing* fetches a good copy.

YouTube Music lookups have an hourly budget (`CATALOG_REQUESTS_PER_HOUR`). A
*Download missing* that finds it spent isn't failed: the download stays
wanted, and each background check asks again for a few albums, leaving the
rest of the budget for browsing.

*Not in Plex* that a scan doesn't fix is usually a track Plex keeps after
deleting its album (a scan finds the file unchanged and skips it). Applying it
again an hour on hides the file from Plex for one scan, empties Plex's trash
and shows it again, so Plex adds it afresh (its play history goes with the old
item) — at most once a week per folder.

The first full run is **report-only**. Review it, then turn on
**auto-apply**: confident fixes are then applied as they are found, and the
rest wait for you. Applying always goes files first (tags, then the move to
`Artist/(Year) Album [Edition]/NN - Title`), then a Plex scan, then Plex's
own fields — which are locked, so a Plex refresh can't undo them.

Plex reads genres only from its own agent with *Genres: Plex Music*, and never
reads moods from files, so those are set through Plex's API. Grouping is
another matter: Plex groups albums by tags, so artist and album fixes are
written to the files.

**Nothing is deleted, and everything can be undone.** Tag writes keep the
values they replace (junk included); moves are renames on the same disk; Plex
edits keep the old value and lock state. Removal of duplicates moves files
into `<library>/.mdl-trash`, restorable from **Trash**.

## How it works

| Service | Role |
|---|---|
| `mdl-app` | FastAPI API + download/import worker (yt-dlp, ytmusicapi, mutagen) |
| `mdl-web` | React UI served by nginx; proxies `/api/` to `mdl-app` (single origin) |
| `mdl-pot` | [bgutil](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) PO-token provider for yt-dlp |

- **Staging first** — downloads land in `./staging` as id-named files; only the
  importer writes to the library, building paths from *verified* tags. Missing
  tags mean quarantine, never "Unknown Album".
- **Quality** — format `141/774/251/140`: YouTube Premium 256k AAC when cookies
  allow, otherwise the best free stream, stream-copied into `.m4a` / `.opus`.
- **Spotify** links are metadata only: each track is matched to YouTube Music
  by ISRC, length (±3s) and title/artist, and nothing unverified is downloaded.
- **Safe imports** — temp file, fsync, checksum, rename; retries on I/O errors;
  a `.mdl-library-online` sentinel so a dropped network mount pauses imports
  instead of writing into an empty folder.
- **yt-dlp stays current** — upgraded (nightly channel) in a persistent volume
  at every start, and checked daily with a restart when idle.

### Rate limiting

YouTube does not publish limits, so the app stays well inside any reasonable
one and stops when told to:

- **Downloads** — one at a time, 5–20s apart with jitter, a long pause every 25
  tracks, hourly and daily caps.
- **Metadata** (browsing, Discover) — its own hourly budget and spacing, using
  one shared YouTube Music client.
- **Backoff** — a 429, 403 or bot check anywhere pauses everything, with
  exponential backoff and a 6-hour circuit breaker after repeated flags.
  YouTube limits the address, not the endpoint, so browsing and downloads back
  off together.

## Command line

Inside the container:

```bash
alias mdl='docker compose exec mdl-app /opt/engine-venv/bin/python -m app.cli'

mdl canary                       # extractor + cookie health check
mdl download <url>               # one URL end to end, in the foreground
mdl index --rebuild              # rebuild the dedup index from the library
mdl import-legacy <archive.txt>  # import a yt-dlp download archive (advisory)
mdl library scan [--full]        # incremental (or full) library scan
mdl library dupes albums         # also: tracks | artists
mdl library purge --days 30      # empty trash older than 30 days
```

## Development

```bash
# backend tests
docker compose exec -w /app mdl-app /opt/engine-venv/bin/python -m unittest discover tests

# frontend dev server (proxies /api to localhost:8000)
cd frontend && npm install && npm run dev
```

## Reverse proxy

Point one host at port 3000. Turn off response buffering for
`/api/v1/events` (server-sent events) or live progress will stall.

## Disclaimer

This is for building a personal library of music you have the right to
download. Downloading from YouTube may be against its Terms of Service, and
copyright law varies by country — how you use it is your responsibility.
