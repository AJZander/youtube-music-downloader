import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert, Avatar, Box, Button, ButtonBase, Checkbox, Chip, CircularProgress, Grid,
  IconButton, LinearProgress, Link, MenuItem, Paper, Select, Stack, Tooltip, Typography,
} from '@mui/material'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import LibraryMusicOutlinedIcon from '@mui/icons-material/LibraryMusicOutlined'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import PlaylistAddIcon from '@mui/icons-material/PlaylistAdd'
import TrendingDownIcon from '@mui/icons-material/TrendingDown'
import TrendingUpIcon from '@mui/icons-material/TrendingUp'

import { api } from '../../api.js'
import { describeQueue } from '../artist/selection.js'

const COUNTRY_KEY = 'mdl-chart-country'
const GENRE_KEY = 'mdl-chart-genre'

function remembered(key) {
  try { return localStorage.getItem(key) || '' } catch { return '' }
}

function remember(key, value) {
  try { localStorage.setItem(key, value) } catch { /* per-browser convenience only */ }
}

const regionNames = (() => {
  try { return new Intl.DisplayNames(['en'], { type: 'region' }) } catch { return null }
})()

function countryName(code) {
  if (code === 'ZZ') return 'Global'
  try { return regionNames?.of(code) || code } catch { return code }
}

function Section({ title, action, children }) {
  return (
    <Paper sx={{ p: 2, mb: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, flexGrow: 1 }}>{title}</Typography>
        {action}
      </Box>
      {children}
    </Paper>
  )
}

function SongStatus({ song }) {
  if (song.library === 'have') {
    return (
      <Tooltip title={`In your library: ${song.library_path}`}>
        <CheckCircleIcon sx={{ fontSize: 16, color: 'success.main' }} />
      </Tooltip>
    )
  }
  if (song.library === 'elsewhere') {
    return (
      <Tooltip title={`In your library on another album: ${song.library_path}. `
        + 'This album\'s copy still downloads.'}>
        <LibraryMusicOutlinedIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
      </Tooltip>
    )
  }
  return null
}

const playlistUrl = (id) => `https://music.youtube.com/playlist?list=${id}`

