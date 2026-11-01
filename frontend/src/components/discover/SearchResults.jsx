import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Avatar, Box, Button, ButtonBase, Checkbox, Chip, CircularProgress,
  FormControlLabel, LinearProgress, Paper, Stack, Tooltip, Typography,
} from '@mui/material'
import AlbumIcon from '@mui/icons-material/Album'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import LibraryMusicOutlinedIcon from '@mui/icons-material/LibraryMusicOutlined'
import YouTubeIcon from '@mui/icons-material/YouTube'

import { api } from '../../api.js'
import ReleaseSection from '../artist/ReleaseSection.jsx'
import SelectionBar from '../artist/SelectionBar.jsx'
import { describeQueue, useReleaseSelection } from '../artist/selection.js'
import { categoryLabel, trackTime } from '../artist/shared.js'
import { findLinks, opensHere } from './links.js'

const albumUrl = (id) => `https://music.youtube.com/browse/${id}`

function Group({ title, count, action, children }) {
  return (
    <Box sx={{ mb: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 1 }}>
        <Typography variant="subtitle2" sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}>
          {title}
        </Typography>
        {count != null && (
          <Typography variant="caption" color="text.secondary">{count.toLocaleString()}</Typography>
        )}
        <Box sx={{ flexGrow: 1 }} />
        {action}
      </Box>
      {children}
    </Box>
  )
}

function Thumb({ src, round }) {
  return src ? (
    <Avatar src={src} alt="" variant={round ? 'circular' : 'rounded'}
      imgProps={{ referrerPolicy: 'no-referrer', loading: 'lazy' }}
      sx={{ width: 40, height: 40 }} />
  ) : (
    <Avatar variant={round ? 'circular' : 'rounded'} sx={{ width: 40, height: 40 }}>
      <AlbumIcon fontSize="small" />
    </Avatar>
  )
}

function LibraryMark({ song }) {
  if (song.library === 'have') {
    return (
      <Tooltip title={`In your library: ${song.library_path}`}>
        <CheckCircleIcon sx={{ fontSize: 16, color: 'success.main' }} />
      </Tooltip>
    )
  }
  if (song.library === 'elsewhere') {
    return (
      <Tooltip title={`In your library on another album: ${song.library_path}`}>
        <LibraryMusicOutlinedIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
      </Tooltip>
    )
  }
  return null
}

function ArtistCard({ artist, onOpen }) {
  const tag = artist.source === 'library' ? 'Yours'
    : artist.source === 'suggested' ? 'Recommended' : null
  return (
    <ButtonBase onClick={() => onOpen(artist)} sx={{ borderRadius: 2, textAlign: 'left' }}>
      <Paper variant="outlined" sx={{
        p: 1, pr: 1.5, display: 'flex', alignItems: 'center', gap: 1.25, width: 240,
        '&:hover': { borderColor: 'primary.main', bgcolor: 'action.hover' },
      }}>
        <Thumb src={artist.thumbnail} round />
        <Box sx={{ minWidth: 0, flexGrow: 1 }}>
          <Typography variant="body2" noWrap>{artist.name}</Typography>
          <Typography variant="caption" color="text.secondary" noWrap component="div">
            {[tag, artist.audience].filter(Boolean).join(' · ') || 'Artist'}
          </Typography>
        </Box>
      </Paper>
    </ButtonBase>
  )
}

