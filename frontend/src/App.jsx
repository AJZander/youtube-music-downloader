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
import JobList from './components/JobList.jsx'
import NeedsReview from './components/NeedsReview.jsx'
import SpotifyBrowser from './components/SpotifyBrowser.jsx'
import ArtistBrowser from './components/ArtistBrowser.jsx'
import StatsBar from './components/StatsBar.jsx'
import SubmitBar from './components/SubmitBar.jsx'

export default function App() {
  const [dark, setDark] = useState(
    () => localStorage.getItem('mdl-theme') !== 'light')
  const theme = useMemo(() => createTheme({
    palette: { mode: dark ? 'dark' : 'light', primary: { main: '#7c4dff' } },
  }), [dark])

  const [tab, setTab] = useState(0)
  const [jobs, setJobs] = useState([])
  const [trackEvents, setTrackEvents] = useState({})   // track_id -> latest track
  const [health, setHealth] = useState(null)
  const [stats, setStats] = useState(null)
  const [reviewCount, setReviewCount] = useState(0)

  const refreshJobs = useCallback(() => {
    api.listJobs({ limit: 100 }).then(setJobs).catch(() => {})
  }, [])
  const refreshMeta = useCallback(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
    api.stats().then(setStats).catch(() => {})
    api.needsReview().then((t) => setReviewCount(t.length)).catch(() => {})
  }, [])

  useEffect(() => { refreshJobs(); refreshMeta() }, [refreshJobs, refreshMeta])
  useEffect(() => {
    const id = setInterval(refreshMeta, 30000)
    return () => clearInterval(id)
  }, [refreshMeta])

  useEffect(() => subscribeEvents({
    'job.updated': (job) => setJobs((prev) => {
      const i = prev.findIndex((j) => j.id === job.id)
      if (i === -1) return [job, ...prev]
      const next = [...prev]; next[i] = { ...next[i], ...job }; return next
    }),
    'track.updated': (track) => {
      setTrackEvents((prev) => ({ ...prev, [track.id]: track }))
      if (track.status === 'needs_review') refreshMeta()
    },
    'queue.stats': () => refreshMeta(),
  }), [refreshMeta])

  const toggleTheme = () => {
    setDark((d) => {
      localStorage.setItem('mdl-theme', d ? 'light' : 'dark')
      return !d
    })
  }

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

      <Container maxWidth="lg" sx={{ py: 2, pb: 10 }}>
        <HealthBanner health={health} stats={stats}
          onResume={() => api.governorResume().then(refreshMeta)} />
        <SubmitBar onQueued={refreshJobs} />
        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}
          variant="scrollable" allowScrollButtonsMobile>
          <Tab label="Queue & History" />
          <Tab label={reviewCount ? `Needs Review (${reviewCount})` : 'Needs Review'} />
          <Tab label="Spotify" />
          <Tab label="Artists" />
        </Tabs>
        {tab === 0 && (
          <JobList jobs={jobs} trackEvents={trackEvents} onChanged={refreshJobs} />
        )}
        {tab === 1 && <NeedsReview onChanged={refreshMeta} />}
        {tab === 2 && <SpotifyBrowser onQueued={refreshJobs} />}
        {tab === 3 && <ArtistBrowser onQueued={refreshJobs} />}
      </Container>

      <StatsBar stats={stats} />
    </ThemeProvider>
  )
}
