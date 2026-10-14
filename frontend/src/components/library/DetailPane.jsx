import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Box, Button, Chip, CircularProgress, Divider, IconButton, LinearProgress, Link, Stack,
  Tab, Table,
  TableBody, TableCell, TableHead, TableRow, Tabs, TextField, Tooltip, Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import AddCircleOutlineIcon from '@mui/icons-material/AddCircleOutline'
import ArrowForwardIcon from '@mui/icons-material/ArrowForward'
import CheckIcon from '@mui/icons-material/Check'
import CloseIcon from '@mui/icons-material/Close'
import DownloadIcon from '@mui/icons-material/Download'
import EditIcon from '@mui/icons-material/Edit'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import PushPinIcon from '@mui/icons-material/PushPin'
import RefreshIcon from '@mui/icons-material/Refresh'
import SearchIcon from '@mui/icons-material/Search'
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff'

import { api } from '../../api.js'
import BottomPane from '../BottomPane.jsx'
import { AlbumDupeGroup } from './AlbumDupes.jsx'
import { ArtImage } from './ArtistView.jsx'
import { ChangeList, IssueRow } from './IssueList.jsx'
import { STATUS, asText, severityColor } from './issues.js'
import { TrackGroupCard } from './TrackDupes.jsx'
import { HealthChips, QualityChip, duration } from './shared.jsx'

export const ALBUM_TABS = ['compare', 'tracks', 'match', 'duplicates', 'files', 'history']
const ARTIST_TABS = ['overview', 'history']

const diffCell = (on, color = 'warning') => (on
  ? { bgcolor: (theme) => alpha(theme.palette[color].main, 0.14) } : {})

function Mono({ children, title }) {
  return (
    <Typography variant="caption" component="div" title={title || undefined}
      sx={{ fontFamily: 'monospace', fontSize: '0.72rem', wordBreak: 'break-all' }}>
      {children}
    </Typography>
  )
}

function Empty({ children }) {
  return (
    <Typography variant="body2" color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>
      {children}
    </Typography>
  )
}

/** Open issues first, resolved ones behind a toggle. */
function IssuesSection({ issues, onChanged, notify }) {
  const [showAll, setShowAll] = useState(false)
  const open = (issues || []).filter((i) => i.state === 'open' || i.state === 'failed' || i.state === 'queued')
  const rest = (issues || []).filter((i) => !open.includes(i))
  if (!issues?.length) return null
  return (
    <Box sx={{ mt: 1.5 }}>
      <Typography variant="overline" color="text.secondary" sx={{ px: 1 }}>
        Issues ({open.length} open)
      </Typography>
      {open.map((i) => <IssueRow key={i.id} issue={i} onChanged={onChanged} notify={notify} />)}
      {rest.length > 0 && (
        <Button size="small" onClick={() => setShowAll((v) => !v)} sx={{ ml: 0.5 }}>
          {showAll ? 'Hide' : 'Show'} {rest.length} resolved or dismissed
        </Button>
      )}
      {showAll && rest.map((i) => <IssueRow key={i.id} issue={i} onChanged={onChanged} notify={notify} />)}
    </Box>
  )
}

