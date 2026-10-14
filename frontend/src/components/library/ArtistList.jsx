import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  Box, Chip, InputAdornment, LinearProgress, MenuItem, Select, Stack, TextField,
  Tooltip, Typography,
} from '@mui/material'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import HelpOutlineIcon from '@mui/icons-material/HelpOutline'
import LinkOffIcon from '@mui/icons-material/LinkOff'
import SearchIcon from '@mui/icons-material/Search'

import { api } from '../../api.js'

const ROW = 46
const OVERSCAN = 8

const FILTERS = [
  ['all', 'All'],
  ['issues', 'Issues'],
  ['dupes', 'Duplicates'],
  ['unmatched', 'Unmatched'],
  ['unknown', 'Not on MusicBrainz'],
]

function load(key, fallback) {
  try { return localStorage.getItem(key) || fallback } catch { return fallback }
}
function save(key, value) {
  try { localStorage.setItem(key, value) } catch { /* private mode */ }
}

function Row({ artist, selected, onSelect, top }) {
  return (
    <Box onClick={() => onSelect(artist.artist_dir)}
      sx={{
        position: 'absolute', top, left: 0, right: 0, height: ROW,
        px: 1.25, display: 'flex', alignItems: 'center', gap: 0.75, cursor: 'pointer',
        borderBottom: 1, borderColor: 'divider',
        bgcolor: selected ? 'action.selected' : 'transparent',
        borderLeft: 3,
        borderLeftColor: selected ? 'primary.main' : 'transparent',
        '&:hover': { bgcolor: selected ? 'action.selected' : 'action.hover' },
      }}>
      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        <Typography variant="body2" noWrap title={artist.artist_dir}
          sx={{ fontWeight: selected ? 600 : 400, lineHeight: 1.3 }}>
          {artist.name || artist.artist_dir}
        </Typography>
        <Typography variant="caption" color="text.secondary" noWrap display="block"
          sx={{ lineHeight: 1.2 }}>
          {artist.albums} album{artist.albums === 1 ? '' : 's'} · {artist.tracks} tracks
        </Typography>
      </Box>
      {artist.unknown && (
        <Tooltip title="Not on MusicBrainz">
          <HelpOutlineIcon sx={{ fontSize: 15, color: 'text.disabled' }} />
        </Tooltip>
      )}
      {artist.unmatched && (
        <Tooltip title="Unmatched in Plex">
          <LinkOffIcon sx={{ fontSize: 15, color: 'warning.main' }} />
        </Tooltip>
      )}
      {artist.dupes > 0 && (
        <Tooltip title={`${artist.dupes} duplicate group${artist.dupes === 1 ? '' : 's'}`}>
          <Box sx={{ display: 'flex', alignItems: 'center', color: 'warning.main' }}>
            <ContentCopyIcon sx={{ fontSize: 13 }} />
            <Typography variant="caption" sx={{ ml: 0.25, fontSize: '0.7rem' }}>
              {artist.dupes}
            </Typography>
          </Box>
        </Tooltip>
      )}
      {artist.issues > 0 && (
        <Tooltip title={`${artist.issues} open issue${artist.issues === 1 ? '' : 's'}`}>
          <Box sx={{
            minWidth: 20, height: 18, px: 0.6, borderRadius: 9, fontSize: '0.68rem',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            bgcolor: 'warning.main', color: 'warning.contrastText', fontWeight: 600,
          }}>
            {artist.issues > 999 ? '999+' : artist.issues}
          </Box>
        </Tooltip>
      )}
    </Box>
  )
}

/**
 * Every artist in the library — 1,300 and counting — in one windowed list:
 * only the rows in view are rendered, so it scrolls like a short one.
 */
