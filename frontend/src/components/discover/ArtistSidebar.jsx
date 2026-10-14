import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  Box, InputAdornment, LinearProgress, MenuItem, Select, Stack, TextField, Typography,
} from '@mui/material'
import SearchIcon from '@mui/icons-material/Search'

import { api } from '../../api.js'

const ROW = 44
const OVERSCAN = 8

const SORTS = {
  name: (a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base' }),
  releases: (a, b) => b.releases - a.releases || SORTS.name(a, b),
  latest: (a, b) => b.latest - a.latest || SORTS.name(a, b),
}

function load(key, fallback) {
  try { return localStorage.getItem(key) || fallback } catch { return fallback }
}
function save(key, value) {
  try { localStorage.setItem(key, value) } catch { /* private mode */ }
}

const plural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : many}`

function Row({ top, selected, title, detail, count, onClick }) {
  return (
    <Box onClick={onClick}
      sx={{
        position: 'absolute', top, left: 0, right: 0, height: ROW,
        px: 1.25, display: 'flex', alignItems: 'center', gap: 0.75, cursor: 'pointer',
        borderBottom: 1, borderColor: 'divider',
        bgcolor: selected ? 'action.selected' : 'transparent',
        borderLeft: 3, borderLeftColor: selected ? 'primary.main' : 'transparent',
        '&:hover': { bgcolor: selected ? 'action.selected' : 'action.hover' },
      }}>
      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        <Typography variant="body2" noWrap title={title}
          sx={{ fontWeight: selected ? 600 : 400, lineHeight: 1.3 }}>
          {title}
        </Typography>
        <Typography variant="caption" color="text.secondary" noWrap display="block"
          sx={{ lineHeight: 1.2 }}>
          {detail}
        </Typography>
      </Box>
      <Typography variant="caption" color="text.secondary">{count.toLocaleString()}</Typography>
    </Box>
  )
}

/**
 * The artists behind Discover's releases, under the same filters, each with
 * how many releases they have there. Picking one narrows the list to them.
 * A windowed list: only the rows in view are drawn.
 */
export default function ArtistSidebar({ params, version, selected, onSelect }) {
  const [artists, setArtists] = useState(null)
  const [loading, setLoading] = useState(false)
  const [q, setQ] = useState('')
  const [sort, setSort] = useState(() => load('mdl-discover-artist-sort', 'name'))
  const [scrollTop, setScrollTop] = useState(0)
  const [viewport, setViewport] = useState(400)
  const scroller = useRef(null)

  useEffect(() => { save('mdl-discover-artist-sort', sort) }, [sort])

  const key = JSON.stringify(params)
  useEffect(() => {
    let live = true
    setLoading(true)
    api.discoverReleaseArtists(JSON.parse(key))
      .then((d) => { if (live) setArtists(d.artists) })
      .catch(() => { if (live) setArtists((prev) => prev || []) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [key, version])

  useLayoutEffect(() => {
    const el = scroller.current
    if (!el) return undefined
    const ro = new ResizeObserver(() => setViewport(el.clientHeight))
    ro.observe(el)
    setViewport(el.clientHeight)
    return () => ro.disconnect()
  }, [])

  const all = artists || []
  const total = useMemo(() => all.reduce((n, a) => n + a.releases, 0), [all])
  const items = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return all.filter((a) => !needle || (a.name || '').toLowerCase().includes(needle))
      .sort(SORTS[sort] || SORTS.name)
  }, [all, q, sort])

  // Row 0 is "All artists"; the artists follow it.
  const rows = items.length + 1
  const first = Math.max(0, Math.floor(scrollTop / ROW) - OVERSCAN)
  const last = Math.min(rows, Math.ceil((scrollTop + viewport) / ROW) + OVERSCAN)
  const detail = (a) => [
    plural(a.releases, 'release', 'releases'),
    a.new > 0 && `${a.new} new`,
    a.source === 'suggested' && 'like yours',
  ].filter(Boolean).join(' · ')

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Stack direction="row" spacing={0.75} sx={{ p: 1, pb: 0.75 }}>
        <TextField size="small" fullWidth placeholder="Search" value={q}
          onChange={(e) => setQ(e.target.value)}
          InputProps={{ startAdornment: (
            <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>
          ) }} />
        <Select size="small" value={sort} onChange={(e) => setSort(e.target.value)}
          sx={{ minWidth: 112, fontSize: '0.8rem' }}>
          <MenuItem value="name">Name</MenuItem>
          <MenuItem value="releases">Releases</MenuItem>
          <MenuItem value="latest">Latest</MenuItem>
        </Select>
      </Stack>
      <Box sx={{ height: 2 }}>{loading && <LinearProgress sx={{ height: 2 }} />}</Box>
      <Box ref={scroller} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
        sx={{ flexGrow: 1, minHeight: 0, overflowY: 'auto', position: 'relative',
          borderTop: 1, borderColor: 'divider' }}>
        <Box sx={{ height: rows * ROW, position: 'relative' }}>
          {Array.from({ length: last - first }, (_, i) => first + i).map((index) => {
            if (index === 0) {
              return (
                <Row key="all" top={0} selected={!selected} title="All artists"
                  detail={plural(all.length, 'artist', 'artists')} count={total}
                  onClick={() => onSelect(null)} />
              )
            }
            const a = items[index - 1]
            return (
              <Row key={a.channel_id} top={index * ROW} selected={a.channel_id === selected}
                title={a.name} detail={detail(a)} count={a.releases}
                onClick={() => onSelect(a.channel_id === selected ? null : a)} />
            )
          })}
        </Box>
        {artists && !items.length && (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2, textAlign: 'center' }}>
            No artists match.
          </Typography>
        )}
      </Box>
      <Typography variant="caption" color="text.secondary" sx={{ px: 1.25, py: 0.5 }}>
        {artists
          ? q.trim() ? `${items.length.toLocaleString()} of ${plural(all.length, 'artist', 'artists')}`
            : plural(all.length, 'artist', 'artists')
          : 'Loading…'}
      </Typography>
    </Box>
  )
}