function CompareTab({ album, onEdit, onClear, onChanged, notify }) {
  const rows = album.compare || []
  return (
    <Box sx={{ p: 1 }}>
      {!rows.length && <Empty>Not checked yet — use Re-check on the Match tab.</Empty>}
      {rows.length > 0 && (
        <Table size="small" sx={{
          tableLayout: 'fixed',
          '& td, & th': { py: 0.4, px: 1, verticalAlign: 'top', wordBreak: 'break-word' },
        }}>
          <TableHead>
            <TableRow>
              <TableCell sx={{ width: 140 }}>Field</TableCell>
              <TableCell sx={{ width: '30%' }}>File tags</TableCell>
              <TableCell sx={{ width: '30%' }}>MusicBrainz (target)</TableCell>
              <TableCell>Plex</TableCell>
              <TableCell sx={{ width: 76 }} />
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.field} hover>
                <TableCell>
                  <Typography variant="body2" color="text.secondary">{r.label}</Typography>
                </TableCell>
                <TableCell sx={diffCell(r.differs_file)}>
                  <Typography variant="body2" sx={{ wordBreak: 'break-word' }}>{asText(r.file)}</Typography>
                </TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.5 }}>
                    <Typography variant="body2" sx={{ wordBreak: 'break-word', fontWeight: 500 }}>
                      {asText(r.desired)}
                    </Typography>
                    {r.overridden && (
                      <Tooltip title="Pinned by you — wins over MusicBrainz and Plex">
                        <PushPinIcon sx={{ fontSize: 14, color: 'primary.main', mt: 0.3 }} />
                      </Tooltip>
                    )}
                  </Box>
                </TableCell>
                <TableCell sx={diffCell(r.differs_plex, 'info')}>
                  <Typography variant="body2" sx={{ wordBreak: 'break-word' }}>{asText(r.plex)}</Typography>
                </TableCell>
                <TableCell align="right">
                  {r.editable && (
                    <Tooltip title={r.overridden ? 'Edit pinned value' : 'Pin your own value'}>
                      <IconButton size="small" onClick={() => onEdit(r)}>
                        <EditIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  )}
                  {r.overridden && (
                    <Tooltip title="Unpin">
                      <IconButton size="small" onClick={() => onClear(r)}>
                        <CloseIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', px: 1, mt: 0.5 }}>
        Amber: the file differs from the target. Blue: Plex differs from the target.
      </Typography>
      <IssuesSection issues={album.issues} onChanged={onChanged} notify={notify} />
    </Box>
  )
}

/** What the identified release has that the library doesn't. */
function MissingTracks({ album, onDetail, notify }) {
  const [busy, setBusy] = useState(false)
  const m = album.missing
  if (!m || !m.tracks?.length) return null
  const queued = m.download?.job_id && m.download.active !== false
  const download = async () => {
    setBusy(true)
    try {
      const r = await api.libraryDownloadMissing(album.key)
      notify?.(r.already_queued ? 'Already in the download queue'
        : `Queued ${r.queued} track${r.queued === 1 ? '' : 's'} for download`
          + (r.not_found?.length ? ` — ${r.not_found.length} not on YouTube Music` : ''),
      r.not_found?.length ? 'warning' : 'success')
      if (r.album?.key) onDetail?.(r.album)
    } catch (e) {
      notify?.(e.message, 'error')
    } finally { setBusy(false) }
  }
  // A few songs of a bigger album aren't an album with gaps: the rest is
  // shown, not flagged.
  return (
    <Alert severity={m.partial ? 'info' : 'warning'} sx={{ m: 1 }}
      action={(
        <Button size="small" color="inherit" disabled={busy} onClick={download}
          startIcon={busy ? <CircularProgress size={14} color="inherit" /> : <DownloadIcon fontSize="small" />}>
          {queued ? 'Queue again' : m.partial ? 'Download the rest' : 'Download missing'}
        </Button>
      )}>
      <Typography variant="body2" sx={{ fontWeight: 500 }}>
        {m.partial
          ? `${m.have} of the album's ${m.total} tracks are in the library`
          : `${m.tracks.length} of ${m.total} tracks missing`}
        {queued && ' — download queued'}
      </Typography>
      <Box sx={{ columns: m.tracks.length > 8 ? 2 : 1, mt: 0.25 }}>
        {m.tracks.map((t) => (
          <Typography key={t.releasetrack_mbid || `${t.disc}-${t.number}`} variant="caption"
            display="block" sx={{ breakInside: 'avoid' }}>
            <Box component="span" sx={{ display: 'inline-block', minWidth: 36, color: 'text.secondary' }}>
              {m.tracks.some((x) => x.disc > 1) || t.disc > 1 ? `${t.disc}-${t.number}` : t.number}
            </Box>
            {t.title}{t.length ? ` (${duration(t.length)})` : ''}
          </Typography>
        ))}
      </Box>
      {m.other_folders?.length > 0 && (
        <Typography variant="caption" color="text.secondary" display="block">
          Counted across {m.other_folders.length + 1} folders of this release.
        </Typography>
      )}
      {m.download?.not_found?.length > 0 && (
        <Typography variant="caption" display="block">
          Not on YouTube Music: {m.download.not_found.join(', ')}
        </Typography>
      )}
    </Alert>
  )
}

function TracksTab({ album, onDetail, notify }) {
  const tracks = album.tracks || []
  if (!tracks.length) return <Empty>No tracks.</Empty>
  return (
    <>
    <MissingTracks album={album} onDetail={onDetail} notify={notify} />
    <Table size="small" stickyHeader sx={{ '& td, & th': { py: 0.35, px: 1, verticalAlign: 'top' } }}>
      <TableHead>
        <TableRow>
          <TableCell sx={{ width: 64 }}>#</TableCell>
          <TableCell>Title — file / target / Plex</TableCell>
          <TableCell>Artist — file / target</TableCell>
          <TableCell sx={{ width: 60 }} align="right">Length</TableCell>
          <TableCell>Flags</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {tracks.map((t) => {
          const f = t.file || {}
          const d = t.desired
          const p = t.plex
          const num = (disc, n) => (n == null ? '–' : (disc > 1 ? `${disc}-${n}` : `${n}`))
          const fileNum = num(f.disc_number, f.track_number)
          const targetNum = d ? num(d.disc_number, d.track_number) : null
          const moving = d?.target_relpath && d.target_relpath !== t.path
          return (
            <TableRow key={t.path} hover>
              <TableCell>
                <Typography variant="body2">{fileNum}</Typography>
                {targetNum && targetNum !== fileNum && (
                  <Typography variant="caption" color="info.main">→ {targetNum}</Typography>
                )}
              </TableCell>
              <TableCell sx={{ maxWidth: 360 }}>
                <Typography variant="body2" sx={{ wordBreak: 'break-word' }}>{f.title || '—'}</Typography>
                {d?.title && d.title !== f.title && (
                  <Typography variant="caption" color="info.main" display="block">→ {d.title}</Typography>
                )}
                {p && p.title !== (d?.title || f.title) && (
                  <Typography variant="caption" color="text.secondary" display="block">
                    Plex: {p.title}{p.matched ? '' : ' (unmatched)'}
                  </Typography>
                )}
                {!p && (
                  <Typography variant="caption" color="warning.main" display="block">Not in Plex</Typography>
                )}
                {moving && (
                  <Mono title={d.target_relpath}>→ {d.target_relpath}</Mono>
                )}
              </TableCell>
              <TableCell sx={{ maxWidth: 240 }}>
                <Typography variant="body2" sx={{ wordBreak: 'break-word' }}>{f.artist || '—'}</Typography>
                {d?.artist && d.artist !== f.artist && (
                  <Typography variant="caption" color="info.main" display="block">→ {d.artist}</Typography>
                )}
              </TableCell>
              <TableCell align="right">
                <Typography variant="caption" color="text.secondary">{duration(f.duration_sec)}</Typography>
              </TableCell>
              <TableCell>
                <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                  {(t.flags || []).map((fl) => (
                    <Chip key={fl.kind} size="small" variant="outlined" color={severityColor(fl.severity)}
                      label={fl.label} sx={{ height: 18, fontSize: '0.66rem' }} />
                  ))}
                  {f.has_mbids === false && (
                    <Chip size="small" variant="outlined" label="no MBIDs"
                      sx={{ height: 18, fontSize: '0.66rem' }} />
                  )}
                </Box>
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
    </>
  )
}

function submitSeed(seed) {
  const form = document.createElement('form')
  form.method = 'post'
  form.action = seed.action
  form.target = '_blank'
  form.acceptCharset = 'UTF-8'
  for (const [name, value] of Object.entries(seed.fields || {})) {
    for (const v of (Array.isArray(value) ? value : [value])) {
      const input = document.createElement('input')
      input.type = 'hidden'
      input.name = name
      input.value = v == null ? '' : String(v)
      form.appendChild(input)
    }
  }
  document.body.appendChild(form)
  form.submit()
  form.remove()
}

function MatchTab({ album, onDetail, notify }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setResults(null); setQ('') }, [album.key])

  const run = async (fn, message) => {
    setBusy(true)
    try {
      const r = await fn()
      if (r && r.key) onDetail(r)
      if (message) notify?.(message)
      return r
    } catch (e) { notify?.(e.message, 'error') } finally { setBusy(false) }
    return null
  }
  const search = async () => {
    const r = await run(() => api.libraryAlbumCandidates(album.key, q.trim() || undefined))
    if (r) setResults(r.items || [])
  }
  const seed = async () => {
    const s = await run(() => api.libraryAlbumSeed(album.key))
    if (s?.action) submitSeed(s)
  }

  const candidates = results ?? album.candidates ?? []
  const ignored = album.status === 'ignored'
  return (
    <Box sx={{ p: 1 }}>
      <Stack direction="row" spacing={1} sx={{ mb: 1, alignItems: 'center', flexWrap: 'wrap', gap: 1 }}>
        <TextField size="small" placeholder="Search MusicBrainz — artist and album"
          value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') search() }}
          sx={{ minWidth: 280, flexGrow: 1, maxWidth: 520 }} />
        <Button size="small" startIcon={<SearchIcon />} disabled={busy} onClick={search}>Search</Button>
        {results && (
          <Button size="small" onClick={() => { setResults(null); setQ('') }}>Show suggestions</Button>
        )}
        <Box sx={{ flexGrow: 1 }} />
        <Button size="small" startIcon={<RefreshIcon />} disabled={busy}
          onClick={() => run(() => api.libraryAlbumRecheck(album.key), 'Re-checked')}>
          Re-check
        </Button>
        <Button size="small" startIcon={<VisibilityOffIcon />} disabled={busy}
          onClick={() => run(() => api.libraryAlbumIgnore(album.key, !ignored),
            ignored ? 'Album will be checked again' : 'Album ignored')}>
          {ignored ? 'Stop ignoring' : 'Ignore album'}
        </Button>
        {album.can_seed && (
          <Tooltip title="Opens MusicBrainz's add-release form, pre-filled from these files">
            <span>
              <Button size="small" startIcon={<AddCircleOutlineIcon />} disabled={busy} onClick={seed}>
                Add to MusicBrainz
              </Button>
            </span>
          </Tooltip>
        )}
        {album.musicbrainz_url && (
          <Button size="small" endIcon={<OpenInNewIcon />} component={Link}
            href={album.musicbrainz_url} target="_blank" rel="noreferrer">
            Open release
          </Button>
        )}
      </Stack>
      {busy && <LinearProgress sx={{ mb: 1 }} />}
      {album.status && (
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
          Status: {STATUS[album.status]?.label || album.status}
          {album.confidence != null && ` · confidence ${Math.round(album.confidence * 100)}%`}
          {album.release_mbid && ` · release ${album.release_mbid}`}
        </Typography>
      )}
      {!candidates.length && (
        <Empty>
          {results ? 'No releases found — try different words.'
            : 'No candidates yet. Search MusicBrainz above'
              + (album.can_seed ? ', or add this release to MusicBrainz.' : '.')}
        </Empty>
      )}
      {candidates.length > 0 && (
        <Table size="small" sx={{ '& td, & th': { py: 0.4, px: 1 } }}>
          <TableHead>
            <TableRow>
              <TableCell sx={{ width: 60 }}>Score</TableCell>
              <TableCell>Release</TableCell>
              <TableCell>Artist</TableCell>
              <TableCell>Date</TableCell>
              <TableCell>Country</TableCell>
              <TableCell>Tracks</TableCell>
              <TableCell>Format</TableCell>
              <TableCell sx={{ width: 150 }} />
            </TableRow>
          </TableHead>
          <TableBody>
            {candidates.map((c) => {
              const score = c.score != null ? Math.round(c.score * 100) : null
              const selected = c.selected || c.release_mbid === album.release_mbid
              return (
                <TableRow key={c.release_mbid} hover selected={selected}>
                  <TableCell>
                    {score != null && (
                      <Chip size="small" label={`${score}%`}
                        color={score >= 85 ? 'success' : score >= 60 ? 'warning' : 'default'}
                        variant="outlined" sx={{ height: 20 }} />
                    )}
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">{c.title}</Typography>
                    {c.disambiguation && (
                      <Typography variant="caption" color="text.secondary">{c.disambiguation}</Typography>
                    )}
                  </TableCell>
                  <TableCell><Typography variant="body2">{c.artist}</Typography></TableCell>
                  <TableCell><Typography variant="caption">{c.date || '—'}</Typography></TableCell>
                  <TableCell><Typography variant="caption">{c.country || '—'}</Typography></TableCell>
                  <TableCell>
                    <Typography variant="caption">
                      {c.matched_tracks != null ? `${c.matched_tracks}/` : ''}{c.track_count ?? '—'}
                    </Typography>
                  </TableCell>
                  <TableCell><Typography variant="caption">{c.format || '—'}</Typography></TableCell>
                  <TableCell align="right">
                    {selected
                      ? <Chip size="small" color="success" icon={<CheckIcon />} label="selected" />
                      : (
                        <Button size="small" variant="outlined" disabled={busy}
                          onClick={() => run(() => api.libraryAlbumMatch(album.key, c.release_mbid),
                            'Release chosen — the album was re-planned')}>
                          Use this
                        </Button>
                      )}
                    {c.url && (
                      <IconButton size="small" component="a" href={c.url} target="_blank" rel="noreferrer">
                        <OpenInNewIcon fontSize="small" />
                      </IconButton>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </Box>
  )
}

function DuplicatesTab({ album, onChanged }) {
  const groups = album.duplicates?.album_groups || []
  const tracks = album.duplicates?.track_groups || []
  if (!groups.length && !tracks.length) {
    return <Empty>No duplicates of this album or its tracks.</Empty>
  }
  return (
    <Box sx={{ p: 1 }}>
      {groups.length > 0 && (
        <Typography variant="overline" color="text.secondary">Album copies</Typography>
      )}
      {groups.map((g) => <AlbumDupeGroup key={g.id} group={g} onResolved={onChanged} />)}
      {tracks.length > 0 && (
        <Typography variant="overline" color="text.secondary">Duplicate tracks</Typography>
      )}
      {tracks.map((g) => <TrackGroupCard key={g.id} group={g} onChanged={onChanged} />)}
    </Box>
  )
}

function FilesTab({ album }) {
  const move = album.move
  return (
    <Box sx={{ p: 1.5 }}>
      <Box sx={{ display: 'grid', gridTemplateColumns: '140px 1fr', rowGap: 0.5, columnGap: 1, mb: 1.5 }}>
        <Typography variant="caption" color="text.secondary">Folder</Typography>
        <Mono>{album.dirpath}</Mono>
        <Typography variant="caption" color="text.secondary">Artist folder</Typography>
        <Mono>{album.artist_dir}</Mono>
        {album.key !== album.dirpath && (
          <>
            <Typography variant="caption" color="text.secondary">Album unit</Typography>
            <Mono>{album.key}</Mono>
          </>
        )}
        <Typography variant="caption" color="text.secondary">Formats</Typography>
        <Box>{(album.formats || []).map((f) => <QualityChip key={f} format={f} bitrate={album.avg_bitrate} />)}</Box>
        {album.health?.length > 0 && (
          <>
            <Typography variant="caption" color="text.secondary">Tag health</Typography>
            <Box><HealthChips issues={album.health} /></Box>
          </>
        )}
      </Box>

      {move && (
        <Box sx={{ mb: 1.5 }}>
          <Typography variant="overline" color="text.secondary">Planned move</Typography>
          {move.conflict && <Alert severity="warning" sx={{ mb: 1 }}>{move.conflict}</Alert>}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
            <Mono>{move.from}</Mono>
            <ArrowForwardIcon fontSize="small" color="info" />
            <Mono>{move.to}</Mono>
          </Box>
          <Table size="small" sx={{ '& td': { py: 0.3, px: 1 } }}>
            <TableBody>
              {(move.files || []).map((f) => (
                <TableRow key={f.from}>
                  <TableCell sx={{ width: '50%' }}><Mono>{f.from}</Mono></TableCell>
                  <TableCell sx={{ width: 24, px: 0 }}><ArrowForwardIcon sx={{ fontSize: 14 }} color="info" /></TableCell>
                  <TableCell><Mono>{f.to}</Mono></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}
      {!move && (
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
          No move planned — the files are already where the layout wants them, or the album
          is not identified yet.
        </Typography>
      )}

      <Typography variant="overline" color="text.secondary">Files</Typography>
      <Table size="small" sx={{ '& td': { py: 0.3, px: 1 } }}>
        <TableBody>
          {(album.tracks || []).map((t) => (
            <TableRow key={t.path} hover>
              <TableCell><Mono>{t.path}</Mono></TableCell>
              <TableCell sx={{ width: 100 }}><QualityChip format={t.file?.format} bitrate={t.file?.bitrate_kbps} /></TableCell>
              <TableCell sx={{ width: 60 }} align="right">
                <Typography variant="caption" color="text.secondary">{duration(t.file?.duration_sec)}</Typography>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  )
}

function ArtistOverviewTab({ artist, onEdit, onChanged, notify }) {
  const d = artist.desired || {}
  const p = artist.plex || {}
  const rows = [
    { field: 'name', label: 'Name', desired: d.name, plex: p.title },
    { field: 'genres', label: 'Genres', desired: d.genres, plex: p.genres, editable: true },
    { field: 'moods', label: 'Moods', desired: d.moods, plex: p.moods, editable: true },
    { field: 'styles', label: 'Styles', desired: null, plex: p.styles },
    { field: 'countries', label: 'Country', desired: null, plex: p.countries },
    { field: 'mbid', label: 'MusicBrainz ID', desired: d.mbid, plex: p.mbid },
  ]
  return (
    <Box sx={{ p: 1 }}>
      <Table size="small" sx={{ '& td, & th': { py: 0.4, px: 1, verticalAlign: 'top' } }}>
        <TableHead>
          <TableRow>
            <TableCell sx={{ width: 140 }}>Field</TableCell>
            <TableCell>Target</TableCell>
            <TableCell>Plex</TableCell>
            <TableCell sx={{ width: 50 }} />
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => {
            const differs = r.desired != null && asText(r.desired) !== asText(r.plex)
            return (
              <TableRow key={r.field} hover>
                <TableCell><Typography variant="body2" color="text.secondary">{r.label}</Typography></TableCell>
                <TableCell><Typography variant="body2" sx={{ fontWeight: 500 }}>{asText(r.desired)}</Typography></TableCell>
                <TableCell sx={diffCell(differs, 'info')}>
                  <Typography variant="body2">{asText(r.plex)}</Typography>
                </TableCell>
                <TableCell>
                  {r.editable && (
                    <IconButton size="small" onClick={() => onEdit({
                      entity: 'artist', refKey: artist.artist_dir, field: r.field, label: r.label,
                      value: r.desired?.length ? r.desired : r.plex,
                    })}>
                      <EditIcon fontSize="small" />
                    </IconButton>
                  )}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
      {d.genre_source && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', px: 1, mt: 0.5 }}>
          Genres from {d.genre_source}.
        </Typography>
      )}
      {p.summary && (
        <Typography variant="body2" color="text.secondary" sx={{ px: 1, mt: 1, whiteSpace: 'pre-line' }}>
          {p.summary}
        </Typography>
      )}
      <IssuesSection issues={artist.issues} onChanged={onChanged} notify={notify} />
    </Box>
  )
}

/** Change history for an artist — fetched, since the artist detail has none. */
function ArtistHistory({ artistDir, version, onChanged, notify }) {
  const [batches, setBatches] = useState(null)
  useEffect(() => {
    setBatches(null)
    api.libraryChanges({ artist_dir: artistDir, limit: 100 })
      .then((d) => setBatches(d.items || [])).catch(() => setBatches([]))
  }, [artistDir, version])
  return (
    <Box sx={{ p: 1 }}>
      <ChangeList batches={batches} onChanged={onChanged} notify={notify} />
    </Box>
  )
}

/**
 * The bottom pane for the selected album (or artist): every side of it —
 * the files, MusicBrainz, Plex — and the controls to make them agree.
 */
export default function DetailPane({
  mode, album, artist, loading, error, height, onHeight, onClose, tab, onTab,
  onEdit, onChanged, onAlbumDetail, notify, version,
}) {
  const tabs = mode === 'album' ? ALBUM_TABS : ARTIST_TABS
  const current = tabs.includes(tab) ? tab : tabs[0]
  const dupeCount = useMemo(() => (album?.duplicates?.album_groups?.length || 0)
    + (album?.duplicates?.track_groups?.length || 0), [album])
  const openIssues = (album?.issues || []).filter((i) => i.state === 'open').length

  const clearOverride = async (row) => {
    try {
      onAlbumDetail(await api.libraryClearOverride('album', album.key, row.field))
      notify?.(`${row.label} unpinned`)
    } catch (e) { notify?.(e.message, 'error') }
  }

  const header = mode === 'album' && album
    ? (
      <>
        <ArtImage src={album.art_url} size={36} />
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="subtitle2" noWrap>
            {album.year ? `(${album.year}) ` : ''}{album.album} — {album.album_artist}
          </Typography>
          <Typography variant="caption" color="text.secondary" noWrap display="block">
            {album.track_count} tracks · {STATUS[album.status]?.label || album.status}
            {album.confidence != null && ` · ${Math.round(album.confidence * 100)}% match`}
            {album.plex ? (album.plex.matched ? ' · matched in Plex' : ' · unmatched in Plex') : ' · not in Plex'}
          </Typography>
        </Box>
        <Box sx={{ display: { xs: 'none', md: 'flex' }, gap: 0.5 }}>
          {(album.flags || []).slice(0, 4).map((f) => (
            <Chip key={f.kind} size="small" variant="outlined" color={severityColor(f.severity)} label={f.label} />
          ))}
        </Box>
      </>
    )
    : (
      <>
        <ArtImage src={artist?.art_url} size={36} round icon="artist" />
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="subtitle2" noWrap>{artist?.name || artist?.artist_dir || 'Loading…'}</Typography>
          <Typography variant="caption" color="text.secondary" noWrap display="block">
            {artist ? `${artist.albums_count ?? artist.albums?.length ?? 0} albums · ${artist.tracks} tracks` : ''}
          </Typography>
        </Box>
      </>
    )

  const label = (t) => ({
    compare: openIssues ? `Compare (${openIssues})` : 'Compare',
    tracks: 'Tracks',
    match: 'Match',
    duplicates: dupeCount ? `Duplicates (${dupeCount})` : 'Duplicates',
    files: 'Files',
    history: 'History',
    overview: 'Overview',
  }[t])

  return (
    <BottomPane height={height} onHeight={onHeight}>
      <Box sx={{ px: 2, pb: 0.5, display: 'flex', alignItems: 'center', gap: 1 }}>
        {header}
        <Tooltip title="Close">
          <IconButton size="small" onClick={onClose}><CloseIcon fontSize="small" /></IconButton>
        </Tooltip>
      </Box>
      <Tabs value={current} onChange={(_, v) => onTab(v)} variant="scrollable"
        sx={{ minHeight: 34, px: 1 }}>
        {tabs.map((t) => <Tab key={t} value={t} label={label(t)} sx={{ minHeight: 34, py: 0 }} />)}
      </Tabs>
      <Divider />
      {loading && <LinearProgress />}
      {error && <Alert severity="error" sx={{ m: 1 }}>{error}</Alert>}
      <Box sx={{ flexGrow: 1, overflow: 'auto', minHeight: 0 }}>
        {mode === 'album' && album && (
          <>
            {current === 'compare' && (
              <CompareTab album={album} notify={notify} onChanged={onChanged}
                onClear={clearOverride}
                onEdit={(r) => onEdit({
                  entity: 'album', refKey: album.key, field: r.field, label: r.label,
                  value: r.desired ?? r.file, overridden: r.overridden,
                })} />
            )}
            {current === 'tracks' && <TracksTab album={album} onDetail={onAlbumDetail} notify={notify} />}
            {current === 'match' && <MatchTab album={album} onDetail={onAlbumDetail} notify={notify} />}
            {current === 'duplicates' && <DuplicatesTab album={album} onChanged={onChanged} />}
            {current === 'files' && <FilesTab album={album} />}
            {current === 'history' && (
              <Box sx={{ p: 1 }}>
                <ChangeList batches={album.history || []} onChanged={onChanged} notify={notify} />
              </Box>
            )}
          </>
        )}
        {mode === 'artist' && artist && (
          <>
            {current === 'overview' && (
              <ArtistOverviewTab artist={artist} onEdit={onEdit} onChanged={onChanged} notify={notify} />
            )}
            {current === 'history' && (
              <ArtistHistory artistDir={artist.artist_dir} version={version}
                onChanged={onChanged} notify={notify} />
            )}
          </>
        )}
      </Box>
    </BottomPane>
  )
}
