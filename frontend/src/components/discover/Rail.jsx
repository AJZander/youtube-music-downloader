import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  Box, Button, Chip, IconButton, InputAdornment, LinearProgress, MenuItem, Select, Stack,
  TextField, Tooltip, Typography,
} from '@mui/material'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import ClearIcon from '@mui/icons-material/Clear'
import LeaderboardIcon from '@mui/icons-material/Leaderboard'
import NewReleasesIcon from '@mui/icons-material/NewReleases'
import SearchIcon from '@mui/icons-material/Search'

import { findLinks } from './links.js'

const ROW = 46
const OVERSCAN = 8

const FILTERS = [
  ['all', 'All'],
  ['library', 'Yours'],
  ['suggested', 'Recommended'],
  ['recent', 'Recent'],
]

const byName = (a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base' })
const SORTS = {
  name: byName,
  new: (a, b) => (b.new || 0) - (a.new || 0) || byName(a, b),
  missing: (a, b) => (b.missing || 0) - (a.missing || 0) || byName(a, b),
  latest: (a, b) => (b.latest || 0) - (a.latest || 0) || byName(a, b),
  tracks: (a, b) => (b.tracks || 0) - (a.tracks || 0) || byName(a, b),
}

function load(key, fallback) {
  try { return localStorage.getItem(key) || fallback } catch { return fallback }
}
function save(key, value) {
  try { localStorage.setItem(key, value) } catch { /* private mode */ }
}

const plural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : many}`

function detail(a) {
  if (a.recentOnly) return 'Opened recently'
  if (!a.checked) return a.source === 'library' ? 'Yours · not checked yet' : 'Recommended · not checked yet'
  return [
    a.source === 'library' ? `${plural(a.tracks, 'track', 'tracks')} yours` : 'Recommended',
    plural(a.releases, 'release', 'releases'),
  ].join(' · ')
}

function Shortcut({ icon, label, selected, onClick }) {
  return (
    <Box onClick={onClick} sx={{
      display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.75, cursor: 'pointer',
      borderLeft: 3, borderLeftColor: selected ? 'primary.main' : 'transparent',
      bgcolor: selected ? 'action.selected' : 'transparent',
      '&:hover': { bgcolor: selected ? 'action.selected' : 'action.hover' },
    }}>
      {icon}
      <Typography variant="body2" sx={{ fontWeight: selected ? 600 : 400 }}>{label}</Typography>
    </Box>
  )
}

function Row({ artist, top, selected, onOpen }) {
  return (
    <Box onClick={() => onOpen(artist)} sx={{
      position: 'absolute', top, left: 0, right: 0, height: ROW,
      px: 1.25, display: 'flex', alignItems: 'center', gap: 0.75, cursor: 'pointer',
      borderBottom: 1, borderColor: 'divider',
      bgcolor: selected ? 'action.selected' : 'transparent',
      borderLeft: 3, borderLeftColor: selected ? 'primary.main' : 'transparent',
      '&:hover': { bgcolor: selected ? 'action.selected' : 'action.hover' },
    }}>
      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        <Typography variant="body2" noWrap title={artist.name}
          sx={{ fontWeight: selected ? 600 : 400, lineHeight: 1.3 }}>
          {artist.name}
        </Typography>
        <Typography variant="caption" color="text.secondary" noWrap display="block"
          sx={{ lineHeight: 1.2 }}>
          {detail(artist)}
        </Typography>
      </Box>
      {artist.new > 0 ? (
        <Tooltip title={`${artist.new} just appeared that you don't have`}>
          <Chip size="small" color="secondary" label={`${artist.new} new`}
            sx={{ height: 20, fontSize: '0.68rem' }} />
        </Tooltip>
      ) : artist.missing > 0 && (
        <Tooltip title={`${artist.missing} release${artist.missing === 1 ? '' : 's'} you don't have`}>
          <Typography variant="caption" color="text.secondary">
            {artist.missing.toLocaleString()}
          </Typography>
        </Tooltip>
      )}
    </Box>
  )
}

/**
 * Discover's navigation: one box to search or paste links into, the views
 * that are not an artist, and every artist Discover follows — yours, the
 * recommended ones, and anyone opened recently. A windowed list: only the
 * rows in view are drawn.
 */
