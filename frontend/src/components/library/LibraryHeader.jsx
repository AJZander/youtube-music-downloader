import { useState } from 'react'
import {
  Alert, Box, Button, Chip, IconButton, LinearProgress, ListItemText, Menu, MenuItem,
  Paper, Tooltip, Typography,
} from '@mui/material'
import CancelIcon from '@mui/icons-material/Cancel'
import FactCheckIcon from '@mui/icons-material/FactCheck'
import ManageSearchIcon from '@mui/icons-material/ManageSearch'
import MoreVertIcon from '@mui/icons-material/MoreVert'
import PauseIcon from '@mui/icons-material/Pause'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import RefreshIcon from '@mui/icons-material/Refresh'

import { PHASES, eta } from './issues.js'
import { bytes } from './shared.jsx'

function Stat({ label, value, hint, color }) {
  const chip = <Chip size="small" variant="outlined" color={color || 'default'}
    label={<span><strong>{value}</strong> {label}</span>} />
  return hint ? <Tooltip title={hint}>{chip}</Tooltip> : chip
}

function pct(part, whole) {
  if (part == null || !whole) return null
  return Math.round(100 * part / whole)
}

/** One bar for whichever long job is running: the library scan or a metadata run. */
function Progress({ scan, meta, onPause, onResume, onCancel }) {
  if (scan?.running) {
    return (
      <Box sx={{ mt: 1 }}>
        <LinearProgress variant={scan.total ? 'determinate' : 'indeterminate'}
          value={scan.pct || 0} />
        <Typography variant="caption" color="text.secondary">
          {scan.phase === 'walking' && 'Library scan: listing files…'}
          {scan.phase === 'reading' && (
            <>
              {`Library scan: reading tags ${(scan.done || 0).toLocaleString()} of ${
                (scan.total || 0).toLocaleString()} (${scan.pct}%)`}
              {scan.rate_per_sec != null
                && ` · ${scan.rate_per_sec}/sec · ${eta(scan.eta_sec) || '?'} left`}
            </>
          )}
          {scan.phase === 'pruning' && 'Library scan: removing entries for deleted files…'}
        </Typography>
      </Box>
    )
  }
  const run = meta?.run
  const busy = meta?.running || meta?.paused
  if (!busy) return null
  const value = run?.total ? Math.round(100 * run.done / run.total) : null
  const counts = run?.counts || {}
  const tally = [
    counts.applied && `${counts.applied} applied`,
    counts.review && `${counts.review} to check`,
    counts.unresolved && `${counts.unresolved} not identified`,
    counts.failed && `${counts.failed} failed`,
  ].filter(Boolean).join(', ')
  return (
    <Box sx={{ mt: 1, display: 'flex', alignItems: 'center', gap: 1 }}>
      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        <LinearProgress variant={value != null ? 'determinate' : 'indeterminate'}
          value={value || 0} color={meta.paused ? 'warning' : 'primary'} />
        <Typography variant="caption" color="text.secondary" noWrap display="block">
          {meta.paused ? 'Paused · ' : ''}
          {PHASES[meta.phase] || meta.phase}
          {run?.report_only ? ' (report only)' : ''}
          {run?.total ? ` · ${run.done.toLocaleString()} of ${run.total.toLocaleString()}` : ''}
          {run?.eta_sec != null && !meta.paused ? ` · ${eta(run.eta_sec)} left` : ''}
          {meta.current ? ` · ${meta.current}` : ''}
          {meta.applying && meta.phase !== 'applying' ? ` · applying fixes to ${meta.applying}` : ''}
          {meta.apply_queue ? ` · ${meta.apply_queue} fixes queued` : ''}
          {tally ? ` · ${tally}` : ''}
        </Typography>
      </Box>
      {meta.paused
        ? (
          <Tooltip title="Resume">
            <IconButton size="small" onClick={onResume}><PlayArrowIcon fontSize="small" /></IconButton>
          </Tooltip>
        )
        : (
          <Tooltip title="Pause">
            <IconButton size="small" onClick={onPause}><PauseIcon fontSize="small" /></IconButton>
          </Tooltip>
        )}
      <Tooltip title="Cancel this run">
        <IconButton size="small" onClick={onCancel}><CancelIcon fontSize="small" /></IconButton>
      </Tooltip>
    </Box>
  )
}

/**
 * Library header, one row when nothing is happening: what is on disk, how
 * much of it Plex and MusicBrainz agree on, and the controls for the two
 * long jobs — rescans and metadata runs.
 */
