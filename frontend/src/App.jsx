import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AppBar, Box, Chip, Container, CssBaseline, IconButton, ListItemIcon, ListItemText, Menu,
  MenuItem, Tab, Tabs, ThemeProvider, Toolbar, Tooltip, Typography, createTheme,
} from '@mui/material'
import DarkModeIcon from '@mui/icons-material/DarkMode'
import ExploreIcon from '@mui/icons-material/Explore'
import HeadphonesIcon from '@mui/icons-material/Headphones'
import LibraryMusicIcon from '@mui/icons-material/LibraryMusic'
import LightModeIcon from '@mui/icons-material/LightMode'
import MenuIcon from '@mui/icons-material/Menu'
import MusicNoteIcon from '@mui/icons-material/MusicNote'
import QueueMusicIcon from '@mui/icons-material/QueueMusic'
import RateReviewIcon from '@mui/icons-material/RateReview'
import SkipNextIcon from '@mui/icons-material/SkipNext'

import { api, subscribeEvents } from './api.js'
import HealthBanner from './components/HealthBanner.jsx'
import DownloadDetails, { STATS_BAR_HEIGHT } from './components/DownloadDetails.jsx'
import JobList from './components/JobList.jsx'
import Library from './components/Library.jsx'
import NeedsReview from './components/NeedsReview.jsx'
import Queue from './components/Queue.jsx'
import SkippedList from './components/SkippedList.jsx'
import SpotifyBrowser from './components/SpotifyBrowser.jsx'
import Discover from './components/Discover.jsx'
import StatsBar from './components/StatsBar.jsx'

const DISCOVER_TAB = 4
const LIBRARY_TAB = 5
// Tabs that keep where they are in the address bar.
const HASH_TABS = [['#/discover', DISCOVER_TAB], ['#/library', LIBRARY_TAB]]
const tabForHash = (hash) => HASH_TABS.find(([prefix]) => (hash || '').startsWith(prefix))?.[1]

/** A count beside a section's name — what is waiting there. */
function NavCount({ n, warn }) {
  if (!n) return null
  return (
    <Box component="span" sx={{
      ml: 0.75, px: 0.75, minWidth: 20, lineHeight: '18px', borderRadius: 9,
      fontSize: '0.7rem', fontWeight: 600, textAlign: 'center',
      bgcolor: warn ? 'warning.main' : 'rgba(255, 255, 255, 0.22)',
      color: warn ? 'warning.contrastText' : 'inherit',
    }}>
      {n > 999 ? '999+' : n}
    </Box>
  )
}

/** The sections, in the app bar: tabs on a wide screen, a menu on a narrow
 *  one. What else the bar shows grows with the screen (the name from lg,
 *  icons from xl) so the tabs always fit without scroll arrows. The bar stays
 *  48px tall — Discover's sticky rail sits below it. */
