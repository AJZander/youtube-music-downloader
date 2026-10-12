import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Box, Button, Chip, CircularProgress, Paper, Stack, Typography,
} from '@mui/material'

import { api } from '../../api.js'
import { bytes } from './shared.jsx'

export default function Trash({ onChanged }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    api.libraryTrashList().then(setData).catch((e) => setError(e.message))
  }, [])
  useEffect(() => { refresh() }, [refresh])

  const act = async (fn, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return
    setBusy(true); setError(null)
    try {
      setMessage(await fn())
      refresh(); onChanged?.()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  if (!data) return <Box sx={{ textAlign: 'center', mt: 4 }}><CircularProgress /></Box>

  return (
    <Box>
      <Paper sx={{ p: 1.5, mb: 2, display: 'flex', alignItems: 'center', gap: 2,
        flexWrap: 'wrap' }}>
        <Typography variant="body2" sx={{ flexGrow: 1 }}>
          <strong>{data.entries}</strong> files in the library trash,
          holding <strong>{bytes(data.bytes)}</strong>. They still occupy disk
          space, and are still restorable, until you purge.
        </Typography>
        <Button size="small" color="error" disabled={busy || !data.entries}
          onClick={() => act(
            () => api.libraryTrashPurge({ older_than_days: 0 }),
            `Permanently delete all ${data.entries} trashed files (${bytes(data.bytes)})? `
            + 'This cannot be undone.')}>
          Purge everything
        </Button>
        <Button size="small" disabled={busy || !data.entries}
          onClick={() => act(() => api.libraryTrashPurge({ older_than_days: 30 }),
            'Permanently delete everything trashed more than 30 days ago?')}>
          Purge older than 30 days
        </Button>
      </Paper>

      {message && (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMessage(null)}>
          {message.purged != null
            ? `Purged ${message.purged} entries, freed ${bytes(message.freed_bytes)}.`
            : `Restored ${message.restored} entries.`
              + (message.failures?.length ? ` ${message.failures.length} failed.` : '')
              + ' Rescan the library to pick them back up in the index.'}
        </Alert>
      )}
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {!data.batches.length && (
        <Typography color="text.secondary" sx={{ mt: 4, textAlign: 'center' }}>
          The trash is empty.
        </Typography>
      )}

      {data.batches.map((batch) => (
        <Paper key={batch.batch_id} variant="outlined"
          sx={{ p: 1.5, mb: 1, display: 'flex', alignItems: 'center', gap: 1,
            flexWrap: 'wrap' }}>
          <Box sx={{ flexGrow: 1, minWidth: 220 }}>
            <Typography variant="body2">{batch.reason || 'removed from library'}</Typography>
            <Typography variant="caption" color="text.secondary">
              {batch.trashed_at} · batch {batch.batch_id}
            </Typography>
          </Box>
          <Chip size="small" label={`${batch.entries} files`} />
          <Chip size="small" variant="outlined" label={bytes(batch.bytes)} />
          <Stack direction="row" spacing={1}>
            <Button size="small" disabled={busy}
              onClick={() => act(() => api.libraryTrashRestore(batch.batch_id))}>
              Restore
            </Button>
            <Button size="small" color="error" disabled={busy}
              onClick={() => act(() => api.libraryTrashPurge({ batch_id: batch.batch_id }),
                `Permanently delete ${batch.entries} files (${bytes(batch.bytes)})?`)}>
              Purge
            </Button>
          </Stack>
        </Paper>
      ))}
    </Box>
  )
}
