import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Box, Button, Chip, LinearProgress, Link, Paper, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material'
import AlbumIcon from '@mui/icons-material/Album'
import CallMergeIcon from '@mui/icons-material/CallMerge'
import DriveFileMoveIcon from '@mui/icons-material/DriveFileMove'
import EditIcon from '@mui/icons-material/Edit'
import GridViewIcon from '@mui/icons-material/GridView'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import LinkIcon from '@mui/icons-material/Link'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import PersonIcon from '@mui/icons-material/Person'
import ViewListIcon from '@mui/icons-material/ViewList'

import { IssueRow } from './IssueList.jsx'
import { STATUS, severityColor } from './issues.js'
import { QualityChip, bytes, duration } from './shared.jsx'

const CARD = 168
const GAP = 12

/** Cover art from the backend, with a quiet placeholder when there is none. */
export function ArtImage({ src, size, round = false, icon = 'album' }) {
  const [failed, setFailed] = useState(false)
  const Icon = icon === 'artist' ? PersonIcon : AlbumIcon
  const box = {
    width: size || '100%', height: size || 'auto', aspectRatio: '1 / 1',
    borderRadius: round ? '50%' : 1, overflow: 'hidden', flexShrink: 0,
    bgcolor: 'action.hover', display: 'flex', alignItems: 'center', justifyContent: 'center',
  }
  if (!src || failed) {
    return (
      <Box sx={box}>
        <Icon sx={{ fontSize: size ? size * 0.45 : 48, color: 'text.disabled' }} />
      </Box>
    )
  }
  return (
    <Box sx={box}>
      <img src={src} alt="" loading="lazy" onError={() => setFailed(true)}
        style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
    </Box>
  )
}

function FlagChips({ flags, max = 2 }) {
  if (!flags?.length) return null
  const shown = flags.slice(0, max)
  return (
    <>
      {shown.map((f) => (
        <Chip key={f.kind} size="small" variant="outlined" color={severityColor(f.severity)}
          label={f.label} sx={{ height: 18, fontSize: '0.66rem' }} />
      ))}
      {flags.length > max && (
        <Tooltip title={flags.slice(max).map((f) => f.label).join(', ')}>
          <Chip size="small" label={`+${flags.length - max}`}
            sx={{ height: 18, fontSize: '0.66rem' }} />
        </Tooltip>
      )}
    </>
  )
}

function StatusChip({ status, confidence }) {
  const s = STATUS[status] || STATUS.pending
  return (
    <Tooltip title={confidence != null ? `MusicBrainz match confidence ${Math.round(confidence * 100)}%` : ''}>
      <Chip size="small" color={s.color} variant={status === 'resolved' ? 'outlined' : 'filled'}
        label={s.label} sx={{ height: 18, fontSize: '0.66rem' }} />
    </Tooltip>
  )
}

function MoveIcon({ album }) {
  if (!album.target_dirpath || album.target_dirpath === album.dirpath) return null
  return (
    <Tooltip title={`Will move to ${album.target_dirpath}`}>
      <DriveFileMoveIcon sx={{ fontSize: 16, color: 'info.main' }} />
    </Tooltip>
  )
}

function AlbumCard({ album, selected, onClick }) {
  return (
    <Paper variant="outlined" onClick={onClick} data-album-key={album.key} sx={{
      p: 1, cursor: 'pointer', position: 'relative', minWidth: 0,
      borderColor: selected ? 'primary.main' : 'divider',
      borderWidth: selected ? 2 : 1, m: selected ? 0 : '1px',
      '&:hover': { borderColor: 'primary.light' },
    }}>
      <Box sx={{ position: 'relative' }}>
        <ArtImage src={album.art_url} />
        {album.issue_count > 0 && (
          <Box sx={{
            position: 'absolute', top: 6, right: 6, minWidth: 20, height: 20, px: 0.6,
            borderRadius: 10, bgcolor: 'warning.main', color: 'warning.contrastText',
            fontSize: '0.7rem', fontWeight: 600,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            {album.issue_count}
          </Box>
        )}
      </Box>
      <Typography variant="body2" noWrap title={album.album} sx={{ mt: 0.75, fontWeight: 500 }}>
        {album.album}
      </Typography>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        <Typography variant="caption" color="text.secondary" noWrap sx={{ flexGrow: 1 }}>
          {[album.year, `${album.track_count} tracks`].filter(Boolean).join(' · ')}
        </Typography>
        <MoveIcon album={album} />
      </Box>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 0.5 }}>
        <StatusChip status={album.status} confidence={album.confidence} />
        <FlagChips flags={album.flags} max={1} />
      </Box>
    </Paper>
  )
}

