import { useCallback, useEffect, useState } from 'react'
import {
  Box, Button, Chip, Paper, Typography,
} from '@mui/material'

import { api } from '../api.js'

export default function NeedsReview({ onChanged }) {
  const [tracks, setTracks] = useState([])

  const refresh = useCallback(() => {
    api.needsReview().then(setTracks).catch(() => {})
  }, [])
  useEffect(() => { refresh() }, [refresh])

  const act = async (id, action) => {
    await api.resolveTrack(id, action)
    refresh(); onChanged?.()
  }

  if (!tracks.length) {
    return <Typography color="text.secondary" sx={{ mt: 4, textAlign: 'center' }}>
      Nothing needs review.
    </Typography>
  }

  return (
    <Box>
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
                    <Button size="small" onClick={() =>
                      api.createJob(`https://music.youtube.com/watch?v=${c.video_id}`)
                        .then(() => onChanged?.())}>
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
              <Button size="small" variant="outlined" onClick={() => act(t.id, 'force_import')}>
                Force
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
