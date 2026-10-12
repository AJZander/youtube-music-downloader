import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, CircularProgress, FormControlLabel,
  LinearProgress, Paper, Radio, Stack, Switch, Tooltip, Typography,
} from '@mui/material'

import { api } from '../../api.js'
import BlockIcon from '@mui/icons-material/Block'

import { QualityChip, bytes } from './shared.jsx'

const PAGE = 50

function Copy({ file, isKeeper, onMakeKeeper, checked, onToggle }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.25 }}>
      <Radio size="small" checked={isKeeper} onChange={onMakeKeeper} sx={{ p: 0.5 }} />
      <Checkbox size="small" checked={checked} disabled={isKeeper}
        onChange={onToggle} sx={{ p: 0.5 }} />
      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        <Typography variant="body2" noWrap title={file.path}
          sx={{ fontFamily: 'monospace', fontSize: '0.78rem' }}>
          {file.path}
        </Typography>
        <Typography variant="caption" color="text.secondary" noWrap>
          {file.reasons.join(' · ')}
        </Typography>
      </Box>
      <QualityChip format={file.format} bitrate={file.bitrate_kbps} />
      <Typography variant="caption" color="text.secondary"
        sx={{ width: 46, textAlign: 'right' }}>
        {file.duration_sec ? `${Math.floor(file.duration_sec / 60)}:${
          String(file.duration_sec % 60).padStart(2, '0')}` : '—'}
      </Typography>
      <Typography variant="caption" color="text.secondary"
        sx={{ width: 60, textAlign: 'right' }}>
        {bytes(file.filesize_bytes)}
      </Typography>
    </Box>
  )
}