function AlbumListRow({ album, selected, onClick }) {
  return (
    <Box onClick={onClick} data-album-key={album.key} sx={{
      display: 'flex', alignItems: 'center', gap: 1.25, px: 1, py: 0.75, cursor: 'pointer',
      borderBottom: 1, borderColor: 'divider',
      bgcolor: selected ? 'action.selected' : 'transparent',
      '&:hover': { bgcolor: selected ? 'action.selected' : 'action.hover' },
    }}>
      <ArtImage src={album.art_url} size={40} />
      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        <Typography variant="body2" noWrap title={album.album}>
          {album.album}
          {album.year && (
            <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 0.75 }}>
              {album.year}
            </Typography>
          )}
        </Typography>
        <Typography variant="caption" color="text.secondary" noWrap display="block"
          sx={{ fontFamily: 'monospace', fontSize: '0.7rem' }} title={album.dirpath}>
          {album.dirpath}
        </Typography>
      </Box>
      <MoveIcon album={album} />
      <Box sx={{ display: { xs: 'none', md: 'flex' }, gap: 0.5, flexWrap: 'wrap',
        justifyContent: 'flex-end', maxWidth: 260 }}>
        <FlagChips flags={album.flags} max={3} />
      </Box>
      <StatusChip status={album.status} confidence={album.confidence} />
      <Typography variant="caption" color="text.secondary" sx={{ width: 58, textAlign: 'right' }}>
        {album.track_count} tracks
      </Typography>
      {album.formats?.[0] && <QualityChip format={album.formats[0]} bitrate={album.avg_bitrate} />}
    </Box>
  )
}

