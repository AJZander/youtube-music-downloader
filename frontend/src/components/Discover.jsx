import { useCallback, useEffect, useRef, useState } from 'react'
import { Box, Paper } from '@mui/material'

import { api, subscribeEvents } from '../api.js'
import { loadRecent, saveRecent } from './artist/shared.js'
import ArtistView from './discover/ArtistView.jsx'
import Charts from './discover/Charts.jsx'
import NewReleases from './discover/NewReleases.jsx'
import Rail from './discover/Rail.jsx'
import ScanStatus from './discover/ScanStatus.jsx'
import SearchResults from './discover/SearchResults.jsx'
import SuggestedArtists from './discover/SuggestedArtists.jsx'
import { findLinks } from './discover/links.js'
import { DISCOVER_PREFIX, buildRoute, parseRoute } from './discover/route.js'

// The app bar (a dense toolbar) stays at the top; the rail sticks below it.
const STICKY_TOP = 48 + 8

/**
 * Finding music: a rail to search from (or paste links into) and to pick an
 * artist from — yours, the recommended ones, anyone opened lately — and a
 * pane for whatever is picked: the new-releases feed, an artist's whole
 * discography, search results, recommendations or the charts.
 *
 * Where it is lives in the address bar (see route.js), so Back works and an
 * artist can be linked to.
 */
