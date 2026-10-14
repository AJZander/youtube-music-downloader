import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AppBar, Box, Chip, Container, CssBaseline, IconButton, Tab, Tabs,
  ThemeProvider, Toolbar, Typography, createTheme,
} from '@mui/material'
import DarkModeIcon from '@mui/icons-material/DarkMode'
import LightModeIcon from '@mui/icons-material/LightMode'
import MusicNoteIcon from '@mui/icons-material/MusicNote'

import { api, subscribeEvents } from './api.js'
import HealthBanner from './components/HealthBanner.jsx'
import DownloadDetails, { STATS_BAR_HEIGHT } from './components/DownloadDetails.jsx'
import JobList from './components/JobList.jsx'
import Library from './components/Library.jsx'
import NeedsReview from './components/NeedsReview.jsx'
import Queue from './components/Queue.jsx'
import SkippedList from './components/SkippedList.jsx'
import SpotifyBrowser from './components/SpotifyBrowser.jsx'
import ArtistBrowser from './components/ArtistBrowser.jsx'
import Discover from './components/Discover.jsx'
import StatsBar from './components/StatsBar.jsx'
import SubmitBar from './components/SubmitBar.jsx'

const DISCOVER_TAB = 5
const LIBRARY_TAB = 6

const TERMINAL_TRACK = new Set([
  'completed', 'skipped_duplicate', 'failed', 'cancelled', 'needs_review',
  'import_failed',
])

