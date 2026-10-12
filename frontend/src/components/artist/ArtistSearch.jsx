import { useState } from 'react'
import {
  Alert, Avatar, Box, Button, ButtonBase, Chip, CircularProgress, InputAdornment,
  Paper, Stack, TextField, Typography,
} from '@mui/material'
import PersonSearchIcon from '@mui/icons-material/PersonSearch'

import { api } from '../../api.js'
import { looksLikeArtistRef } from './shared.js'

function ArtistResult({ artist, onOpen }) {
  return (
    <ButtonBase onClick={() => onOpen(artist.channel_id)}
      sx={{ borderRadius: 2, textAlign: 'left' }}>
      <Paper variant="outlined" sx={{
        p: 1, pr: 2, display: 'flex', alignItems: 'center', gap: 1.25, width: 230,
        '&:hover': { borderColor: 'primary.main', bgcolor: 'action.hover' },
      }}>
        <Avatar src={artist.thumbnail} alt="" sx={{ width: 44, height: 44 }}
          imgProps={{ referrerPolicy: 'no-referrer', loading: 'lazy' }} />
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="body2" noWrap>{artist.name}</Typography>
          <Typography variant="caption" color="text.secondary" noWrap component="div">
            {artist.audience || 'Artist'}
          </Typography>
        </Box>
      </Paper>
    </ButtonBase>
  )
}

/** One box for both ways in: a link opens the artist, anything else searches. */
export default function ArtistSearch({ onOpen, busy, recent, onForget }) {
  const [text, setText] = useState('')
  const [results, setResults] = useState(null)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState(null)

  const open = (ref) => { setResults(null); onOpen(ref) }

  const submit = async () => {
    const value = text.trim()
    if (!value) return
    setError(null)
    if (looksLikeArtistRef(value)) { open(value); return }
    setSearching(true)
    try {
      setResults((await api.catalogSearch(value)).artists)
    } catch (e) {
      setError(e.message)
    } finally {
      setSearching(false)
    }
  }

  return (
    <Paper sx={{ p: 2, mb: 2 }}>
      <Box sx={{ display: 'flex', gap: 1 }}>
        <TextField fullWidth size="small" value={text}
          label="Artist name, or a YouTube Music / YouTube artist link"
          placeholder="Paul Kelly   ·   music.youtube.com/@paulkelly"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          InputProps={{ startAdornment: (
            <InputAdornment position="start"><PersonSearchIcon fontSize="small" /></InputAdornment>
          ) }} />
        <Button variant="contained" onClick={submit} disabled={busy || searching || !text.trim()}
          sx={{ minWidth: 88 }}>
          {searching || busy ? <CircularProgress size={18} color="inherit" /> : 'Find'}
        </Button>
      </Box>

      {error && <Alert severity="error" sx={{ mt: 1 }} onClose={() => setError(null)}>{error}</Alert>}

      {results && (
        <Box sx={{ mt: 1.5 }}>
          {results.length ? (
            <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 1 }}>
              {results.map((a) => <ArtistResult key={a.channel_id} artist={a} onOpen={open} />)}
            </Stack>
          ) : (
            <Typography variant="body2" color="text.secondary">
              No artists by that name on YouTube Music.
            </Typography>
          )}
        </Box>
      )}

      {recent.length > 0 && (
        <Stack direction="row" sx={{ mt: 1.5, flexWrap: 'wrap', gap: 0.75, alignItems: 'center' }}>
          <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>Recent</Typography>
          {recent.map((a) => (
            <Chip key={a.ref} size="small" variant="outlined" label={a.name}
              avatar={<Avatar src={a.thumbnail} alt=""
                imgProps={{ referrerPolicy: 'no-referrer' }} />}
              onClick={() => open(a.ref)} onDelete={() => onForget(a.ref)} />
          ))}
        </Stack>
      )}
    </Paper>
  )
}
