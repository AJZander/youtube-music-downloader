import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Box, Button, Chip, CircularProgress, Paper, Typography,
} from '@mui/material'
import UndoIcon from '@mui/icons-material/Undo'

import { api } from '../../api.js'

const SCOPE_LABEL = {
  album: 'different albums',
  track: 'different recordings',
  artist: 'different artists',
}

/**
 * Every "not a duplicate" judgement, and every group where more than one copy
 * was deliberately kept. Undoing one lets the next scan report it again.
 */
export default function Ignored({ onChanged }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    api.libraryExclusions().then(setData).catch((e) => setError(e.message))
  }, [])
  useEffect(() => { refresh() }, [refresh])

  const forget = async (ids) => {
    setBusy(true); setError(null)
    try { await api.libraryForgetExclusions(ids); refresh(); onChanged?.() }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  if (!data) return <Box sx={{ textAlign: 'center', mt: 4 }}><CircularProgress /></Box>

  if (!data.items.length) {
    return (
      <Typography color="text.secondary" sx={{ mt: 4, textAlign: 'center' }}>
        Nothing ignored yet. Marking a group as &quot;not duplicates&quot;, or
        keeping more than one copy of an album, records the decision here so
        later scans stay quiet about it.
      </Typography>
    )
  }

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <Paper sx={{ p: 1.5, mb: 2 }}>
        <Typography variant="body2">
          <strong>{data.items.length}</strong> recorded decisions
          ({data.total} pairs). These are excluded from every future scan.
          Undo one to have it reported again.
        </Typography>
      </Paper>

      {data.items.map((entry) => (
        <Paper key={entry.ids.join(',')} variant="outlined" sx={{ p: 1.5, mb: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5,
            flexWrap: 'wrap' }}>
            <Chip size="small" label={SCOPE_LABEL[entry.scope] || entry.scope} />
            <Typography variant="caption" color="text.secondary" sx={{ flexGrow: 1 }}>
              {entry.reason || 'no reason recorded'} · {entry.created_at}
            </Typography>
            <Button size="small" startIcon={<UndoIcon />} disabled={busy}
              onClick={() => forget(entry.ids)}>
              Undo
            </Button>
          </Box>
          {entry.members.map((m) => (
            <Typography key={m} variant="caption" display="block"
              sx={{ fontFamily: 'monospace', wordBreak: 'break-all', pl: 1 }}>
              {m}
            </Typography>
          ))}
        </Paper>
      ))}
    </Box>
  )
}
