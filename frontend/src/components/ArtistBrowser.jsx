import { useState } from 'react'
import {
  Alert, Box, Button, CircularProgress, List, ListItem, ListItemText, Paper,
  TextField, Typography,
} from '@mui/material'

import { api } from '../api.js'

export default function ArtistBrowser({ onQueued }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)
  const [queued, setQueued] = useState({})

  const browse = async () => {
    if (!url.trim()) return
    setBusy(true); setError(null); setData(null)
    try {
      setData(await api.artistReleases(url.trim()))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const queue = async (release) => {
    try {
      await api.createJob(release.url)
      setQueued((q) => ({ ...q, [release.browse_id]: true }))
      onQueued?.()
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <Paper sx={{ p: 2 }}>
      <Typography variant="subtitle1" gutterBottom>
        Browse a YouTube Music artist's releases
      </Typography>
      <Box sx={{ display: 'flex', gap: 1 }}>
        <TextField fullWidth size="small"
          label="Artist / channel URL (music.youtube.com/channel/UC… or youtube.com/@handle)"
          value={url} onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && browse()} />
        <Button variant="contained" onClick={browse} disabled={busy}>
          {busy ? <CircularProgress size={16} /> : 'Browse'}
        </Button>
      </Box>
      {error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}
      {data && (
        <Box sx={{ mt: 1.5 }}>
          <Typography variant="h6">{data.artist}</Typography>
          <List dense>
            {data.releases.map((r) => (
              <ListItem key={r.browse_id}
                secondaryAction={
                  queued[r.browse_id]
                    ? <Typography variant="caption" color="success.main">queued</Typography>
                    : <Button size="small" onClick={() => queue(r)}>Queue</Button>
                }>
                <ListItemText primary={r.title}
                  secondary={[r.type, r.year].filter(Boolean).join(' · ')} />
              </ListItem>
            ))}
          </List>
          {!data.releases.length && (
            <Typography color="text.secondary">No releases found.</Typography>
          )}
        </Box>
      )}
    </Paper>
  )
}