function Group({ group, selection, setSelection, onDismissed }) {
  const [keeper, setKeeper] = useState(group.copies[0].path)
  const [busy, setBusy] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const picked = selection.get(group.id) || new Set()

  const setPicked = (next) => setSelection((prev) => {
    const map = new Map(prev)
    if (next.size) map.set(group.id, next); else map.delete(group.id)
    return map
  })

  const makeKeeper = (path) => {
    setKeeper(path)
    // Keeping a different copy shifts, not clears, the selection.
    const next = new Set(group.copies.map((c) => c.path).filter((p) => p !== path))
    setPicked(picked.size ? next : new Set())
  }

  const toggleGroup = (on) => setPicked(on
    ? new Set(group.copies.map((c) => c.path).filter((p) => p !== keeper))
    : new Set())

  const notDuplicates = async () => {
    setBusy(true)
    try {
      await api.libraryNotDuplicates(
        'track', group.copies.map((c) => c.path), 'marked not duplicates')
      setPicked(new Set())
      setDismissed(true)
      onDismissed?.()
    } finally { setBusy(false) }
  }

  if (dismissed) {
    return (
      <Alert severity="success" sx={{ mb: 1 }}>
        <strong>{group.title}</strong> — recorded as different recordings.
        Undo it on the Ignored tab.
      </Alert>
    )
  }

  const risky = group.duration_spread > 5
  return (
    <Paper variant="outlined" sx={{ p: 1.25, mb: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Checkbox size="small" sx={{ p: 0.5 }}
          checked={picked.size > 0}
          indeterminate={picked.size > 0 && picked.size < group.copies.length - 1}
          onChange={(e) => toggleGroup(e.target.checked)} />
        <Typography variant="body2" sx={{ flexGrow: 1, minWidth: 180 }}>
          <strong>{group.title}</strong> — {group.artist}
        </Typography>
        <Chip size="small" label={`${group.copies.length} copies`} />
        {risky && (
          <Tooltip title="These copies differ in length, so they may be different
            versions (a radio edit, a live take, an extended mix) rather than duplicates.">
            <Chip size="small" color="warning"
              label={`lengths differ by ${group.duration_spread}s`} />
          </Tooltip>
        )}
        <Chip size="small" variant="outlined" label={bytes(group.reclaimable_bytes)} />
        <Tooltip title="These are different recordings that happen to share a
          title. Recorded permanently so no future scan reports them.">
          <span>
            <Button size="small" startIcon={<BlockIcon />} disabled={busy}
              onClick={notDuplicates}>
              Not duplicates
            </Button>
          </span>
        </Tooltip>
      </Box>
      <Box sx={{ mt: 0.5 }}>
        {group.copies.map((c) => (
          <Copy key={c.path} file={c} isKeeper={c.path === keeper}
            onMakeKeeper={() => makeKeeper(c.path)}
            checked={picked.has(c.path)}
            onToggle={() => {
              const next = new Set(picked)
              if (next.has(c.path)) next.delete(c.path); else next.add(c.path)
              setPicked(next)
            }} />
        ))}
      </Box>
    </Paper>
  )
}

export default function TrackDupes({ onChanged }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [confidentOnly, setConfidentOnly] = useState(true)
  const [includeCovered, setIncludeCovered] = useState(false)
  const [shown, setShown] = useState(PAGE)
  const [selection, setSelection] = useState(new Map())
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  const refresh = useCallback(() => {
    setLoading(true); setError(null); setSelection(new Map())
    api.libraryDuplicates('tracks', {
      limit: 500, confident_only: confidentOnly, include_covered: includeCovered,
    })
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [confidentOnly, includeCovered])
  useEffect(() => { refresh(); setShown(PAGE) }, [refresh])

  const selectedPaths = useMemo(
    () => [...selection.values()].flatMap((s) => [...s]), [selection])
  const selectedBytes = useMemo(() => {
    const sizes = new Map()
    data?.items.forEach((g) => g.copies.forEach((c) => sizes.set(c.path, c.filesize_bytes || 0)))
    return selectedPaths.reduce((n, p) => n + (sizes.get(p) || 0), 0)
  }, [selectedPaths, data])

  const remove = async () => {
    if (!window.confirm(
      `Move ${selectedPaths.length} files (${bytes(selectedBytes)}) to the library `
      + 'trash? They stay restorable until you purge.')) return
    setBusy(true)
    try {
      const r = await api.libraryTrash(selectedPaths, 'duplicate track')
      // Copies left standing in a pruned group are a decision about *those*
      // copies. Recorded per group — pooling survivors across groups would
      // pair unrelated tracks with each other and explode into n² rows.
      const removing = new Set(selectedPaths)
      for (const group of (data?.items || []).filter((g) => selection.has(g.id))) {
        const survivors = group.copies
          .map((c) => c.path).filter((p) => !removing.has(p))
        if (survivors.length > 1) {
          await api.libraryNotDuplicates(
            'track', survivors, `kept ${survivors.length} copies of ${group.title}`)
        }
      }
      setResult(r); onChanged?.(r); refresh()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  if (loading) return <Box sx={{ textAlign: 'center', mt: 4 }}><CircularProgress /></Box>
  if (error) return <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>

  return (
    <Box>
      {result && (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setResult(null)}>
          Moved {result.trashed} files ({bytes(result.freed_bytes)}) to trash.
        </Alert>
      )}
      <Paper sx={{ p: 1.5, mb: 2 }}>
        <Typography variant="body2" sx={{ mb: 1 }}>
          <strong>{data?.total || 0}</strong> tracks exist in more than one place,
          holding <strong>{bytes(data?.reclaimable_bytes)}</strong> of redundant copies.
          These are the scattered ones — singles folders, compilations, loose files.
        </Typography>
        <Stack direction="row" spacing={2} sx={{ flexWrap: 'wrap', alignItems: 'center' }}>
          <FormControlLabel
            control={<Switch size="small" checked={confidentOnly}
              onChange={(e) => setConfidentOnly(e.target.checked)} />}
            label={<Typography variant="body2">Same length only</Typography>} />
          <Tooltip title="Duplicate albums already cover these; resolve them on the
            Albums tab instead, where you can see the whole folder at once.">
            <FormControlLabel
              control={<Switch size="small" checked={includeCovered}
                onChange={(e) => setIncludeCovered(e.target.checked)} />}
              label={<Typography variant="body2">Include ones inside duplicate albums</Typography>} />
          </Tooltip>
          <Box sx={{ flexGrow: 1 }} />
          <Button size="small" variant="contained" color="error"
            disabled={busy || !selectedPaths.length} onClick={remove}>
            Remove {selectedPaths.length} selected ({bytes(selectedBytes)})
          </Button>
        </Stack>
        {busy && <LinearProgress sx={{ mt: 1 }} />}
      </Paper>

      {!data?.total && (
        <Typography color="text.secondary" sx={{ mt: 4, textAlign: 'center' }}>
          No scattered duplicate tracks.
        </Typography>
      )}

      {data?.items.slice(0, shown).map((group) => (
        <Group key={group.id} group={group} onDismissed={onChanged}
          selection={selection} setSelection={setSelection} />
      ))}

      {data?.items.length > shown && (
        <Button fullWidth onClick={() => setShown((n) => n + PAGE)}>
          Show more ({data.items.length - shown} remaining)
        </Button>
      )}
    </Box>
  )
}