function Nav({ sections, tab, onChange }) {
  const [anchor, setAnchor] = useState(null)
  const current = sections[tab]
  return (
    <>
      <IconButton color="inherit" edge="start" aria-label="Open navigation"
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={{ display: { xs: 'inline-flex', md: 'none' }, mr: 0.5 }}>
        <MenuIcon />
      </IconButton>
      <Menu anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)}>
        {sections.map((section, i) => (
          <MenuItem key={section.label} selected={i === tab}
            onClick={() => { onChange(i); setAnchor(null) }}>
            <ListItemIcon><section.Icon fontSize="small" /></ListItemIcon>
            <ListItemText>{section.label}</ListItemText>
            {section.count > 0 && (
              <Typography variant="body2" color={section.warn ? 'warning.main' : 'text.secondary'}
                sx={{ ml: 2, fontWeight: 600 }}>
                {section.count}
              </Typography>
            )}
          </MenuItem>
        ))}
      </Menu>
      <Typography variant="subtitle1" noWrap
        sx={{ display: { xs: 'block', md: 'none' }, flexGrow: 1, fontWeight: 600 }}>
        {current?.label}
      </Typography>

      <Box component="nav" aria-label="Sections"
        sx={{ display: { xs: 'none', md: 'flex' }, flexGrow: 1, minWidth: 0, ml: 2 }}>
        <Tabs value={tab} onChange={(_, v) => onChange(v)} textColor="inherit"
          variant="scrollable" scrollButtons={false}
          sx={{ flexGrow: 1, minHeight: 48,
            '& .MuiTabs-indicator': { bgcolor: 'currentColor', height: 3 } }}>
          {sections.map((section) => (
            <Tab key={section.label} iconPosition="start"
              icon={<section.Icon sx={{ fontSize: 18, display: { md: 'none', xl: 'block' } }} />}
              label={(
                <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center' }}>
                  {section.label}
                  <NavCount n={section.count} warn={section.warn} />
                </Box>
              )}
              sx={{ minHeight: 48, px: 1.75, textTransform: 'none', fontSize: '0.9rem',
                fontWeight: 500, '& .MuiTab-iconWrapper': { mr: 0.75 } }} />
          ))}
        </Tabs>
      </Box>
    </>
  )
}

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

  // A #/library or #/discover link (each keeps where it is in the hash)
  // opens straight onto its tab, and Back into one switches to it.
  const [tab, setTabState] = useState(() => tabForHash(window.location.hash) ?? 0)
  const setTab = useCallback((next) => {
    setTabState((prev) => {
      if (prev !== next && tabForHash(window.location.hash) === prev) {
        window.history.replaceState(null, '', window.location.pathname + window.location.search)
      }
      return next
    })
  }, [])
  useEffect(() => {
    const follow = () => {
      const next = tabForHash(window.location.hash)
      if (next !== undefined) setTabState(next)
    }
    window.addEventListener('popstate', follow)
    window.addEventListener('hashchange', follow)
    return () => {
      window.removeEventListener('popstate', follow)
      window.removeEventListener('hashchange', follow)
    }
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
  // In tab order: the index is the tab.
  const sections = [
    { label: 'Queue & History', Icon: QueueMusicIcon },
    { label: 'Needs Review', Icon: RateReviewIcon, count: reviewCount, warn: true },
    { label: 'Skipped', Icon: SkipNextIcon, count: skippedCount },
    { label: 'Spotify', Icon: HeadphonesIcon },
    { label: 'Discover', Icon: ExploreIcon },
    { label: 'Library', Icon: LibraryMusicIcon },
  ]

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <AppBar position="sticky" elevation={1}>
        <Toolbar variant="dense" sx={{ gap: 0.5 }}>
          <Tooltip title="Music Downloader">
            <MusicNoteIcon sx={{ display: { xs: 'none', md: 'block' } }} />
          </Tooltip>
          {/* The name gives way to the sections on a laptop-sized screen. */}
          <Typography variant="h6" noWrap
            sx={{ display: { xs: 'none', lg: 'block' }, ml: 0.5, flexShrink: 0 }}>
            Music Downloader
          </Typography>
          <Nav sections={sections} tab={tab} onChange={setTab} />
          {stats?.ytdlp_version && (
            <Chip size="small" label={`yt-dlp ${stats.ytdlp_version}`} variant="outlined"
              sx={{ display: { xs: 'none', lg: 'flex' }, ml: 1, color: 'inherit' }} />
          )}
          <Tooltip title={dark ? 'Light theme' : 'Dark theme'}>
            <IconButton color="inherit" onClick={toggleTheme}>
              {dark ? <LightModeIcon /> : <DarkModeIcon />}
            </IconButton>
          </Tooltip>
        </Toolbar>
      </AppBar>

      {/* The library workspace is three columns wide and sizes itself to the
          window, so it gets the full width and manages its own bottom pane.
          Discover is wider too: it has a rail of artists beside its pane. */}
      <Container maxWidth={onLibrary ? false : tab === DISCOVER_TAB ? 'xl' : 'lg'} sx={{
        py: 2,
        // Keep the last row reachable above the detail pane and the stats bar.
        pb: onLibrary ? `${STATS_BAR_HEIGHT + 8}px`
          : `${(selectedJobId ? detailsHeight : 0) + STATS_BAR_HEIGHT + 32}px`,
      }}>
        <HealthBanner health={health} stats={stats}
          onResume={() => api.governorResume().then(refreshMeta)} />
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
        <Box sx={{ display: tab === DISCOVER_TAB ? 'block' : 'none' }}>
          <Discover active={tab === DISCOVER_TAB} onQueued={refreshJobs} stats={stats}
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
