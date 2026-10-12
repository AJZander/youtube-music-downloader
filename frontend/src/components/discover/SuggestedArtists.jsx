import { useEffect, useState } from 'react'
import {
  Alert, Avatar, Box, Button, Chip, Grid, LinearProgress, Paper, Typography,
} from '@mui/material'

import { api } from '../../api.js'

function because(artist) {
  const names = artist.because.join(', ')
  const more = artist.because_count - artist.because.length
  return `Because you have ${names}${more > 0 ? ` and ${more} more` : ''}`
}

function recent(artist) {
  if (artist.recent_releases == null) return { label: 'Not checked yet', color: 'default' }
  if (!artist.recent_releases) return { label: 'Nothing recent', color: 'default' }
  return {
    label: `${artist.recent_releases} recent release${artist.recent_releases === 1 ? '' : 's'}`,
    color: 'secondary',
  }
}

/** Artists your artists' pages list as related, that the library lacks. */
export default function SuggestedArtists({ status, onBrowseArtist }) {
  const [artists, setArtists] = useState(null)
  const [error, setError] = useState(null)
  // Re-rank as the scan reads more pages — but not on every single one.
  const progress = Math.floor((status?.checked || 0) / 20)

  useEffect(() => {
    api.discoverArtists(60).then((d) => setArtists(d.artists)).catch((e) => setError(e.message))
  }, [progress])

  const dismiss = async (a) => {
    try {
      await api.discoverDismissArtist(a.channel_id, a.name)
      setArtists((list) => list.filter((x) => x.channel_id !== a.channel_id))
    } catch (e) {
      setError(e.message)
    }
  }

  if (error) return <Alert severity="error">{error}</Alert>
  if (!artists) return <LinearProgress />
  if (!artists.length) {
    return (
      <Paper variant="outlined" sx={{ p: 3, textAlign: 'center', borderStyle: 'dashed' }}>
        <Typography variant="body2" color="text.secondary">
          No recommendations yet. They come from the "fans also like" lists on your artists'
          pages, so they appear as Discover reads them.
        </Typography>
      </Paper>
    )
  }

  return (
    <Grid container spacing={1.5}>
      {artists.map((a) => {
        const chip = recent(a)
        return (
          <Grid item xs={12} sm={6} md={4} key={a.channel_id}>
            <Paper variant="outlined" sx={{ p: 1.5, display: 'flex', gap: 1.5, height: '100%' }}>
              <Avatar src={a.thumbnail} alt="" sx={{ width: 56, height: 56 }}
                imgProps={{ referrerPolicy: 'no-referrer', loading: 'lazy' }} />
              <Box sx={{ minWidth: 0, flexGrow: 1, display: 'flex', flexDirection: 'column' }}>
                <Typography variant="body1" sx={{ fontWeight: 600 }} noWrap>{a.name}</Typography>
                <Typography variant="caption" color="text.secondary" sx={{
                  display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                  overflow: 'hidden' }}>
                  {because(a)}
                </Typography>
                <Box sx={{ mt: 0.75 }}>
                  <Chip size="small" variant="outlined" color={chip.color} label={chip.label} />
                </Box>
                <Box sx={{ mt: 'auto', pt: 1, display: 'flex', gap: 0.5, justifyContent: 'flex-end' }}>
                  <Button size="small" onClick={() => dismiss(a)}>Not interested</Button>
                  <Button size="small" variant="outlined" onClick={() => onBrowseArtist(a.channel_id)}>
                    Open
                  </Button>
                </Box>
              </Box>
            </Paper>
          </Grid>
        )
      })}
    </Grid>
  )
}