/** The country charts, and every genre's top songs, albums and playlists. */
export default function Charts({ onQueued, onBrowseArtist }) {
  const [country, setCountry] = useState(() => remembered(COUNTRY_KEY))
  const [charts, setCharts] = useState(null)
  const [genres, setGenres] = useState(null)
  const [genre, setGenre] = useState(() => remembered(GENRE_KEY))
  const [page, setPage] = useState(null)
  const [loadingGenre, setLoadingGenre] = useState(false)
  const [songs, setSongs] = useState(new Set())
  const [albums, setAlbums] = useState(new Set())
  const [allArtists, setAllArtists] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  useEffect(() => {
    setCharts(null)
    api.discoverCharts(country).then(setCharts).catch((e) => setError(e.message))
  }, [country])

  useEffect(() => {
    api.discoverGenres().then((d) => {
      setGenres(d)
      if (!genre && d.genres.length) setGenre(d.genres[0].title)
    }).catch((e) => setError(e.message))
  }, [])  // the genre list changes weekly at most

  const loadGenre = useCallback(async (title) => {
    if (!title) return
    setLoadingGenre(true); setSongs(new Set()); setAlbums(new Set())
    try {
      setPage(await api.discoverGenre(title))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingGenre(false)
    }
  }, [])
  useEffect(() => { loadGenre(genre) }, [genre, loadGenre])

  const pickCountry = (code) => { setCountry(code); remember(COUNTRY_KEY, code) }
  const pickGenre = (title) => { setGenre(title); remember(GENRE_KEY, title) }

  const report = (result, titles) => {
    const { notice: text, error: problem } = describeQueue(result, titles)
    setNotice(text)
    if (problem) setError(problem)
    onQueued?.()
  }

  const queuePlaylist = async (p) => {
    setBusy(true); setError(null); setNotice(null)
    try {
      await api.createJob(playlistUrl(p.playlist_id))
      setNotice(`Queued the playlist "${p.title}".`)
      onQueued?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const queueSongs = async () => {
    const picked = page.songs.filter((s) => songs.has(s.video_id))
    setBusy(true); setError(null); setNotice(null)
    try {
      report(await api.discoverQueueSongs(picked.map((s) => ({
        video_id: s.video_id, title: s.title, artist: s.artist, album: s.album,
        album_id: s.album_id,
      }))))
      setSongs(new Set())
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const queueAlbums = async () => {
    const picked = page.albums.filter((a) => albums.has(a.browse_id))
    setBusy(true); setError(null); setNotice(null)
    try {
      report(await api.catalogQueue(picked.map((a) => ({
        browse_id: a.browse_id, video_ids: null, title: a.title, artist: a.artist,
      }))), Object.fromEntries(picked.map((a) => [a.browse_id, a.title])))
      setAlbums(new Set())
      loadGenre(genre)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const toggleIn = (setter, id) => setter((prev) => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  const newSongs = useMemo(
    () => (page?.songs || []).filter((s) => s.library !== 'have').map((s) => s.video_id), [page])

  const topArtists = charts ? (allArtists ? charts.artists : charts.artists.slice(0, 20)) : []

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
      {notice && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setNotice(null)}>{notice}</Alert>}

      <Section title="Top artists" action={charts && (
        <Select size="small" value={charts.country} onChange={(e) => pickCountry(e.target.value)}
          sx={{ minWidth: 160 }}>
          {charts.countries.map((code) => (
            <MenuItem key={code} value={code}>{countryName(code)}</MenuItem>
          ))}
        </Select>
      )}>
        {!charts && <LinearProgress />}
        {charts && (
          <>
            <Grid container spacing={1}>
              {topArtists.map((a) => (
                <Grid item xs={12} sm={6} md={3} key={a.channel_id}>
                  <ButtonBase onClick={() => onBrowseArtist(a.channel_id)}
                    sx={{ width: '100%', borderRadius: 1, justifyContent: 'flex-start' }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, p: 0.75, width: '100%',
                      borderRadius: 1, '&:hover': { bgcolor: 'action.hover' } }}>
                      <Typography variant="body2" color="text.secondary"
                        sx={{ width: 22, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                        {a.rank}
                      </Typography>
                      <Avatar src={a.thumbnail} alt="" sx={{ width: 36, height: 36 }}
                        imgProps={{ referrerPolicy: 'no-referrer', loading: 'lazy' }} />
                      <Box sx={{ minWidth: 0, flexGrow: 1, textAlign: 'left' }}>
                        <Typography variant="body2" noWrap>{a.name}</Typography>
                        {a.subscribers && (
                          <Typography variant="caption" color="text.secondary">
                            {a.subscribers} subscribers
                          </Typography>
                        )}
                      </Box>
                      {a.trend === 'up' && <TrendingUpIcon fontSize="small" color="success" />}
                      {a.trend === 'down' && <TrendingDownIcon fontSize="small" color="error" />}
                    </Box>
                  </ButtonBase>
                </Grid>
              ))}
            </Grid>
            {charts.artists.length > 20 && (
              <Button size="small" sx={{ mt: 1 }} onClick={() => setAllArtists((v) => !v)}>
                {allArtists ? 'Show top 20' : `Show all ${charts.artists.length}`}
              </Button>
            )}
            {charts.playlists.length > 0 && (
              <Stack direction="row" sx={{ mt: 1.5, flexWrap: 'wrap', gap: 1, alignItems: 'center' }}>
                <Tooltip title="These charts list music videos, so queueing one downloads the
                  videos' audio rather than album tracks.">
                  <Typography variant="caption" color="text.secondary">Video charts</Typography>
                </Tooltip>
                {charts.playlists.map((p) => (
                  <Chip key={p.playlist_id} variant="outlined" label={p.title}
                    onClick={() => window.open(playlistUrl(p.playlist_id), '_blank', 'noreferrer')}
                    onDelete={busy ? undefined : () => queuePlaylist(p)}
                    deleteIcon={<Tooltip title="Queue the playlist"><PlaylistAddIcon /></Tooltip>} />
                ))}
              </Stack>
            )}
          </>
        )}
      </Section>

      <Paper sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Genres</Typography>
        {!genres && <LinearProgress />}
        {genres && (
          <>
            <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.75 }}>
              {genres.genres.map((g) => (
                <Chip key={g.title} label={g.title} clickable
                  color={g.title === genre ? 'primary' : 'default'}
                  variant={g.title === genre ? 'filled' : 'outlined'}
                  onClick={() => pickGenre(g.title)} />
              ))}
            </Stack>
            {genres.moods.length > 0 && (
              <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.75, mt: 1, alignItems: 'center' }}>
                <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>Moods</Typography>
                {genres.moods.map((g) => (
                  <Chip key={g.title} size="small" label={g.title} clickable
                    color={g.title === genre ? 'primary' : 'default'}
                    variant={g.title === genre ? 'filled' : 'outlined'}
                    onClick={() => pickGenre(g.title)} />
                ))}
              </Stack>
            )}
          </>
        )}
      </Paper>

      {loadingGenre && <LinearProgress sx={{ mb: 2 }} />}

      {page && !loadingGenre && (
        <>
          {page.songs.length > 0 && (
            <Section title={`Top ${page.title} songs`} action={(
              <>
                <Button size="small" onClick={() => setSongs(new Set(newSongs))}>
                  Select new
                </Button>
                <Button size="small" disabled={!songs.size} onClick={() => setSongs(new Set())}>
                  Clear
                </Button>
                <Button size="small" variant="contained" disabled={!songs.size || busy}
                  startIcon={busy ? <CircularProgress size={14} color="inherit" /> : <PlaylistAddIcon />}
                  onClick={queueSongs}>
                  Queue {songs.size || ''} song{songs.size === 1 ? '' : 's'}
                </Button>
              </>
            )}>
              <Typography variant="caption" color="text.secondary" component="div" sx={{ mb: 1 }}>
                Each song is downloaded from its album, so it arrives with the album audio and tags.
              </Typography>
              {page.songs.map((s) => {
                const owned = s.library === 'have'
                return (
                  <Box key={s.video_id} sx={{ display: 'flex', alignItems: 'center', gap: 1,
                    py: 0.25, borderRadius: 1, '&:hover': { bgcolor: 'action.hover' },
                    opacity: owned ? 0.6 : 1 }}>
                    <Tooltip title={owned ? 'Already in your library' : ''}>
                      <span>
                        <Checkbox size="small" sx={{ p: 0.5 }} disabled={owned}
                          checked={songs.has(s.video_id)}
                          onChange={() => toggleIn(setSongs, s.video_id)}
                          inputProps={{ 'aria-label': `Select ${s.title}` }} />
                      </span>
                    </Tooltip>
                    <Typography variant="caption" color="text.secondary"
                      sx={{ width: 22, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                      {s.rank}
                    </Typography>
                    <Avatar variant="rounded" src={s.thumbnail} alt="" sx={{ width: 36, height: 36 }}
                      imgProps={{ referrerPolicy: 'no-referrer', loading: 'lazy' }} />
                    <Box sx={{ minWidth: 0, flexGrow: 1 }}>
                      <Typography variant="body2" noWrap>{s.title}</Typography>
                      <Typography variant="caption" color="text.secondary" noWrap component="div">
                        {s.artist_id ? (
                          <Link component="button" variant="caption" color="inherit"
                            underline="hover" onClick={() => onBrowseArtist(s.artist_id)}>
                            {s.artist}
                          </Link>
                        ) : s.artist}
                        {s.album && ` · ${s.album}`}
                      </Typography>
                    </Box>
                    <Box sx={{ width: 20, display: 'flex', justifyContent: 'center' }}>
                      <SongStatus song={s} />
                    </Box>
                    <Typography variant="caption" color="text.secondary"
                      sx={{ width: 90, textAlign: 'right', display: { xs: 'none', sm: 'block' } }}>
                      {s.views}
                    </Typography>
                  </Box>
                )
              })}
            </Section>
          )}

          {page.albums.length > 0 && (
            <Section title={`Top ${page.title} albums`} action={(
              <Button size="small" variant="contained" disabled={!albums.size || busy}
                startIcon={<PlaylistAddIcon />} onClick={queueAlbums}>
                Queue {albums.size || ''} album{albums.size === 1 ? '' : 's'}
              </Button>
            )}>
              <Grid container spacing={1.5}>
                {page.albums.map((a) => (
                  <Grid item xs={6} sm={4} md={2} key={a.browse_id}>
                    <Box sx={{ position: 'relative' }}>
                      <Box component="img" src={a.thumbnail} alt="" loading="lazy"
                        referrerPolicy="no-referrer"
                        sx={{ width: '100%', aspectRatio: '1', borderRadius: 1, objectFit: 'cover',
                          bgcolor: 'action.hover', display: 'block',
                          opacity: a.in_library ? 0.55 : 1 }} />
                      <Checkbox size="small" checked={albums.has(a.browse_id)}
                        disabled={a.in_library || a.queued}
                        onChange={() => toggleIn(setAlbums, a.browse_id)}
                        sx={{ position: 'absolute', top: 2, left: 2, bgcolor: 'background.paper',
                          borderRadius: 1, p: 0.25, '&:hover': { bgcolor: 'background.paper' } }}
                        inputProps={{ 'aria-label': `Select ${a.title}` }} />
                      {(a.in_library || a.queued) && (
                        <Chip size="small" color={a.in_library ? 'success' : 'info'}
                          label={a.in_library ? 'In library' : 'Queued'}
                          sx={{ position: 'absolute', bottom: 6, left: 6 }} />
                      )}
                    </Box>
                    <Typography variant="body2" noWrap sx={{ mt: 0.5 }} title={a.title}>
                      {a.title}
                    </Typography>
                    {a.artist_id ? (
                      <Link component="button" variant="caption" color="text.secondary"
                        underline="hover" onClick={() => onBrowseArtist(a.artist_id)}
                        sx={{ display: 'block', maxWidth: '100%', overflow: 'hidden',
                          textOverflow: 'ellipsis', whiteSpace: 'nowrap', textAlign: 'left' }}>
                        {a.artist}
                      </Link>
                    ) : (
                      <Typography variant="caption" color="text.secondary" noWrap component="div">
                        {a.artist}
                      </Typography>
                    )}
                  </Grid>
                ))}
              </Grid>
            </Section>
          )}

          {page.playlists.length > 0 && (
            <Section title={`${page.title} playlists`}>
              {page.playlists.map((p) => (
                <Box key={p.playlist_id} sx={{ display: 'flex', alignItems: 'center', gap: 1.25,
                  py: 0.5 }}>
                  <Avatar variant="rounded" src={p.thumbnail} alt="" sx={{ width: 40, height: 40 }}
                    imgProps={{ referrerPolicy: 'no-referrer', loading: 'lazy' }} />
                  <Box sx={{ minWidth: 0, flexGrow: 1 }}>
                    <Typography variant="body2" noWrap>{p.title}</Typography>
                    <Typography variant="caption" color="text.secondary" noWrap component="div">
                      {p.subtitle}
                    </Typography>
                  </Box>
                  <Tooltip title="Open in YouTube Music">
                    <IconButton size="small" component="a" href={playlistUrl(p.playlist_id)}
                      target="_blank" rel="noreferrer">
                      <OpenInNewIcon sx={{ fontSize: 16 }} />
                    </IconButton>
                  </Tooltip>
                  <Button size="small" disabled={busy} onClick={() => queuePlaylist(p)}>Queue</Button>
                </Box>
              ))}
            </Section>
          )}

          {!page.songs.length && !page.albums.length && !page.playlists.length && (
            <Typography color="text.secondary" sx={{ p: 2 }}>
              YouTube Music has nothing listed for {page.title} right now.
            </Typography>
          )}
        </>
      )}
    </Box>
  )
}
