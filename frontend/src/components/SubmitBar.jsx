import { useState } from 'react'
import {
  Alert, Box, Button, Checkbox, CircularProgress, FormControlLabel, List,
  ListItem, ListItemText, Paper, TextField,
} from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'

import { api } from '../api.js'

const isUrl = (s) => /^https?:\/\//i.test(s.trim())

export default function SubmitBar({ onQueued }) {
  const [input, setInput] = useState('')
  const [force, setForce] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [results, setResults] = useState([])

  const submit = async () => {
    const value = input.trim()
    if (!value) return
    setBusy(true); setError(null); setNotice(null); setResults([])
    try {
      if (isUrl(value)) {
        // multiple URLs (one per line) supported
        const urls = value.split('\n').map((u) => u.trim()).filter(Boolean)
        for (const url of urls) {
          const job = await api.createJob(url, force)
          setNotice(`Queued ${job.provider} ${job.source_type}`)
        }
        setInput('')
        onQueued?.()
      } else {
        const found = await api.search(value)
        setResults(found)
        if (!found.length) setNotice('No results')
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const queueResult = async (r) => {
    setBusy(true); setError(null)
    try {
      await api.createJob(r.url, force)
      setNotice(`Queued: ${r.artists} — ${r.title}`)
      setResults((prev) => prev.filter((x) => x.video_id !== r.video_id))
      onQueued?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Paper sx={{ p: 2, mb: 2 }}>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
        <TextField
          fullWidth multiline maxRows={4} size="small"
          label="Paste a YouTube / YouTube Music / Spotify link (song, album, artist, playlist) — or type to search"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
          }}
        />
        <Button variant="contained" onClick={submit} disabled={busy}
          startIcon={busy ? <CircularProgress size={16} /> : <DownloadIcon />}>
          {isUrl(input) ? 'Queue' : 'Search'}
        </Button>
      </Box>
      <FormControlLabel sx={{ mt: 0.5 }}
        control={<Checkbox size="small" checked={force}
          onChange={(e) => setForce(e.target.checked)} />}
        label="Force (re-download even if already in library)" />
      {error && <Alert severity="error" sx={{ mt: 1 }} onClose={() => setError(null)}>{error}</Alert>}
      {notice && <Alert severity="success" sx={{ mt: 1 }} onClose={() => setNotice(null)}>{notice}</Alert>}
      {results.length > 0 && (
        <List dense sx={{ mt: 1 }}>
          {results.map((r) => (
            <ListItem key={r.video_id}
              secondaryAction={
                <Button size="small" onClick={() => queueResult(r)}>Queue</Button>
              }>
              <ListItemText
                primary={`${r.artists} — ${r.title}`}
                secondary={[r.album, r.duration].filter(Boolean).join(' · ')} />
            </ListItem>
          ))}
        </List>
      )}
    </Paper>
  )
}