/** The selected album's tracks, shown right under its card. */
function TrackPanel({ detail, album, onOpenPane }) {
  const loading = !detail || detail.key !== album.key
  return (
    <Paper variant="outlined" sx={{ gridColumn: '1 / -1', p: 1, bgcolor: 'action.hover' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 0.5, mb: 0.5 }}>
        <Typography variant="subtitle2" sx={{ flexGrow: 1 }} noWrap>
          {album.album}
          <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
            {album.album_artist} · {bytes(album.bytes)}
          </Typography>
        </Typography>
        <Button size="small" startIcon={<InfoOutlinedIcon />} onClick={onOpenPane}>
          Details
        </Button>
      </Box>
      {loading && <LinearProgress />}
      {!loading && (
        <Table size="small" sx={{ '& td, & th': { py: 0.35, px: 0.75 } }}>
          <TableHead>
            <TableRow>
              <TableCell sx={{ width: 40 }}>#</TableCell>
              <TableCell>Title</TableCell>
              <TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}>Artist</TableCell>
              <TableCell />
              <TableCell sx={{ width: 64 }} align="right">Length</TableCell>
              <TableCell sx={{ width: 90 }}>Quality</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {detail.tracks.map((t) => {
              const f = t.file || {}
              const number = f.disc_number > 1
                ? `${f.disc_number}-${f.track_number ?? '–'}` : (f.track_number ?? '–')
              return (
                <TableRow key={t.path} hover>
                  <TableCell sx={{ color: 'text.secondary' }}>{number}</TableCell>
                  <TableCell sx={{ maxWidth: 320 }}>
                    <Typography variant="body2" noWrap title={t.path}>{f.title || t.path}</Typography>
                    {t.desired?.title && t.desired.title !== f.title && (
                      <Typography variant="caption" color="info.main" noWrap display="block">
                        → {t.desired.title}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell sx={{ display: { xs: 'none', md: 'table-cell' }, maxWidth: 200 }}>
                    <Typography variant="caption" color="text.secondary" noWrap display="block">
                      {f.artist || '—'}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                      <FlagChips flags={t.flags} max={2} />
                      {!t.plex && (
                        <Tooltip title="Plex does not list this file">
                          <Chip size="small" variant="outlined" color="warning" label="not in Plex"
                            sx={{ height: 18, fontSize: '0.66rem' }} />
                        </Tooltip>
                      )}
                    </Box>
                  </TableCell>
                  <TableCell align="right">
                    <Typography variant="caption" color="text.secondary">
                      {duration(f.duration_sec)}
                    </Typography>
                  </TableCell>
                  <TableCell><QualityChip format={f.format} bitrate={f.bitrate_kbps} /></TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </Paper>
  )
}

function load(key, fallback) {
  try { return localStorage.getItem(key) || fallback } catch { return fallback }
}

/**
 * The middle column: one artist, their albums as covers or rows, and the
 * selected album's tracks opened in place.
 */
export default function ArtistView({
  artist, loading, error, selectedKey, albumDetail, onSelectAlbum, onOpenPane,
  onEdit, onMerge, onFixMatch, onChanged, notify, onNavigateIssue,
}) {
  const [view, setView] = useState(() => load('mdl-lib-album-view', 'grid'))
  const [onlyIssues, setOnlyIssues] = useState(false)
  const [columns, setColumns] = useState(4)
  const grid = useRef(null)

  useLayoutEffect(() => {
    const el = grid.current
    if (!el) return undefined
    const measure = () => setColumns(Math.max(1, Math.floor((el.clientWidth + GAP) / (CARD + GAP))))
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    measure()
    return () => ro.disconnect()
  }, [artist?.artist_dir, view])

  const albums = useMemo(() => (artist?.albums || [])
    .filter((a) => !onlyIssues || a.issue_count > 0), [artist, onlyIssues])

  // An album selected from elsewhere (a sidebar item, a link) is scrolled to.
  useEffect(() => {
    if (!selectedKey || !grid.current) return
    const el = grid.current.querySelector(`[data-album-key="${CSS.escape(selectedKey)}"]`)
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selectedKey, artist?.artist_dir])

  if (error) return <Alert severity="error" sx={{ m: 2 }}>{error}</Alert>
  if (!artist) {
    return loading ? <LinearProgress /> : (
      <Box sx={{ p: 4, textAlign: 'center' }}>
        <Typography color="text.secondary">
          Pick an artist on the left — or an item under Needs attention.
        </Typography>
      </Box>
    )
  }

  const genres = artist.desired?.genres?.length ? artist.desired.genres : (artist.plex?.genres || [])
  const moods = artist.desired?.moods?.length ? artist.desired.moods : (artist.plex?.moods || [])
  const split = (artist.issues || []).some((i) => i.kind === 'split_artist' && i.state === 'open')
  const mbStatus = artist.desired?.status
  const openArtistIssues = (artist.issues || []).filter((i) => i.state === 'open' || i.state === 'failed')
  const selectAlbum = (key) => onSelectAlbum(key === selectedKey ? null : key)

  // In the grid the track panel takes a full row, right after the row of the
  // selected card.
  const selectedIndex = albums.findIndex((a) => a.key === selectedKey)
  const panelAfter = selectedIndex < 0 ? -1
    : Math.min(albums.length - 1, Math.ceil((selectedIndex + 1) / columns) * columns - 1)
  const selectedAlbum = selectedIndex >= 0 ? albums[selectedIndex] : null

  return (
    <Box sx={{ p: 1.5 }}>
      {loading && <LinearProgress sx={{ mb: 1 }} />}
      <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-start', mb: 1.5, flexWrap: 'wrap' }}>
        <ArtImage src={artist.art_url} size={96} round icon="artist" />
        <Box sx={{ flexGrow: 1, minWidth: 240 }}>
          <Typography variant="h5" sx={{ lineHeight: 1.2 }}>{artist.name || artist.artist_dir}</Typography>
          <Typography variant="caption" color="text.secondary" display="block">
            {artist.albums_count ?? artist.albums?.length} albums · {artist.tracks} tracks
            · {bytes(artist.bytes)}
            {artist.name && artist.name !== artist.artist_dir && ` · folder "${artist.artist_dir}"`}
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 0.75 }}>
            {mbStatus === 'resolved' && (
              <Chip size="small" color="success" variant="outlined" icon={<LinkIcon />}
                label="MusicBrainz" component={artist.musicbrainz_url ? 'a' : 'div'}
                href={artist.musicbrainz_url || undefined} target="_blank" rel="noreferrer"
                clickable={!!artist.musicbrainz_url} />
            )}
            {mbStatus === 'unknown' && (
              <Chip size="small" variant="outlined" label="Not on MusicBrainz" />
            )}
            {(!mbStatus || mbStatus === 'pending') && (
              <Chip size="small" variant="outlined" label="Not checked yet" />
            )}
            {artist.plex
              ? (
                <Chip size="small" variant="outlined"
                  color={artist.plex.matched ? 'success' : 'warning'}
                  label={artist.plex.matched ? 'Plex matched' : 'Plex unmatched'} />
              )
              : <Chip size="small" variant="outlined" color="warning" label="Not in Plex" />}
            {genres.map((g) => <Chip key={`g-${g}`} size="small" label={g} color="primary" variant="outlined" />)}
            {moods.slice(0, 6).map((m) => <Chip key={`m-${m}`} size="small" label={m} />)}
          </Box>
        </Box>
        <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap', gap: 0.5 }}>
          <Button size="small" startIcon={<InfoOutlinedIcon />} onClick={onOpenPane}>Details</Button>
          <Button size="small" startIcon={<EditIcon />}
            onClick={() => onEdit({ entity: 'artist', refKey: artist.artist_dir, field: 'genres',
              label: 'Genres', value: genres })}>
            Genres
          </Button>
          <Button size="small" startIcon={<LinkIcon />} onClick={onFixMatch}>Fix match</Button>
          {split && (
            <Button size="small" color="warning" startIcon={<CallMergeIcon />} onClick={onMerge}>
              Merge
            </Button>
          )}
          {artist.musicbrainz_url && (
            <Button size="small" endIcon={<OpenInNewIcon />} component={Link}
              href={artist.musicbrainz_url} target="_blank" rel="noreferrer">
              MB
            </Button>
          )}
        </Stack>
      </Box>

      {openArtistIssues.length > 0 && (
        <Paper variant="outlined" sx={{ mb: 1.5 }}>
          {openArtistIssues.map((issue) => (
            <IssueRow key={issue.id} issue={issue} notify={notify}
              onNavigate={issue.kind === 'split_artist' ? () => onMerge() : onNavigateIssue}
              onChanged={onChanged} />
          ))}
        </Paper>
      )}

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>
          Albums
          <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 0.75 }}>
            {albums.length}{onlyIssues ? ` of ${artist.albums.length}` : ''}
          </Typography>
        </Typography>
        <Chip size="small" label="With issues" onClick={() => setOnlyIssues((v) => !v)}
          color={onlyIssues ? 'primary' : 'default'} variant={onlyIssues ? 'filled' : 'outlined'} />
        <ToggleButtonGroup size="small" exclusive value={view}
          onChange={(_, v) => { if (v) { setView(v); try { localStorage.setItem('mdl-lib-album-view', v) } catch { /* */ } } }}>
          <ToggleButton value="grid" sx={{ py: 0.25 }}><GridViewIcon fontSize="small" /></ToggleButton>
          <ToggleButton value="list" sx={{ py: 0.25 }}><ViewListIcon fontSize="small" /></ToggleButton>
        </ToggleButtonGroup>
      </Box>

      {!albums.length && (
        <Typography variant="body2" color="text.secondary" sx={{ p: 2, textAlign: 'center' }}>
          {onlyIssues ? 'No album has open issues.' : 'No albums indexed for this artist.'}
        </Typography>
      )}

      {view === 'grid' && (
        <Box ref={grid} sx={{
          display: 'grid', gap: `${GAP}px`,
          gridTemplateColumns: `repeat(auto-fill, minmax(${CARD}px, 1fr))`,
        }}>
          {albums.map((album, i) => (
            <Box key={album.key} sx={{ display: 'contents' }}>
              <AlbumCard album={album} selected={album.key === selectedKey}
                onClick={() => selectAlbum(album.key)} />
              {i === panelAfter && selectedAlbum && (
                <TrackPanel detail={albumDetail} album={selectedAlbum}
                  onOpenPane={() => onSelectAlbum(selectedAlbum.key, { pane: true })} />
              )}
            </Box>
          ))}
        </Box>
      )}

      {view === 'list' && (
        <Paper variant="outlined" ref={grid}>
          {albums.map((album) => (
            <Box key={album.key}>
              <AlbumListRow album={album} selected={album.key === selectedKey}
                onClick={() => selectAlbum(album.key)} />
              {album.key === selectedKey && (
                <Box sx={{ p: 1 }}>
                  <TrackPanel detail={albumDetail} album={album}
                    onOpenPane={() => onSelectAlbum(album.key, { pane: true })} />
                </Box>
              )}
            </Box>
          ))}
        </Paper>
      )}
    </Box>
  )
}
