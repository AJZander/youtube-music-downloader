import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, CircularProgress, Collapse, Divider,
  FormControlLabel, LinearProgress, Paper, Stack, Switch, Tooltip, Typography,
} from '@mui/material'
import BlockIcon from '@mui/icons-material/Block'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'

import { api } from '../../api.js'
import { HealthChips, QualityChip, bytes } from './shared.jsx'

const PAGE = 25

function FileRow({ file, checked, onToggle }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.25 }}>
      {onToggle && (
        <Checkbox size="small" checked={checked} onChange={onToggle} sx={{ p: 0.5 }} />
      )}
      <Typography variant="caption" sx={{ width: 28, color: 'text.secondary' }}>
        {file.track_number ?? '–'}
      </Typography>
      <Typography variant="body2" noWrap sx={{ flexGrow: 1, minWidth: 0 }}
        title={file.path}>
        {file.title || file.path}
      </Typography>
      {file.duration_delta != null && Math.abs(file.duration_delta) > 5 && (
        <Tooltip title="length differs from the matching track in a copy you are keeping">
          <Chip size="small" color="warning" variant="outlined"
            label={`${file.duration_delta > 0 ? '+' : ''}${file.duration_delta}s`} />
        </Tooltip>
      )}
      <QualityChip format={file.format} bitrate={file.bitrate_kbps} />
      <Typography variant="caption" color="text.secondary"
        sx={{ width: 62, textAlign: 'right' }}>
        {bytes(file.filesize_bytes)}
      </Typography>
    </Box>
  )
}

/** One physical album folder. Keeping is a checkbox, not a radio: three
 *  folders that share tracks are often three real compilations. */