export default function ArtistList({ selected, onSelect, version }) {
  const [q, setQ] = useState('')
  const [sort, setSort] = useState(() => load('mdl-lib-artist-sort', 'name'))
  const [filter, setFilter] = useState('all')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewport, setViewport] = useState(400)
  const scroller = useRef(null)
  const centred = useRef(null)

  useEffect(() => { save('mdl-lib-artist-sort', sort) }, [sort])

  useEffect(() => {
    let live = true
    setLoading(true)
    const id = setTimeout(() => {
      api.libraryArtists({ q, sort, filter, limit: 5000 })
        .then((d) => { if (live) setData(d) })
        .catch(() => { if (live) setData((prev) => prev || { total: 0, items: [] }) })
        .finally(() => { if (live) setLoading(false) })
    }, q ? 250 : 0)
    return () => { live = false; clearTimeout(id) }
  }, [q, sort, filter, version])

  useLayoutEffect(() => {
    const el = scroller.current
    if (!el) return undefined
    const ro = new ResizeObserver(() => setViewport(el.clientHeight))
    ro.observe(el)
    setViewport(el.clientHeight)
    return () => ro.disconnect()
  }, [])

  // Bring the selected artist into view once — on load from a link, or when
  // something outside the list (a sidebar item) selects it.
  useEffect(() => {
    const el = scroller.current
    if (!el || !data || !selected || centred.current === selected) return
    const index = data.items.findIndex((a) => a.artist_dir === selected)
    if (index < 0) return
    centred.current = selected
    const top = index * ROW
    if (top < el.scrollTop || top + ROW > el.scrollTop + el.clientHeight) {
      el.scrollTop = Math.max(0, top - el.clientHeight / 2)
    }
  }, [data, selected])

  const items = data?.items || []
  const first = Math.max(0, Math.floor(scrollTop / ROW) - OVERSCAN)
  const last = Math.min(items.length, Math.ceil((scrollTop + viewport) / ROW) + OVERSCAN)
  const select = (dir) => { centred.current = dir; onSelect(dir) }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Stack spacing={0.75} sx={{ p: 1, pb: 0.75 }}>
        <Stack direction="row" spacing={0.75}>
          <TextField size="small" fullWidth placeholder="Search artists" value={q}
            onChange={(e) => setQ(e.target.value)}
            InputProps={{ startAdornment: (
              <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>
            ) }} />
          <Select size="small" value={sort} onChange={(e) => setSort(e.target.value)}
            sx={{ minWidth: 92, fontSize: '0.8rem' }}>
            <MenuItem value="name">Name</MenuItem>
            <MenuItem value="issues">Issues</MenuItem>
            <MenuItem value="tracks">Tracks</MenuItem>
            <MenuItem value="albums">Albums</MenuItem>
            <MenuItem value="size">Size</MenuItem>
          </Select>
        </Stack>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
          {FILTERS.map(([key, label]) => (
            <Chip key={key} size="small" label={label} onClick={() => setFilter(key)}
              color={filter === key ? 'primary' : 'default'}
              variant={filter === key ? 'filled' : 'outlined'}
              sx={{ height: 22, fontSize: '0.7rem' }} />
          ))}
        </Box>
      </Stack>
      <Box sx={{ height: 2 }}>{loading && <LinearProgress sx={{ height: 2 }} />}</Box>
      <Box ref={scroller} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
        sx={{ flexGrow: 1, minHeight: 0, overflowY: 'auto', position: 'relative',
          borderTop: 1, borderColor: 'divider' }}>
        <Box sx={{ height: items.length * ROW, position: 'relative' }}>
          {items.slice(first, last).map((artist, i) => (
            <Row key={artist.artist_dir} artist={artist} top={(first + i) * ROW}
              selected={artist.artist_dir === selected} onSelect={select} />
          ))}
        </Box>
        {data && !items.length && (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2, textAlign: 'center' }}>
            No artists match.
          </Typography>
        )}
      </Box>
      <Typography variant="caption" color="text.secondary" sx={{ px: 1.25, py: 0.5 }}>
        {data ? `${data.total.toLocaleString()} artist${data.total === 1 ? '' : 's'}` : 'Loading…'}
      </Typography>
    </Box>
  )
}
