import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Box, Button, Chip, Paper, Typography,
} from '@mui/material'

import { api } from '../api.js'

export default function NeedsReview({ onChanged }) {
  const [tracks, setTracks] = useState([])
  // what went wrong with the last action or load, said rather than swallowed
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    api.needsReview().then(setTracks).catch((e) => setError(e.message))
  }, [])
  useEffect(() => { refresh() }, [refresh])

  const act = async (id, action) => {
    try {
      await api.resolveTrack(id, action)
      setError(null)
    } catch (e) {
      setError(e.message)
    }
    refresh(); onChanged?.()
  }
  const queue = (videoId) => api.createJob(`https://music.youtube.com/watch?v=${videoId}`)
    .then(() => { setError(null); onChanged?.() })
    .catch((e) => setError(e.message))

  const alert = error && (
    <Alert severity="error" sx={{ mb: 1.5 }} onClose={() => setError(null)}>{error}</Alert>
  )

  if (!tracks.length) {
    return (
      <Box>
        {alert}
        <Typography color="text.secondary" sx={{ mt: 4, textAlign: 'center' }}>
          Nothing needs review.
        </Typography>
      </Box>
    )
  }

  return (
    <Box>
      {alert}
      {tracks.map((t) => {
        let candidates = []
        try { candidates = JSON.parse(t.match_candidates || '[]') } catch { /* ignore */ }
        return (
          <Paper key={t.id} sx={{ p: 2, mb: 1.5 }}>
            <Typography variant="body1">
              {[t.artist, t.title].filter(Boolean).join(' — ') || t.source_url}
            </Typography>
            {t.album && <Typography variant="caption" color="text.secondary">{t.album}</Typography>}
            <Typography variant="body2" color="error" sx={{ mt: 0.5 }}>
              {t.error_code}: {t.error_message}
            </Typography>
            {candidates.length > 0 && (
              <Box sx={{ mt: 1 }}>
                <Typography variant="caption" color="text.secondary">
                  Rejected candidates (queue one manually if correct):
                </Typography>
                {candidates.map((c) => (
                  <Box key={c.video_id} sx={{ display: 'flex', gap: 1, alignItems: 'center', mt: 0.5 }}>
                    <Chip size="small" label={c.result_type || 'video'} />
                    <Typography variant="caption" sx={{ flexGrow: 1 }}>
                      {c.artists} — {c.title}
                      {c.duration_sec ? ` (${Math.round(c.duration_sec)}s)` : ''}
                    </Typography>
                    <Button size="small" onClick={() => queue(c.video_id)}>
                      Queue this
                    </Button>
                  </Box>
                ))}
              </Box>
            )}
            <Box sx={{ mt: 1, display: 'flex', gap: 1 }}>
              <Button size="small" variant="outlined" onClick={() => act(t.id, 'retry')}>
                Retry
              </Button>
              <Button size="small" variant="outlined" onClick={() => act(t.id, 'force_import')}
                title="Import this exact file as-is, keeping its duration">
                Force import
              </Button>
              <Button size="small" color="error" onClick={() => act(t.id, 'discard')}>
                Discard
              </Button>
            </Box>
          </Paper>
        )
      })}
    </Box>
  )
}