export default function Discover({ active, onQueued, stats, bottomOffset }) {
  const [route, setRoute] = useState(() => parseRoute() || { view: 'feed' })
  const [text, setText] = useState(() => (route.view === 'search' ? route.q : ''))
  const [status, setStatus] = useState(null)
  const [artists, setArtists] = useState(null)
  const [artistsLoading, setArtistsLoading] = useState(false)
  const [recent, setRecent] = useState(loadRecent)
  const [activeChannel, setActiveChannel] = useState(null)
  const [submit, setSubmit] = useState(null)        // Enter on links: {q, n}
  const [chartsSeen, setChartsSeen] = useState(route.view === 'charts')
  // Where to go back to when the search box is emptied.
  const beforeSearch = useRef({ view: 'feed' })
  const routeRef = useRef(route)
  routeRef.current = route

  // ---------------------------------------------------------------- routing
  const navigate = useCallback((next, { replace = false } = {}) => {
    setRoute(next)
    if (next.view === 'charts') setChartsSeen(true)
    const hash = buildRoute(next)
    if (window.location.hash !== hash) {
      if (replace) window.history.replaceState(null, '', hash)
      else window.history.pushState(null, '', hash)
    }
  }, [])

  useEffect(() => {
    const sync = () => {
      const parsed = parseRoute()
      if (!parsed) return
      setRoute(parsed)
      if (parsed.view === 'charts') setChartsSeen(true)
      setText(parsed.view === 'search' ? parsed.q : '')
    }
    window.addEventListener('popstate', sync)
    window.addEventListener('hashchange', sync)
    return () => {
      window.removeEventListener('popstate', sync)
      window.removeEventListener('hashchange', sync)
    }
  }, [])

  // Coming back to the tab puts where Discover is back in the address bar.
  useEffect(() => {
    if (active && !window.location.hash.startsWith(DISCOVER_PREFIX)) {
      window.history.replaceState(null, '', buildRoute(routeRef.current))
    }
  }, [active])

  const openArtist = useCallback((ref) => navigate({ view: 'artist', ref }), [navigate])

  // The rail's views clear the search; an artist keeps it, so the rail's
  // filtered list can be clicked through.
  const railNavigate = useCallback((next) => {
    if (next.view !== 'artist') setText('')
    navigate(next)
  }, [navigate])

  // ---------------------------------------------------------------- search box
  const onText = (value) => {
    setText(value)
    const q = value.trim()
    const current = routeRef.current
    if (!q) {
      if (current.view === 'search') navigate(beforeSearch.current, { replace: true })
      return
    }
    if (!findLinks(q).length && q.length < 2) return
    if (current.view === 'search') {
      navigate({ view: 'search', q }, { replace: true })
    } else {
      beforeSearch.current = current
      navigate({ view: 'search', q })
    }
  }

  const onSubmit = () => {
    const q = text.trim()
    if (!q) return
    const current = routeRef.current
    if (findLinks(q).length) {
      if (current.view !== 'search' || current.q !== q) {
        if (current.view !== 'search') beforeSearch.current = current
        navigate({ view: 'search', q }, { replace: current.view === 'search' })
      }
      setSubmit((s) => ({ q, n: (s?.n || 0) + 1 }))
      return
    }
    if (q.length < 2) return
    if (current.view !== 'search') beforeSearch.current = current
    navigate({ view: 'search', q, youtube: true }, { replace: current.view === 'search' })
  }

  // ---------------------------------------------------------------- data
  const loadArtists = useCallback(() => {
    setArtistsLoading(true)
    api.discoverKnownArtists()
      .then((d) => setArtists(d.artists))
      .catch(() => setArtists((prev) => prev || []))
      .finally(() => setArtistsLoading(false))
  }, [])
  useEffect(() => { loadArtists() }, [loadArtists])

  useEffect(() => {
    api.discoverStatus().then(setStatus).catch(() => {})
  }, [])
  useEffect(() => subscribeEvents({ 'discover.progress': setStatus }), [])
  const scan = () => api.discoverScan().then(setStatus).catch(() => {})

  // The scan finds releases as it goes: the rail's counts follow, gently.
  const checked = (status?.checked || 0) + (status?.suggested_checked || 0)
  const lastChecked = useRef(checked)
  useEffect(() => {
    if (checked - lastChecked.current >= 10) {
      lastChecked.current = checked
      loadArtists()
    }
  }, [checked, loadArtists])

  const remember = useCallback((artist) => {
    setActiveChannel(artist.channel_id)
    setRecent((prev) => {
      const next = [
        { ref: artist.channel_id, name: artist.name, thumbnail: artist.thumbnail },
        ...prev.filter((a) => a.ref !== artist.channel_id),
      ].slice(0, 10)
      saveRecent(next)
      return next
    })
  }, [])

  return (
    <Box sx={{
      display: 'grid', gap: 2, alignItems: 'start',
      gridTemplateColumns: { xs: 'minmax(0, 1fr)', md: '300px minmax(0, 1fr)' },
    }}>
      <Paper variant="outlined" sx={{
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
        position: { md: 'sticky' }, top: STICKY_TOP,
        height: { xs: 420, md: `calc(100vh - ${STICKY_TOP + bottomOffset + 16}px)` },
      }}>
        <Rail route={route} activeChannel={activeChannel} artists={artists} recent={recent}
          loading={artistsLoading} text={text} onText={onText} onSubmit={onSubmit}
          onNavigate={railNavigate} />
      </Paper>

      <Box sx={{ minWidth: 0 }}>
        {/* Kept mounted: a selection in the feed survives a look at an artist. */}
        <Box sx={{ display: route.view === 'feed' ? 'block' : 'none' }}>
          <ScanStatus status={status} onScan={scan} />
          <NewReleases status={status} onQueued={onQueued} onOpenArtist={openArtist}
            onChanged={loadArtists} stats={stats} bottomOffset={bottomOffset} />
        </Box>
        {route.view === 'artist' && (
          <ArtistView artistRef={route.ref} release={route.release} onOpenArtist={openArtist}
            onLoaded={remember} onArtistChanged={loadArtists} onQueued={onQueued}
            stats={stats} bottomOffset={bottomOffset} />
        )}
        {route.view === 'search' && (
          <SearchResults q={route.q} youtube={!!route.youtube} artists={artists} recent={recent}
            submit={submit} onSubmitted={() => setSubmit(null)} onNavigate={navigate}
            onSearchYoutube={() => navigate({ ...route, youtube: true }, { replace: true })}
            onQueued={onQueued} stats={stats} bottomOffset={bottomOffset} />
        )}
        {route.view === 'recommended' && (
          <SuggestedArtists status={status} onBrowseArtist={openArtist} onChanged={loadArtists} />
        )}
        {/* Loaded the first time it is shown, then kept, like the feed. */}
        {chartsSeen && (
          <Box sx={{ display: route.view === 'charts' ? 'block' : 'none' }}>
            <Charts onQueued={onQueued} onBrowseArtist={openArtist} />
          </Box>
        )}
      </Box>
    </Box>
  )
}