function Instance({ inst, isKeeper, onToggleKeep, canUnkeep, selected,
                   onToggleFile, onToggleAll }) {
  const [open, setOpen] = useState(false)
  const removable = inst.covered || []
  const orphans = inst.orphans || []
  const allChecked = removable.length > 0 && removable.every((f) => selected.has(f.path))
  const pickedHere = removable.filter((f) => selected.has(f.path)).length

  return (
    <Paper variant="outlined" sx={{
      p: 1.5, mb: 1,
      borderLeft: 4, borderLeftColor: isKeeper ? 'success.main' : 'divider',
      bgcolor: isKeeper ? 'action.hover' : 'transparent',
    }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Tooltip title={isKeeper
          ? (canUnkeep ? 'Stop keeping this copy' : 'At least one copy must be kept')
          : 'Keep this copy as well'}>
          <span>
            <Checkbox size="small" checked={isKeeper} sx={{ p: 0.5 }}
              disabled={isKeeper && !canUnkeep}
              onChange={() => onToggleKeep(inst.dirpath)} />
          </span>
        </Tooltip>
        <Box sx={{ flexGrow: 1, minWidth: 220 }}>
          <Typography variant="body2" sx={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>
            {inst.dirpath}
          </Typography>
          <Stack direction="row" spacing={0.5} sx={{ mt: 0.5, flexWrap: 'wrap', gap: 0.5 }}>
            <Chip size="small" label={`${inst.track_count} tracks`} />
            {inst.formats.map((f) => (
              <QualityChip key={f} format={f} bitrate={inst.avg_bitrate} />
            ))}
            <Chip size="small" variant="outlined" label={bytes(inst.bytes)} />
            {inst.year && <Chip size="small" variant="outlined" label={inst.year} />}
            <HealthChips issues={inst.health} />
          </Stack>
        </Box>
        {isKeeper
          ? <Chip size="small" color="success" label="KEEPING" />
          : (
            <Box sx={{ textAlign: 'right' }}>
              <Typography variant="caption" color="error.main" display="block">
                {pickedHere} of {inst.track_count} to remove
              </Typography>
              {orphans.length > 0 && (
                <Typography variant="caption" color="warning.main" display="block">
                  {orphans.length} not in any copy you are keeping
                </Typography>
              )}
            </Box>
          )}
        <Button size="small" onClick={() => setOpen((v) => !v)}
          endIcon={open ? <ExpandLessIcon /> : <ExpandMoreIcon />}>
          Tracks
        </Button>
      </Box>

      <Collapse in={open} unmountOnExit>
        <Divider sx={{ my: 1 }} />
        {isKeeper
          ? (inst.files || []).map((f) => <FileRow key={f.path} file={f} />)
          : (
            <>
              {removable.length > 0 && (
                <>
                  <FormControlLabel
                    sx={{ ml: 0 }}
                    control={<Checkbox size="small" checked={allChecked}
                      onChange={() => onToggleAll(removable, !allChecked)} />}
                    label={<Typography variant="caption">
                      {removable.length} also in a copy you are keeping
                    </Typography>} />
                  {removable.map((f) => (
                    <FileRow key={f.path} file={f} checked={selected.has(f.path)}
                      onToggle={() => onToggleFile(f.path)} />
                  ))}
                </>
              )}
              {orphans.length > 0 && (
                <Box sx={{ mt: 1 }}>
                  <Typography variant="caption" color="warning.main">
                    Only in this folder — removing these loses the track entirely.
                    Left unchecked on purpose.
                  </Typography>
                  {orphans.map((f) => (
                    <FileRow key={f.path} file={f} checked={selected.has(f.path)}
                      onToggle={() => onToggleFile(f.path)} />
                  ))}
                </Box>
              )}
            </>
          )}
      </Collapse>
    </Paper>
  )
}

function coveredPaths(instances) {
  return new Set(instances.flatMap((i) => (i.covered || []).map((f) => f.path)))
}

function Group({ group, onResolved }) {
  const [instances, setInstances] = useState(group.instances)
  const [keepers, setKeepers] = useState(
    () => new Set([group.instances.find((i) => i.is_keeper)?.dirpath
                   ?? group.instances[0].dirpath]))
  const [selected, setSelected] = useState(() => coveredPaths(group.instances))
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)
  const [error, setError] = useState(null)

  const dirpaths = useMemo(() => instances.map((i) => i.dirpath), [instances])

  const toggleKeep = async (dirpath) => {
    const next = new Set(keepers)
    if (next.has(dirpath)) {
      if (next.size === 1) return       // something has to survive
      next.delete(dirpath)
    } else {
      next.add(dirpath)
    }
    setBusy(true); setError(null)
    try {
      // The server recomputes which tracks are redundant, because "is this
      // the same track" lives in one place and it is not here.
      const plan = await api.libraryAlbumPlan(dirpaths, [...next])
      setInstances(plan.instances)
      setKeepers(new Set(plan.keepers))
      setSelected(coveredPaths(plan.instances))
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const toggleFile = (path) => setSelected((prev) => {
    const next = new Set(prev)
    if (next.has(path)) next.delete(path); else next.add(path)
    return next
  })
  const toggleAll = (files, on) => setSelected((prev) => {
    const next = new Set(prev)
    files.forEach((f) => (on ? next.add(f.path) : next.delete(f.path)))
    return next
  })

  const paths = useMemo(() => [...selected], [selected])
  const size = useMemo(() => instances
    .flatMap((i) => [...(i.covered || []), ...(i.orphans || [])])
    .filter((f) => selected.has(f.path))
    .reduce((n, f) => n + (f.filesize_bytes || 0), 0), [instances, selected])

  const apply = async () => {
    setBusy(true); setError(null)
    try {
      const kept = [...keepers]
      const result = await api.libraryTrash(
        paths, `duplicate album: kept ${kept.length} of ${instances.length}`,
        kept.length > 1 ? kept : null, 'album')
      setDone(result)
      onResolved?.(result)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const notDuplicates = async () => {
    setBusy(true); setError(null)
    try {
      await api.libraryNotDuplicates('album', dirpaths, 'marked not duplicates')
      setDone({ dismissed: true })
      onResolved?.()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  if (done) {
    return (
      <Alert severity="success" sx={{ mb: 1.5 }}>
        {done.dismissed
          ? 'Marked as different albums — this group will not come back. '
            + 'Undo it on the Ignored tab.'
          : <>
              Moved {done.trashed} files ({bytes(done.freed_bytes)}) to trash — restore
              them any time from the Trash tab.
              {done.emptied_folders?.length > 0
                && ` Removed ${done.emptied_folders.length} emptied folder(s).`}
              {done.kept_together > 0
                && ' The copies you kept are recorded, so this group will not be '
                  + 'reported again.'}
            </>}
      </Alert>
    )
  }

  const keepingAll = keepers.size === instances.length
  return (
    <Paper sx={{ p: 2, mb: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <Typography variant="subtitle1" sx={{ flexGrow: 1 }}>
          {group.album_artist} — <strong>{group.album}</strong>
        </Typography>
        <Tooltip title={group.kind === 'exact'
          ? 'Both folders name the same album and their track lists match'
          : 'Track lists overlap heavily but the albums are named differently — '
            + 'often a deluxe edition against the standard one, or two '
            + 'compilations sharing songs. Check the extra tracks.'}>
          <Chip size="small" color={group.kind === 'exact' ? 'default' : 'warning'}
            label={group.kind === 'exact' ? 'same album' : 'overlapping'} />
        </Tooltip>
        <Chip size="small" variant="outlined"
          label={`keeping ${keepers.size} of ${instances.length}`} />
        <Chip size="small" variant="outlined" label={`${bytes(size)} selected`} />
      </Box>

      {busy && <LinearProgress sx={{ mb: 1 }} />}
      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}

      {instances.map((inst) => (
        <Instance key={inst.dirpath} inst={inst}
          isKeeper={keepers.has(inst.dirpath)}
          canUnkeep={keepers.size > 1}
          onToggleKeep={toggleKeep}
          selected={selected} onToggleFile={toggleFile} onToggleAll={toggleAll} />
      ))}

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 1, flexWrap: 'wrap' }}>
        <Typography variant="caption" color="text.secondary" sx={{ flexGrow: 1 }}>
          {keepingAll
            ? 'Every copy is ticked to keep — say they are different albums to '
              + 'stop this group coming back.'
            : 'Files move to a hidden trash folder inside the library. Nothing is '
              + 'deleted until you purge.'}
        </Typography>
        <Button size="small" startIcon={<BlockIcon />} disabled={busy}
          onClick={notDuplicates}
          title="These are different albums that happen to share tracks. Recorded permanently.">
          Not duplicates
        </Button>
        <Button size="small" variant="contained" color="error"
          disabled={busy || !paths.length} onClick={apply}>
          Remove {paths.length} file{paths.length === 1 ? '' : 's'}
        </Button>
      </Box>
    </Paper>
  )
}

export default function AlbumDupes({ onChanged }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [confidentOnly, setConfidentOnly] = useState(false)
  const [shown, setShown] = useState(PAGE)

  const refresh = useCallback(() => {
    setLoading(true); setError(null)
    api.libraryDuplicates('albums', { limit: 500, confident_only: confidentOnly })
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [confidentOnly])
  useEffect(() => { refresh(); setShown(PAGE) }, [refresh])

  if (loading) return <Box sx={{ textAlign: 'center', mt: 4 }}><CircularProgress /></Box>
  if (error) return <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>
  if (!data?.total) {
    return <Typography color="text.secondary" sx={{ mt: 4, textAlign: 'center' }}>
      No duplicate albums found.
    </Typography>
  }

  return (
    <Box>
      <Paper sx={{ p: 1.5, mb: 2, display: 'flex', alignItems: 'center', gap: 2,
        flexWrap: 'wrap' }}>
        <Typography variant="body2" sx={{ flexGrow: 1 }}>
          <strong>{data.total}</strong> albums exist more than once —{' '}
          <strong>{bytes(data.reclaimable_bytes)}</strong> in redundant copies.
          The best-scoring copy is ticked to keep; tick more than one to keep
          several, and that choice is remembered.
        </Typography>
        <FormControlLabel
          control={<Switch size="small" checked={confidentOnly}
            onChange={(e) => setConfidentOnly(e.target.checked)} />}
          label={<Typography variant="body2">Unambiguous only</Typography>} />
      </Paper>

      {data.items.slice(0, shown).map((group) => (
        <Group key={group.id} group={group} onResolved={onChanged} />
      ))}

      {data.items.length > shown && (
        <Button fullWidth onClick={() => setShown((n) => n + PAGE)}>
          Show more ({data.items.length - shown} remaining)
        </Button>
      )}
    </Box>
  )
}