export default function App() {
  const [dark, setDark] = useState(
    () => localStorage.getItem('mdl-theme') !== 'light')
  const theme = useMemo(() => createTheme({
    palette: { mode: dark ? 'dark' : 'light', primary: { main: '#7c4dff' } },
  }), [dark])

  // A #/library link (the library workspace keeps its selection in the hash)
  // opens straight onto the Library tab.
  const [tab, setTabState] = useState(
    () => (window.location.hash.startsWith('#/library') ? LIBRARY_TAB : 0))
  const setTab = useCallback((next) => {
    setTabState((prev) => {
      if (prev === LIBRARY_TAB && next !== LIBRARY_TAB
          && window.location.hash.startsWith('#/library')) {
        window.history.replaceState(null, '', window.location.pathname + window.location.search)
      }
      return next
    })
  }, [])
  const [jobs, setJobs] = useState([])
  const [trackEvents, setTrackEvents] = useState({})   // track_id -> latest track
  // Transient transfer stats: speed/ETA/bytes, never persisted server-side.
  const [trackProgress, setTrackProgress] = useState({})
  const [health, setHealth] = useState(null)
  const [stats, setStats] = useState(null)
  const [reviewCount, setReviewCount] = useState(0)
  const [queueToken, setQueueToken] = useState(0)
  const [selectedJobId, setSelectedJobId] = useState(null)
  const [detailsHeight, setDetailsHeight] = useState(
    () => Number(localStorage.getItem('mdl-details-height')) || 320)

  const setHeight = useCallback((h) => {
    setDetailsHeight(h)
    localStorage.setItem('mdl-details-height', String(h))
  }, [])
  const [skippedCount, setSkippedCount] = useState(0)
  // An artist link pasted into the main bar, handed to the Artists tab.
  const [artistRequest, setArtistRequest] = useState(null)
  const browseArtist = useCallback((ref) => {
    setArtistRequest({ ref, at: Date.now() })
    setTab(4)
  }, [setTab])

  const refreshJobs = useCallback(() => {
    api.listJobs({ limit: 100 }).then(setJobs).catch(() => {})
  }, [])
  const refreshMeta = useCallback(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
    api.stats().then(setStats).catch(() => {})
    api.needsReview().then((t) => setReviewCount(t.length)).catch(() => {})
    api.skipped({ limit: 1 }).then((d) => setSkippedCount(d.total)).catch(() => {})
  }, [])

  useEffect(() => { refreshJobs(); refreshMeta() }, [refreshJobs, refreshMeta])
  useEffect(() => {
    const id = setInterval(refreshMeta, 30000)
    return () => clearInterval(id)
  }, [refreshMeta])

  useEffect(() => subscribeEvents({
    'job.updated': (job) => {
      setJobs((prev) => {
        const i = prev.findIndex((j) => j.id === job.id)
        if (i === -1) return [job, ...prev]
        const next = [...prev]; next[i] = { ...next[i], ...job }; return next
      })
      setQueueToken((n) => n + 1)
    },
    'track.updated': (track) => {
      setTrackEvents((prev) => ({ ...prev, [track.id]: track }))
      // Transfer stats mean nothing once a track stops moving, and a session
      // that runs for days would otherwise accumulate one entry per track.
      if (TERMINAL_TRACK.has(track.status)) {
        setTrackProgress((prev) => {
          if (!(track.id in prev)) return prev
          const next = { ...prev }
          delete next[track.id]
          return next
        })
      }
      if (track.status === 'needs_review') refreshMeta()
    },
    'track.progress': (p) => setTrackProgress((prev) => ({ ...prev, [p.track_id]: p })),
    'queue.stats': () => refreshMeta(),
    'queue.reordered': () => setQueueToken((n) => n + 1),
  }), [refreshMeta])

  const toggleTheme = () => {
    setDark((d) => {
      localStorage.setItem('mdl-theme', d ? 'light' : 'dark')
      return !d
    })
  }

  const onLibrary = tab === LIBRARY_TAB

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AppBar position="sticky" elevation={1}>
        <Toolbar variant="dense">
          <MusicNoteIcon sx={{ mr: 1 }} />
          <Typography variant="h6" sx={{ flexGrow: 1 }}>Music Downloader</Typography>
          {stats?.ytdlp_version && (
            <Chip size="small" label={`yt-dlp ${stats.ytdlp_version}`}
              sx={{ mr: 1, color: 'inherit' }} variant="outlined" />
          )}
          <IconButton color="inherit" onClick={toggleTheme}>
            {dark ? <LightModeIcon /> : <DarkModeIcon />}
          </IconButton>
        </Toolbar>
      </AppBar>

      {/* The library workspace is three columns wide and sizes itself to the
          window, so it gets the full width and manages its own bottom pane.
          Discover is wider too: new releases have an artist sidebar. */}
      <Container maxWidth={onLibrary ? false : tab === DISCOVER_TAB ? 'xl' : 'lg'} sx={{
        py: 2,
        // Keep the last row reachable above the detail pane and the stats bar.
        pb: onLibrary ? `${STATS_BAR_HEIGHT + 8}px`
          : `${(selectedJobId ? detailsHeight : 0) + STATS_BAR_HEIGHT + 32}px`,
      }}>
        <HealthBanner health={health} stats={stats}
          onResume={() => api.governorResume().then(refreshMeta)} />
        <SubmitBar onQueued={refreshJobs} onBrowseArtist={browseArtist} />
        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}
          variant="scrollable" allowScrollButtonsMobile>
          <Tab label="Queue & History" />
          <Tab label={reviewCount ? `Needs Review (${reviewCount})` : 'Needs Review'} />
          <Tab label={skippedCount ? `Skipped (${skippedCount})` : 'Skipped'} />
          <Tab label="Spotify" />
          <Tab label="Artists" />
          <Tab label="Discover" />
          <Tab label="Library" />
        </Tabs>
        {tab === 0 && (
          <>
            <Queue onChanged={refreshJobs} refreshToken={queueToken}
              trackProgress={trackProgress}
              selectedJobId={selectedJobId} onSelect={setSelectedJobId} />
            <JobList jobs={jobs} onChanged={refreshJobs}
              selectedJobId={selectedJobId} onSelect={setSelectedJobId} />
          </>
        )}
        {tab === 1 && <NeedsReview onChanged={refreshMeta} />}
        {tab === 2 && <SkippedList onChanged={refreshMeta} />}
        {tab === 3 && <SpotifyBrowser onQueued={refreshJobs} />}
        {/* Kept mounted: a half-made selection survives a look at the queue. */}
        <Box sx={{ display: tab === 4 ? 'block' : 'none' }}>
          <ArtistBrowser request={artistRequest} onQueued={refreshJobs} stats={stats}
            bottomOffset={(selectedJobId ? detailsHeight : 0) + STATS_BAR_HEIGHT} />
        </Box>
        <Box sx={{ display: tab === 5 ? 'block' : 'none' }}>
          <Discover onQueued={refreshJobs} onBrowseArtist={browseArtist} stats={stats}
            bottomOffset={(selectedJobId ? detailsHeight : 0) + STATS_BAR_HEIGHT} />
        </Box>
        {onLibrary && <Library />}
      </Container>

      {/* Hidden, not closed, on the Library tab: the library has its own pane. */}
      {selectedJobId && !onLibrary && (
        <DownloadDetails
          jobId={selectedJobId}
          trackEvents={trackEvents}
          trackProgress={trackProgress}
          height={detailsHeight}
          onHeight={setHeight}
          onClose={() => setSelectedJobId(null)}
          onChanged={refreshJobs} />
      )}
      <StatsBar stats={stats} />
    </ThemeProvider>
  )
}