export default function LibraryHeader({
  overview, scan, meta, onScan, onFullRun, onCheckNew, onPause, onResume, onCancel,
  onAutoApply, onPlexEmptyTrash,
}) {
  const [dismissed, setDismissed] = useState(() => new Set())
  const [menu, setMenu] = useState(null)
  const running = scan?.running
  const cov = overview?.coverage || {}
  const losslessPct = pct(overview?.lossless_files, overview?.files)
  const matchedPct = cov.plex_albums
    ? pct(cov.plex_albums - (cov.plex_unmatched_albums || 0), cov.plex_albums) : null
  const genrePct = pct(cov.albums_with_genre, cov.plex_albums || cov.albums_total)
  const mbidPct = pct(cov.files_with_mbids, overview?.files)
  const plex = overview?.plex
  const messages = (meta?.messages || []).filter((m) => !dismissed.has(m))
  const metaBusy = meta?.running || meta?.paused

  return (
    <Paper sx={{ px: 1.5, py: 1, mb: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0.75 }}>
        <Stat label="files" value={overview?.files?.toLocaleString() ?? '—'}
          hint={overview ? `${bytes(overview.bytes)} on disk`
            + (losslessPct != null ? ` · ${losslessPct}% lossless` : '') : ''} />
        <Stat label="artists" value={overview?.artists?.toLocaleString() ?? '—'} />
        <Stat label="albums" value={overview?.albums?.toLocaleString() ?? '—'} />
        {matchedPct != null && (
          <Stat label="matched" value={`${matchedPct}%`}
            color={matchedPct >= 95 ? 'success' : 'default'}
            hint={`${(cov.plex_unmatched_albums || 0).toLocaleString()} of ${
              cov.plex_albums.toLocaleString()} Plex albums and ${
              (cov.plex_unmatched_artists || 0).toLocaleString()} artists have no online match`} />
        )}
        {genrePct != null && (
          <Stat label="genres" value={`${genrePct}%`}
            hint={`${(cov.albums_with_genre || 0).toLocaleString()} albums have genres in Plex`} />
        )}
        {mbidPct != null && (
          <Stat label="MBIDs" value={`${mbidPct}%`}
            hint={`${(cov.files_with_mbids || 0).toLocaleString()} files carry MusicBrainz IDs`} />
        )}
        {cov.files_not_in_plex > 0 && (
          <Stat label="not in Plex" value={cov.files_not_in_plex.toLocaleString()}
            color="warning" hint="Files Plex has not picked up yet" />
        )}
        {overview?.unreadable > 0 && (
          <Stat label="unreadable" value={overview.unreadable.toLocaleString()}
            hint="Files whose tags will not parse. They are skipped by the duplicate
              report and not retried on every scan." />
        )}
        {plex && (
          <Tooltip title={plex.reachable
            ? `Plex read ${plex.last_sync ? new Date(plex.last_sync).toLocaleString() : 'never'}`
            : 'Plex is not reachable — check PLEX_URL and PLEX_TOKEN'}>
            <Chip size="small" color={plex.reachable ? 'success' : 'error'}
              variant="outlined" label={plex.reachable ? 'Plex' : 'Plex offline'} />
          </Tooltip>
        )}

        <Box sx={{ flexGrow: 1 }} />

        {meta && (
          <Tooltip title={meta.auto_apply
            ? 'Confident fixes are applied as albums are checked. Click to turn off.'
            : 'Nothing changes until you apply it. Review the report, then turn auto-apply on.'}>
            <Chip size="small" variant={meta.auto_apply ? 'filled' : 'outlined'}
              color={meta.auto_apply ? 'success' : 'default'}
              label={meta.auto_apply ? 'Auto-apply on' : 'Auto-apply off — turn on'}
              onClick={() => onAutoApply(!meta.auto_apply)} />
          </Tooltip>
        )}
        <Button size="small" startIcon={<RefreshIcon />} disabled={running}
          onClick={() => onScan(false)}>
          {running ? 'Scanning…' : 'Rescan'}
        </Button>
        <Tooltip title="Check what is new or changed since the last run">
          <span>
            <Button size="small" startIcon={<ManageSearchIcon />} disabled={metaBusy}
              onClick={onCheckNew}>
              Check new
            </Button>
          </span>
        </Tooltip>
        <Button size="small" variant="contained" startIcon={<FactCheckIcon />}
          disabled={metaBusy} onClick={onFullRun}>
          Full run
        </Button>
        <IconButton size="small" onClick={(e) => setMenu(e.currentTarget)}>
          <MoreVertIcon fontSize="small" />
        </IconButton>
        <Menu anchorEl={menu} open={!!menu} onClose={() => setMenu(null)}>
          <MenuItem disabled={running} onClick={() => { setMenu(null); onScan(true) }}>
            <ListItemText primary="Full rescan"
              secondary="Re-read every file's tags — slow; only after editing tags elsewhere" />
          </MenuItem>
          <MenuItem onClick={() => { setMenu(null); onPlexEmptyTrash() }}>
            <ListItemText primary="Empty Plex trash"
              secondary="Drop Plex entries for files that are gone" />
          </MenuItem>
        </Menu>
      </Box>

      <Progress scan={scan} meta={meta} onPause={onPause} onResume={onResume}
        onCancel={onCancel} />

      {messages.map((m) => (
        <Alert key={m} severity="info" sx={{ mt: 1, py: 0, '& .MuiAlert-icon': { py: 0.5 } }}
          onClose={() => setDismissed((prev) => new Set([...prev, m]))}>
          <Typography variant="caption">{m}</Typography>
        </Alert>
      ))}

      {!running && overview?.unenriched > 0 && (
        <Alert severity="warning" sx={{ mt: 1, py: 0 }}
          action={<Button size="small" onClick={() => onScan(false)}>Scan now</Button>}>
          <Typography variant="caption">
            {overview.unenriched.toLocaleString()} files have not been read yet — duplicate
            detection and metadata checks miss them until a scan reads their tags.
          </Typography>
        </Alert>
      )}
    </Paper>
  )
}
