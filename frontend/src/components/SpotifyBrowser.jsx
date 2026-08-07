import { useEffect, useState } from 'react'
import {
  Alert, Box, Button, CircularProgress, List, ListItem, ListItemText, Paper,
  TextField, Typography,
} from '@mui/material'

import { api } from '../api.js'

export default function SpotifyBrowser({ onQueued }) {
  const [connected, setConnected] = useState(null)
  const [playlists, setPlaylists] = useState([])
  const [url, setUrl] = useState('')
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.spotifyPlaylists()
      .then((r) => { setConnected(r.connected); setPlaylists(r.playlists) })
      .catch(() => setConnected(false))
  }, [])

  const resolve = async () => {
    if (!url.trim()) return
    setBusy(true); setError(null); setPreview(null)
    try {
      setPreview(await api.spotifyResolve(url.trim()))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const queue = async (u) => {
    setBusy(true); setError(null)
    try {
      await api.createJob(u)
      onQueued?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Box>
      <Paper sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle1" gutterBottom>Preview a Spotify link</Typography>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <TextField fullWidth size="small" label="Spotify track / album / playlist / artist URL"
            value={url} onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && resolve()} />
          <Button variant="outlined" onClick={resolve} disabled={busy}>
            {busy ? <CircularProgress size={16} /> : 'Preview'}
          </Button>
          <Button variant="contained" onClick={() => queue(url.trim())} disabled={busy || !url.trim()}>
            Queue
          </Button>
        </Box>
        {error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}
        {preview && (
          <Box sx={{ mt: 1.5 }}>
            <Typography variant="body2" color="text.secondary">
              {preview.type}: <b>{preview.title}</b>{preview.artist ? ` — ${preview.artist}` : ''}
              {preview.tracks ? ` · ${preview.tracks.length} tracks` : ''}
              {preview.albums ? ` · ${preview.albums.length} releases` : ''}
            </Typography>
            <List dense sx={{ maxHeight: 300, overflow: 'auto' }}>
              {(preview.tracks || []).slice(0, 100).map((t) => (
                <ListItem key={t.provider_track_id} disablePadding sx={{ py: 0.25 }}>
                  <ListItemText primary={`${t.artist} — ${t.title}`}
                    secondary={t.album} />
                </ListItem>
              ))}
              {(preview.albums || []).map((a) => (
                <ListItem key={a.id}
                  secondaryAction={
                    <Button size="small"
                      onClick={() => queue(`https://open.spotify.com/album/${a.id}`)}>
                      Queue album
                    </Button>
                  }>
                  <ListItemText primary={a.name}
                    secondary={`${a.album_type} · ${a.release_date || ''}`} />
                </ListItem>
              ))}
            </List>
          </Box>
        )}
      </Paper>

      <Paper sx={{ p: 2 }}>
        <Typography variant="subtitle1" gutterBottom>Your playlists</Typography>
        {connected === false && (
          <Alert severity="info"
            action={
              <Button size="small" color="inherit" onClick={async () => {
                try {
                  const { url } = await api.spotifyAuthUrl()
                  window.location.href = url
                } catch (e) { setError(e.message) }
              }}>
                Connect Spotify
              </Button>
            }>
            Spotify account not connected — public links still work above.
          </Alert>
        )}
        {connected && playlists.length === 0 && (
          <Typography color="text.secondary">No playlists found.</Typography>
        )}
        <List dense>
          {playlists.map((p) => (
            <ListItem key={p.id}
              secondaryAction={
                <Button size="small" onClick={() => queue(p.url)}>Queue</Button>
              }>
              <ListItemText primary={p.name}
                secondary={p.tracks > 0 ? `${p.tracks} tracks` : null} />
            </ListItem>
          ))}
        </List>
      </Paper>
    </Box>
  )
}
