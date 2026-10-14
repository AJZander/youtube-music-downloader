import {
  Box, Button, CircularProgress, Paper, Typography,
} from '@mui/material'
import PlaylistAddIcon from '@mui/icons-material/PlaylistAdd'
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff'

import { downloadEstimate } from './shared.js'

/** Pinned above the stats bar (and the download details pane, when open)
 *  while anything is selected, so queueing never needs a scroll back up.
 *  With onDismiss, the selection can be hidden instead — `selected` counts
 *  every release picked, owned ones too, which have nothing to queue. */
export default function SelectionBar({
  summary, caps, force, busy, onClear, onQueue, bottomOffset, selected = 0, onDismiss,
}) {
  const dismissable = !!onDismiss && selected > 0
  if (!summary.releases && !dismissable) return null
  const toFetch = summary.tracks
  const estimate = downloadEstimate(toFetch, caps)
  const parts = summary.releases ? [
    `${summary.releases} release${summary.releases === 1 ? '' : 's'}`,
    `${summary.tracks} track${summary.tracks === 1 ? '' : 's'} to download`
      + (summary.reowned > 0 ? ` (${summary.reowned} of them already in your library)` : ''),
    summary.owned > 0 && `${summary.owned} already in your library left out`,
  ].filter(Boolean) : [`${selected} release${selected === 1 ? '' : 's'} selected`]

  return (
    <Paper elevation={8} sx={{
      position: 'sticky', bottom: bottomOffset + 8, zIndex: (t) => t.zIndex.appBar - 2,
      mt: 2, px: 2, py: 1.25, display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap',
      border: 1, borderColor: 'primary.main',
    }}>
      <Box sx={{ flexGrow: 1, minWidth: 220 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>{parts.join(' · ')}</Typography>
        <Typography variant="caption" color="text.secondary" component="div">
          {[
            summary.pending > 0 && `${summary.pending} still loading their tracklist — `
              + 'owned tracks are worked out when you queue',
            toFetch > 0 && estimate,
            !toFetch && !summary.pending && 'Everything selected is already in your library',
          ].filter(Boolean).join(' · ')}
        </Typography>
      </Box>
      <Button onClick={onClear} disabled={busy}>Clear</Button>
      {dismissable && (
        <Button variant="outlined" color="inherit" onClick={onDismiss} disabled={busy}
          startIcon={<VisibilityOffIcon />}>
          Not interested{selected > 1 && ` (${selected})`}
        </Button>
      )}
      <Button variant="contained" onClick={onQueue}
        disabled={busy || (!toFetch && !summary.pending)}
        startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <PlaylistAddIcon />}>
        Queue {summary.releases > 1 ? `${summary.releases} releases` : 'release'}
      </Button>
    </Paper>
  )
}
