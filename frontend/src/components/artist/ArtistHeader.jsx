import { useState } from 'react'
import {
  Alert, Avatar, Box, Button, Chip, IconButton, LinearProgress, Link, Paper, Stack,
  Tooltip, Typography,
} from '@mui/material'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import RefreshIcon from '@mui/icons-material/Refresh'

import { ownedFully } from './shared.js'

function since(epochSeconds) {
  const minutes = Math.round((Date.now() / 1000 - epochSeconds) / 60)
  if (minutes < 2) return 'just now'
  if (minutes < 90) return `${minutes} min ago`
  return `${Math.round(minutes / 60)} h ago`
}

/** Why background loading is stalled, in words — "0 of 62" alone looks broken. */
function PrefetchStatus({ prefetch, pacer }) {
  if (!prefetch?.total || (!prefetch.running && !prefetch.failed)) return null
  const pct = Math.round(100 * (prefetch.done + prefetch.failed) / prefetch.total)
  let note = 'spaced a few seconds apart so YouTube does not mind — expand any release to load it now'
  if (prefetch.waiting === 'backoff') {
    note = `paused: YouTube asked us to slow down — resuming in ${Math.ceil((pacer?.backoff_sec || 60) / 60)} min`
  } else if (prefetch.waiting === 'budget') {
    note = "paused: this hour's browsing budget is used up"
  }
  return (
    <Box sx={{ mt: 1.5 }}>
      {prefetch.running && (
        <LinearProgress variant="determinate" value={pct}
          color={prefetch.waiting ? 'warning' : 'primary'}
          sx={{ height: 4, borderRadius: 2, mb: 0.5 }} />
      )}
      <Typography variant="caption" color={prefetch.waiting ? 'warning.main' : 'text.secondary'}>
        {prefetch.running
          ? `Loading tracklists: ${prefetch.done} of ${prefetch.total} — ${note}`
          : `${prefetch.failed} tracklist${prefetch.failed === 1 ? '' : 's'} would not load; `
            + 'expand the release to try again, or Refresh.'}
      </Typography>
    </Box>
  )
}

export default function ArtistHeader({ view, loading, onRefresh, onOpenArtist }) {
  const { artist, releases, prefetch, pacer } = view
  const [more, setMore] = useState(false)
  const complete = releases.filter(ownedFully).length
  const partly = releases.filter((r) => r.library?.have > 0 && !ownedFully(r)).length
  const loaded = releases.filter((r) => r.tracks).length

  const facts = [
    artist.subscribers && `${artist.subscribers} subscribers`,
    artist.monthly_listeners && `${artist.monthly_listeners} monthly audience`,
    `${releases.length} releases`,
  ].filter(Boolean)

  const title = (
    <Box sx={{ minWidth: 0, flexGrow: 1 }}>
      <Typography variant="h5" sx={{ fontWeight: 600, lineHeight: 1.2 }} noWrap>
        {artist.name}
      </Typography>
      <Typography variant="body2" sx={{ opacity: 0.85 }}>{facts.join(' · ')}</Typography>
    </Box>
  )
  const actions = (
    <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0 }}>
      <Tooltip title="Open in YouTube Music">
        <IconButton size="small" color="inherit" component="a" target="_blank"
          rel="noreferrer" href={`https://music.youtube.com/channel/${artist.channel_id}`}>
          <OpenInNewIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title={`Re-read the discography from YouTube Music (fetched ${since(view.fetched_at)}; kept for 12 hours)`}>
        <span>
          <IconButton size="small" color="inherit" disabled={loading} onClick={onRefresh}>
            <RefreshIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
    </Stack>
  )

  return (
    <Paper sx={{ mb: 2, overflow: 'hidden' }}>
      {artist.thumbnail && artist.thumbnail_wide ? (
        <Box sx={{
          position: 'relative', minHeight: { xs: 140, md: 190 }, display: 'flex', alignItems: 'flex-end',
          backgroundImage: `linear-gradient(to top, rgba(0,0,0,.82), rgba(0,0,0,.1) 70%), url("${artist.thumbnail}")`,
          // Banners are cut for YouTube Music's 2.4:1 header; this strip is
          // much wider, so aim at the band where faces usually are.
          backgroundSize: 'cover', backgroundPosition: 'center 42%', color: '#fff', p: 2, gap: 1,
        }}>
          {title}{actions}
        </Box>
      ) : (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, p: 2, pb: 0 }}>
          <Avatar src={artist.thumbnail} alt="" sx={{ width: 72, height: 72 }}
            imgProps={{ referrerPolicy: 'no-referrer' }} />
          {title}{actions}
        </Box>
      )}

      <Box sx={{ p: 2, pt: 1.5 }}>
        <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.75 }}>
          {loaded > 0 && (
            <Chip size="small" color="success" variant={complete ? 'filled' : 'outlined'}
              label={`${complete} complete in your library`} />
          )}
          {partly > 0 && (
            <Tooltip title="Some tracks are already in the library. Expand a release to see which.">
              <Chip size="small" color="warning" variant="outlined"
                label={`${partly} partly in your library`} />
            </Tooltip>
          )}
        </Stack>

        {artist.description && (
          <Typography variant="body2" color="text.secondary" sx={{
            mt: 1, whiteSpace: 'pre-line',
            ...(!more && { display: '-webkit-box', WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical', overflow: 'hidden' }),
          }}>
            {artist.description}
          </Typography>
        )}
        {artist.description?.length > 180 && (
          <Link component="button" variant="caption" onClick={() => setMore((m) => !m)}>
            {more ? 'Less' : 'More'}
          </Link>
        )}

        {view.partial && (
          <Alert severity="warning" sx={{ mt: 1.5 }}
            action={<Button size="small" color="inherit" onClick={onRefresh}>Retry</Button>}>
            YouTube Music only returned the first page of this artist's releases, so
            some may be missing.
          </Alert>
        )}

        <PrefetchStatus prefetch={prefetch} pacer={pacer} />

        {artist.related?.length > 0 && (
          <Stack direction="row" sx={{ mt: 1.5, flexWrap: 'wrap', gap: 0.75, alignItems: 'center' }}>
            <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
              Fans also like
            </Typography>
            {artist.related.map((a) => (
              <Chip key={a.channel_id} size="small" variant="outlined" label={a.name}
                avatar={<Avatar src={a.thumbnail} alt=""
                  imgProps={{ referrerPolicy: 'no-referrer', loading: 'lazy' }} />}
                onClick={() => onOpenArtist(a.channel_id)} />
            ))}
          </Stack>
        )}
      </Box>
    </Paper>
  )
}