/** A song: queue it (through its album where it is on one), or open the album. */
function SongRow({ song, busy, onQueue, onOpenAlbum }) {
  return (
    <Paper variant="outlined" sx={{ mb: 0.75, p: 0.75, pr: 1, display: 'flex', alignItems: 'center', gap: 1.25 }}>
      <Thumb src={song.thumbnail} />
      <Box sx={{ minWidth: 0, flexGrow: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>{song.title}</Typography>
          <LibraryMark song={song} />
        </Box>
        <Typography variant="caption" color="text.secondary" noWrap component="div">
          {[song.artist, song.album, song.year, trackTime(song.duration_sec)]
            .filter(Boolean).join(' · ')}
        </Typography>
      </Box>
      {song.album_id && (
        <Button size="small" onClick={() => onOpenAlbum(song)}>Open album</Button>
      )}
      <Button size="small" variant="outlined" disabled={busy || song.queued}
        onClick={() => onQueue(song)}>
        {song.queued ? 'Queued' : 'Queue'}
      </Button>
    </Paper>
  )
}

function AlbumRow({ album, busy, onQueue, onOpen }) {
  return (
    <Paper variant="outlined" sx={{ mb: 0.75, p: 0.75, pr: 1, display: 'flex', alignItems: 'center', gap: 1.25 }}>
      <Thumb src={album.thumbnail} />
      <Box sx={{ minWidth: 0, flexGrow: 1 }}>
        <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>{album.title}</Typography>
        <Typography variant="caption" color="text.secondary" noWrap component="div">
          {[album.artist, categoryLabel(album.category), album.year].filter(Boolean).join(' · ')}
        </Typography>
      </Box>
      {(album.in_library || album.downloaded) && (
        <Chip size="small" color="success" variant="outlined"
          label={album.in_library ? 'In library' : 'Downloaded'} />
      )}
      <Button size="small" onClick={() => onOpen(album)}>Open</Button>
      <Button size="small" variant="outlined" disabled={busy || album.queued}
        onClick={() => onQueue(album)}>
        {album.queued ? 'Queued' : 'Queue'}
      </Button>
    </Paper>
  )
}

/** Pasted links: artists and albums open here; the rest queue. */
function Links({ links, q, submit, onSubmitted, onNavigate, onQueued }) {
  const [force, setForce] = useState(false)
  const [state, setState] = useState({})     // url -> 'queued' | error text
  const [busy, setBusy] = useState(false)
  const queueable = links.filter((l) => !opensHere(l) || l.kind === 'album')

  const queue = useCallback(async (list) => {
    setBusy(true)
    for (const l of list) {
      try {
        await api.createJob(l.url, force)
        setState((s) => ({ ...s, [l.url]: 'queued' }))
      } catch (e) {
        setState((s) => ({ ...s, [l.url]: e.message }))
      }
    }
    setBusy(false)
    onQueued?.()
  }, [force, onQueued])

  // Enter in the search box, once: one artist or album opens; otherwise the
  // links not queued yet are.
  useEffect(() => {
    if (!submit || submit.q !== q) return
    onSubmitted()
    if (links.length === 1 && opensHere(links[0])) {
      onNavigate({ view: 'artist', ref: links[0].ref, release: links[0].release })
    } else {
      queue(queueable.filter((l) => !state[l.url] && l.kind !== 'artist'))
    }
  }, [submit]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Group title="Links" count={links.length} action={(
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
        <FormControlLabel sx={{ mr: 0 }}
          control={<Checkbox size="small" checked={force} onChange={(e) => setForce(e.target.checked)} />}
          label={<Typography variant="body2">Re-download if owned</Typography>} />
        <Button size="small" variant="contained" disabled={busy || !queueable.length}
          onClick={() => queue(queueable.filter((l) => state[l.url] !== 'queued'
            && l.kind !== 'artist'))}>
          Queue all
        </Button>
      </Stack>
    )}>
      {links.map((l) => (
        <Paper key={l.url} variant="outlined" sx={{ mb: 0.75, p: 1, display: 'flex', alignItems: 'center', gap: 1 }}>
          <Chip size="small" label={l.label} />
          <Box sx={{ minWidth: 0, flexGrow: 1 }}>
            <Typography variant="body2" noWrap title={l.url}>{l.url}</Typography>
            {state[l.url] && state[l.url] !== 'queued' && (
              <Typography variant="caption" color="error">{state[l.url]}</Typography>
            )}
          </Box>
          {opensHere(l) && (
            <Button size="small" onClick={() => onNavigate({ view: 'artist', ref: l.ref, release: l.release })}>
              Open
            </Button>
          )}
          {l.kind !== 'artist' && (
            <Button size="small" variant="outlined" disabled={busy || state[l.url] === 'queued'}
              onClick={() => queue([l])}>
              {state[l.url] === 'queued' ? 'Queued' : 'Queue'}
            </Button>
          )}
        </Paper>
      ))}
    </Group>
  )
}

/**
 * Search everything Discover knows — artists it follows, releases in its
 * catalogue, songs on tracklists already loaded — as you type, and YouTube
 * Music when asked (Enter, or the button): one request, cached for a week.
 */
export default function SearchResults({
  q, youtube, artists, recent, submit, onSubmitted, onNavigate, onSearchYoutube, onQueued,
  stats, bottomOffset,
}) {
  const links = useMemo(() => findLinks(q), [q])
  const [local, setLocal] = useState(null)
  const [localLoading, setLocalLoading] = useState(false)
  const [yt, setYt] = useState(null)
  const [ytLoading, setYtLoading] = useState(false)
  const [more, setMore] = useState({})           // kind -> loading
  const [expanded, setExpanded] = useState({})
  const [fetching, setFetching] = useState({})
  const [queued, setQueued] = useState({})        // video_id / browse_id -> true
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const text = q.trim()

  // Local results follow the typing, a beat behind it.
  useEffect(() => {
    if (links.length || text.length < 2) { setLocal(null); return undefined }
    let live = true
    setLocalLoading(true)
    const id = setTimeout(() => {
      api.discoverSearch(text)
        .then((d) => { if (live) { setLocal(d); setExpanded({}) } })
        .catch((e) => { if (live) setError(e.message) })
        .finally(() => { if (live) setLocalLoading(false) })
    }, 250)
    return () => { live = false; clearTimeout(id) }
  }, [text, links.length])

  // YouTube Music only when asked, and again only for a new query.
  useEffect(() => {
    setYt(null); setMore({})
    if (!youtube || links.length || text.length < 2) return undefined
    let live = true
    setYtLoading(true)
    api.discoverSearchYoutube(text)
      .then((d) => { if (live) setYt(d) })
      .catch((e) => { if (live) setError(e.message) })
      .finally(() => { if (live) setYtLoading(false) })
    return () => { live = false }
  }, [text, youtube, links.length])

  const loadMore = async (kind) => {
    setMore((m) => ({ ...m, [kind]: true }))
    try {
      const d = await api.discoverSearchYoutube(text, kind)
      setYt((prev) => ({ ...prev, [kind]: d[kind], [`${kind}_more`]: true }))
    } catch (e) {
      setError(e.message)
    } finally {
      setMore((m) => ({ ...m, [kind]: false }))
    }
  }

  // Artists Discover follows (and ones opened lately) whose name matches.
  const followed = useMemo(() => {
    if (links.length || text.length < 2) return []
    const needle = text.toLowerCase()
    const known = artists || []
    const ids = new Set(known.map((a) => a.channel_id))
    const all = [...known, ...recent.filter((r) => !ids.has(r.ref))
      .map((r) => ({ channel_id: r.ref, name: r.name, thumbnail: r.thumbnail }))]
    return all.filter((a) => (a.name || '').toLowerCase().includes(needle))
      .sort((a, b) => Number(!(a.name || '').toLowerCase().startsWith(needle))
        - Number(!(b.name || '').toLowerCase().startsWith(needle)))
      .slice(0, 12)
  }, [artists, recent, text, links.length])

  // ---------------------------------------------------------------- releases
  const releases = useMemo(() => local?.releases.items || [], [local])
  const [opened, setOpened] = useState({})        // browse_id -> fetched album
  const shown = useMemo(() => releases.map((r) => (opened[r.browse_id]
    ? { ...r, ...opened[r.browse_id] } : r)), [releases, opened])
  const titles = useMemo(() => Object.fromEntries(shown.map((r) => [r.browse_id, r.title])),
    [shown])
  const expandedRef = useRef(expanded)
  const fetchingRef = useRef(fetching)
  expandedRef.current = expanded
  fetchingRef.current = fetching

  const expand = useCallback(async (r) => {
    const opening = !expandedRef.current[r.browse_id]
    setExpanded((e) => ({ ...e, [r.browse_id]: opening }))
    if (!opening || r.tracks || fetchingRef.current[r.browse_id]) return
    setFetching((f) => ({ ...f, [r.browse_id]: true }))
    try {
      const album = await api.catalogAlbum(r.browse_id)
      setOpened((o) => ({ ...o, [r.browse_id]: {
        tracks: album.tracks, track_count: album.track_count, duration_sec: album.duration_sec,
        artists: album.artists, library: album.library,
      } }))
    } catch (e) {
      setError(`${r.title}: ${e.message}`)
      setExpanded((x) => ({ ...x, [r.browse_id]: false }))
    } finally {
      setFetching((f) => {
        const next = { ...f }
        delete next[r.browse_id]
        return next
      })
    }
  }, [])

  const {
    selection, setSelection, toggle, toggleTrack, selectMany, summary, picks, clearSettled,
  } = useReleaseSelection(shown, false)

  const report = (result, names) => {
    const { notice: text2, error: problem } = describeQueue(result, names)
    setNotice(text2)
    if (problem) setError(problem)
    onQueued?.()
  }

  const queueReleases = async () => {
    setBusy(true); setError(null); setNotice(null)
    try {
      const result = await api.catalogQueue(picks(shown), false)
      clearSettled(result)
      report(result, titles)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // ---------------------------------------------------------------- songs, albums
  const queueSong = async (s) => {
    setBusy(true); setError(null); setNotice(null)
    try {
      report(await api.discoverQueueSongs([{
        video_id: s.video_id, title: s.title, artist: s.artist, album: s.album,
        album_id: s.album_id,
      }]))
      setQueued((x) => ({ ...x, [s.video_id]: true }))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const queueAlbum = async (a) => {
    setBusy(true); setError(null); setNotice(null)
    try {
      report(await api.catalogQueue([{
        browse_id: a.browse_id, video_ids: null, title: a.title, artist: a.artist,
      }]), { [a.browse_id]: a.title })
      setQueued((x) => ({ ...x, [a.browse_id]: true }))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const openArtist = (a) => onNavigate({ view: 'artist', ref: a.channel_id })
  const openAlbum = (item) => onNavigate({
    view: 'artist',
    ref: item.artist_id || albumUrl(item.album_id || item.browse_id),
    release: item.album_id || item.browse_id,
  })

  if (links.length) {
    return (
      <Links links={links} q={q} submit={submit} onSubmitted={onSubmitted}
        onNavigate={onNavigate} onQueued={onQueued} />
    )
  }

  if (text.length < 2) {
    return (
      <Paper variant="outlined" sx={{ p: 3, textAlign: 'center', borderStyle: 'dashed' }}>
        <Typography variant="body2" color="text.secondary">
          Type at least two characters — an artist, an album, a song — or paste links.
        </Typography>
      </Paper>
    )
  }

  const tracks = local?.tracks
  const nothingLocal = local && !followed.length && !releases.length && !tracks?.items.length

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
      {notice && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setNotice(null)}>{notice}</Alert>}
      <Box sx={{ height: 4, mb: 1 }}>{(localLoading || ytLoading) && <LinearProgress />}</Box>

      {followed.length > 0 && (
        <Group title="Artists" count={followed.length}>
          <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 1 }}>
            {followed.map((a) => <ArtistCard key={a.channel_id} artist={a} onOpen={openArtist} />)}
          </Stack>
        </Group>
      )}

      {releases.length > 0 && (
        <Box sx={{ mb: 1 }}>
          <ReleaseSection section={{ key: 'releases', label: 'Releases in Discover', releases: shown }}
            selection={selection} expanded={expanded} fetching={fetching} titles={titles}
            query="" force={false} onToggle={toggle} onToggleTrack={toggleTrack}
            onExpand={expand} onSelectMany={selectMany}
            onOpenArtist={(channelId) => onNavigate({ view: 'artist', ref: channelId })} />
          {local.releases.total > releases.length && (
            <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: -1.5, mb: 2 }}>
              Showing {releases.length} of {local.releases.total.toLocaleString()} — add a word
              to narrow it down.
            </Typography>
          )}
        </Box>
      )}

      {tracks?.items.length > 0 && (
        <Group title="Songs on tracklists already loaded" count={tracks.total}>
          {tracks.items.map((s) => (
            <SongRow key={`${s.album_id}/${s.video_id}`} song={{ ...s, queued: queued[s.video_id] }}
              busy={busy} onQueue={queueSong} onOpenAlbum={openAlbum} />
          ))}
        </Group>
      )}

      {nothingLocal && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Nothing Discover knows matches “{text}”.
        </Typography>
      )}

      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: yt ? 2 : 0 }}>
          <YouTubeIcon color="error" />
          <Typography variant="subtitle1" sx={{ fontWeight: 600, flexGrow: 1 }}>
            On YouTube Music
          </Typography>
          {!youtube && (
            <Button variant="contained" size="small" onClick={onSearchYoutube}>
              Search YouTube Music for “{text}”
            </Button>
          )}
          {ytLoading && <CircularProgress size={18} />}
        </Box>
        {!youtube && (
          <Typography variant="caption" color="text.secondary">
            Press Enter in the search box, or the button. One request, remembered for a week.
          </Typography>
        )}
        {yt && (
          <>
            <Group title="Artists" count={yt.artists.length} action={!yt.artists_more && (
              <Button size="small" disabled={more.artists} onClick={() => loadMore('artists')}>
                More artists
              </Button>
            )}>
              {yt.artists.length ? (
                <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 1 }}>
                  {yt.artists.map((a) => <ArtistCard key={a.channel_id} artist={a} onOpen={openArtist} />)}
                </Stack>
              ) : <Typography variant="body2" color="text.secondary">None.</Typography>}
            </Group>
            <Group title="Songs" count={yt.songs.length} action={!yt.songs_more && (
              <Button size="small" disabled={more.songs} onClick={() => loadMore('songs')}>
                More songs
              </Button>
            )}>
              {yt.songs.length ? yt.songs.map((s) => (
                <SongRow key={s.video_id} song={{ ...s, queued: queued[s.video_id] }} busy={busy}
                  onQueue={queueSong} onOpenAlbum={openAlbum} />
              )) : <Typography variant="body2" color="text.secondary">None.</Typography>}
            </Group>
            <Group title="Albums & singles" count={yt.albums.length} action={!yt.albums_more && (
              <Button size="small" disabled={more.albums} onClick={() => loadMore('albums')}>
                More albums
              </Button>
            )}>
              {yt.albums.length ? yt.albums.map((a) => (
                <AlbumRow key={a.browse_id} album={{ ...a, queued: queued[a.browse_id] }}
                  busy={busy} onQueue={queueAlbum} onOpen={openAlbum} />
              )) : <Typography variant="body2" color="text.secondary">None.</Typography>}
            </Group>
          </>
        )}
      </Paper>

      <SelectionBar summary={summary} caps={stats?.caps} force={false} busy={busy}
        onClear={() => setSelection({})} onQueue={queueReleases} bottomOffset={bottomOffset} />
    </Box>
  )
}