export default function Rail({
  route, activeChannel, artists, recent, loading, text, onText, onSubmit, onNavigate,
}) {
  const [filter, setFilter] = useState(() => load('mdl-discover-rail-filter', 'all'))
  const [sort, setSort] = useState(() => load('mdl-discover-rail-sort', 'name'))
  const [scrollTop, setScrollTop] = useState(0)
  const [viewport, setViewport] = useState(400)
  const scroller = useRef(null)
  const centred = useRef(null)

  useEffect(() => { save('mdl-discover-rail-filter', filter) }, [filter])
  useEffect(() => { save('mdl-discover-rail-sort', sort) }, [sort])

  useLayoutEffect(() => {
    const el = scroller.current
    if (!el) return undefined
    const ro = new ResizeObserver(() => setViewport(el.clientHeight))
    ro.observe(el)
    setViewport(el.clientHeight)
    return () => ro.disconnect()
  }, [])

  // Known artists, and the recently opened ones Discover does not follow.
  const everyone = useMemo(() => {
    const known = artists || []
    const ids = new Set(known.map((a) => a.channel_id))
    const extra = recent.filter((r) => !ids.has(r.ref))
      .map((r) => ({ channel_id: r.ref, name: r.name, recentOnly: true }))
    return [...known, ...extra]
  }, [artists, recent])

  const isLinks = findLinks(text).length > 0
  const items = useMemo(() => {
    const needle = isLinks ? '' : text.trim().toLowerCase()
    let list
    if (filter === 'recent') {
      const byId = Object.fromEntries(everyone.map((a) => [a.channel_id, a]))
      list = recent.map((r) => byId[r.ref] || { channel_id: r.ref, name: r.name, recentOnly: true })
    } else {
      list = everyone.filter((a) => filter === 'all' || a.source === filter)
        .sort(SORTS[sort] || byName)
    }
    return needle ? list.filter((a) => (a.name || '').toLowerCase().includes(needle)) : list
  }, [everyone, recent, filter, sort, text, isLinks])

  const selectedId = route.view === 'artist' ? (activeChannel || route.ref) : null

  // Bring the open artist into view once — opened from search, a link, or
  // an artist's "fans also like".
  useEffect(() => {
    const el = scroller.current
    if (!el || !selectedId || centred.current === selectedId) return
    const index = items.findIndex((a) => a.channel_id === selectedId)
    if (index < 0) return
    centred.current = selectedId
    const top = index * ROW
    if (top < el.scrollTop || top + ROW > el.scrollTop + el.clientHeight) {
      el.scrollTop = Math.max(0, top - el.clientHeight / 2)
    }
  }, [items, selectedId])

  const first = Math.max(0, Math.floor(scrollTop / ROW) - OVERSCAN)
  const last = Math.min(items.length, Math.ceil((scrollTop + viewport) / ROW) + OVERSCAN)
  const open = (a) => {
    centred.current = a.channel_id
    onNavigate({ view: 'artist', ref: a.channel_id })
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Box sx={{ p: 1, pb: 0.5 }}>
        <TextField size="small" fullWidth multiline maxRows={4} value={text}
          placeholder="Search, or paste links"
          onChange={(e) => onText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSubmit() }
            if (e.key === 'Escape') onText('')
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>
            ),
            endAdornment: text && (
              <InputAdornment position="end">
                <IconButton size="small" aria-label="Clear" onClick={() => onText('')}>
                  <ClearIcon fontSize="small" />
                </IconButton>
              </InputAdornment>
            ),
          }} />
        <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 0.5, px: 0.5 }}>
          {isLinks ? 'Enter queues the links (artists and albums open here)'
            : text.trim() ? 'Enter also searches YouTube Music' : 'Artists, releases, songs — or links'}
        </Typography>
      </Box>

      <Box sx={{ py: 0.5 }}>
        <Shortcut icon={<NewReleasesIcon fontSize="small" color="secondary" />} label="New releases"
          selected={route.view === 'feed'} onClick={() => onNavigate({ view: 'feed' })} />
        <Shortcut icon={<AutoAwesomeIcon fontSize="small" color="primary" />} label="Recommended artists"
          selected={route.view === 'recommended'} onClick={() => onNavigate({ view: 'recommended' })} />
        <Shortcut icon={<LeaderboardIcon fontSize="small" color="action" />} label="Charts & genres"
          selected={route.view === 'charts'} onClick={() => onNavigate({ view: 'charts' })} />
      </Box>

      <Stack spacing={0.75} sx={{ p: 1, pt: 0.75, borderTop: 1, borderColor: 'divider' }}>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
          {FILTERS.map(([key, label]) => (
            <Chip key={key} size="small" label={label} onClick={() => setFilter(key)}
              color={filter === key ? 'primary' : 'default'}
              variant={filter === key ? 'filled' : 'outlined'}
              sx={{ height: 22, fontSize: '0.7rem' }} />
          ))}
        </Box>
        {filter !== 'recent' && (
          <Select size="small" value={sort} onChange={(e) => setSort(e.target.value)}
            sx={{ fontSize: '0.8rem' }}>
            <MenuItem value="name">Name</MenuItem>
            <MenuItem value="new">Most just appeared</MenuItem>
            <MenuItem value="missing">Most you don't have</MenuItem>
            <MenuItem value="latest">Latest release found</MenuItem>
            <MenuItem value="tracks">Most in your library</MenuItem>
          </Select>
        )}
      </Stack>
      <Box sx={{ height: 2 }}>{loading && <LinearProgress sx={{ height: 2 }} />}</Box>
      <Box ref={scroller} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
        sx={{ flexGrow: 1, minHeight: 0, overflowY: 'auto', position: 'relative',
          borderTop: 1, borderColor: 'divider' }}>
        <Box sx={{ height: items.length * ROW, position: 'relative' }}>
          {items.slice(first, last).map((a, i) => (
            <Row key={a.channel_id} artist={a} top={(first + i) * ROW}
              selected={a.channel_id === selectedId} onOpen={open} />
          ))}
        </Box>
        {artists && !items.length && (
          <Box sx={{ p: 2, textAlign: 'center' }}>
            <Typography variant="body2" color="text.secondary">
              {text.trim() && !isLinks ? `No artists named like “${text.trim()}”.`
                : filter === 'recent' ? 'No artists opened yet.' : 'No artists match.'}
            </Typography>
            {text.trim() && !isLinks && (
              <Button size="small" onClick={() => onText('')}>Clear the search</Button>
            )}
          </Box>
        )}
      </Box>
      <Typography variant="caption" color="text.secondary" sx={{ px: 1.25, py: 0.5 }}>
        {artists ? plural(items.length, 'artist', 'artists') : 'Loading…'}
      </Typography>
    </Box>
  )
}
