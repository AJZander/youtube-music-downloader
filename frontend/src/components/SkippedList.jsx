import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Box, Button, Chip, FormControlLabel, Paper, Switch, Typography,
} from '@mui/material'

import { api } from '../api.js'

const PAGE = 100

export default function SkippedList({ onChanged }) {
  const [data, setData] = useState({ total: 0, different_album: 0, items: [] })
  const [onlyDifferent, setOnlyDifferent] = useState(false)
  const [shown, setShown] = useState(PAGE)
  const [busy, setBusy] = useState(false)

  const [recheck, setRecheck] = useState(null)
  const [notice, setNotice] = useState(null)

  const refresh = useCallback(() => {
    api.skipped({ limit: 500, only_different_album: onlyDifferent })
      .then(setData).catch(() => {})
    api.skippedRecheckPreview().then(setRecheck).catch(() => {})
  }, [onlyDifferent])
  useEffect(() => { refresh(); setShown(PAGE) }, [refresh])

  const act = async (id, action) => {
    await api.resolveTrack(id, action)
    refresh(); onChanged?.()
  }

  const dismissAll = async () => {
    if (!window.confirm(
      `Dismiss all ${data.total} skipped tracks? They stay skipped — this only `
      + 'clears the list.')) return
    setBusy(true)
    try { await api.acknowledgeSkipped(null); refresh(); onChanged?.() }
    finally { setBusy(false) }
  }

  const runRecheck = async () => {
    if (!window.confirm(
      `Re-queue ${recheck.tracks} skipped tracks? Each was skipped because the song `
      + 'was in your library on a different album. They go back through the duplicate '
      + 'check, which now only skips the same track on the same album.')) return
    setBusy(true)
    try {
      const r = await api.skippedRecheck()
      setNotice(`Re-queued ${r.requeued} tracks from ${r.jobs} downloads.`)
      refresh(); onChanged?.()
    } finally { setBusy(false) }
  }

  // Skips from before the same-album rule — reviewed ones included, which is
  // why this shows even when the list below is empty.
  const banner = (
    <>
      {notice && <Alert severity="success" sx={{ mb: 1.5 }} onClose={() => setNotice(null)}>
        {notice}</Alert>}
      {recheck?.tracks > 0 && (
        <Alert severity="info" sx={{ mb: 1.5 }}
          action={<Button size="small" color="inherit" disabled={busy} onClick={runRecheck}>
            Re-check {recheck.tracks}</Button>}>
          {recheck.tracks} earlier skip{recheck.tracks === 1 ? ' was' : 's were'} for a
          song that is in your library on a <strong>different album</strong>. Duplicates now
          only count on the same album, so these would download.
        </Alert>
      )}
    </>
  )

  if (!data.total) {
    return (
      <Box>
        {banner}
        <Typography color="text.secondary" sx={{ mt: 4, textAlign: 'center' }}>
          No skipped tracks to review.
        </Typography>
      </Box>
    )
  }

  const items = data.items.slice(0, shown)

  return (
    <Box>
      {banner}
      <Paper sx={{ p: 1.5, mb: 1.5, display: 'flex', alignItems: 'center',
        gap: 2, flexWrap: 'wrap' }}>
        <Typography variant="body2" sx={{ flexGrow: 1 }}>
          {data.total} skipped as duplicates
          {data.different_album > 0 && (
            <> — <strong>{data.different_album}</strong> matched a track on a
              different album, which is where false positives hide</>
          )}
        </Typography>
        <FormControlLabel
          control={<Switch size="small" checked={onlyDifferent}
            onChange={(e) => setOnlyDifferent(e.target.checked)} />}
          label="Different album only" />
        <Button size="small" color="error" disabled={busy} onClick={dismissAll}>
          Dismiss all
        </Button>
      </Paper>

      {items.map((t) => (
        <Paper key={t.id} sx={{ p: 2, mb: 1.5 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography variant="body1" sx={{ flexGrow: 1 }}>
              {[t.artist, t.title].filter(Boolean).join(' — ') || t.source_url}
            </Typography>
            {t.different_album && (
              <Chip size="small" color="warning" label="different album" />
            )}
          </Box>
          <Typography variant="caption" color="text.secondary" display="block">
            Wanted from: {t.album || '(no album)'}
          </Typography>
          <Typography variant="caption" color="text.secondary" display="block">
            Already in library: {t.matched_path || '(unknown)'}
          </Typography>
          <Box sx={{ mt: 1, display: 'flex', gap: 1 }}>
            <Button size="small" variant="outlined"
              onClick={() => act(t.id, 'force_download')}
              title="Ignore the duplicate match and download it. The file is still checked — a wrong version lands in Needs Review.">
              Force download
            </Button>
            <Button size="small" onClick={() => act(t.id, 'acknowledge')}
              title="Correctly skipped — clear it from this list">
              OK, dismiss
            </Button>
          </Box>
        </Paper>
      ))}

      {data.items.length > shown && (
        <Button fullWidth onClick={() => setShown((n) => n + PAGE)}>
          Show more ({data.items.length - shown} remaining)
        </Button>
      )}
    </Box>
  )
}
