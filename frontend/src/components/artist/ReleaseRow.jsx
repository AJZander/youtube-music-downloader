import { memo } from 'react'
import {
  Box, Checkbox, Chip, CircularProgress, Collapse, Divider, IconButton, Link, Paper,
  Tooltip, Typography,
} from '@mui/material'
import AlbumIcon from '@mui/icons-material/Album'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import CloseIcon from '@mui/icons-material/Close'
import LibraryMusicOutlinedIcon from '@mui/icons-material/LibraryMusicOutlined'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'

import {
  albumLength, categoryLabel, isOwned, isSingleFamily, ownedFully, selectedTracks,
  trackTime,
} from './shared.js'

/** Where the owned tracks live — "Paul Kelly/Seventy (10), Paul Kelly/Time (2)". */
function folderList(library) {
  return (library.folders || []).map((f) => `${f.folder} (${f.tracks})`).join(', ')
}

function LibraryChip({ library }) {
  if (!library?.total) return null
  const where = folderList(library)
  const others = library.elsewhere
    ? ` ${library.elsewhere} more ${library.elsewhere === 1 ? 'is' : 'are'} in your library `
      + 'on other albums — those still download, so this album is complete.'
    : ''
  if (library.have && !library.new) {
    return (
      <Tooltip title={`Every track is already in your library${where ? `: ${where}` : ''}. `
        + 'Nothing to download.'}>
        <Chip size="small" color="success" label="In library" />
      </Tooltip>
    )
  }
  if (library.have) {
    return (
      <Tooltip title={`${library.have} already in your library${where ? ` (${where})` : ''} `
        + `will be left out; ${library.new} will download.${others}`}>
        <Chip size="small" color="warning" variant="outlined"
          label={`${library.have} of ${library.total} in library`} />
      </Tooltip>
    )
  }
  if (library.elsewhere) {
    return (
      <Tooltip title={others.trim()}>
        <Chip size="small" variant="outlined"
          label={`${library.elsewhere} on other albums`} />
      </Tooltip>
    )
  }
  return null
}

/** One line under the title explaining how this release overlaps others. */
function overlapHint(release, titles) {
  if (release.queued) return 'Already in the download queue'
  const names = (ids) => ids.map((id) => titles[id]).filter(Boolean)
  const holders = names(release.contained_in)
  if (holders.length) {
    return isSingleFamily(release)
      ? `Also on ${holders.slice(0, 2).join(', ')}${holders.length > 2 ? ` +${holders.length - 2}` : ''}`
      : `Every track is also on ${holders[0]}`
  }
  const versions = names(release.versions)
  if (versions.length) return `Other edition${versions.length > 1 ? 's' : ''}: ${versions.join(', ')}`
  return null
}

/** "(your copy is 4:28, this is 4:06)" — only when the lengths really differ. */
function lengths(track) {
  const ours = track.library_duration
  if (!ours || !track.duration_sec || Math.abs(ours - track.duration_sec) <= 5) return ''
  return ` (your copy is ${trackTime(ours)}, this is ${trackTime(track.duration_sec)})`
}

function TrackStatus({ track, ticked }) {
  if (!track.available) {
    return <Typography variant="caption" color="text.disabled">unavailable</Typography>
  }
  if (isOwned(track)) {
    const upload = track.library_upload ? ' — an old YouTube upload, not an album copy' : ''
    const different = track.library_mismatch
      ? ' It looks like a different version, though.' : ''
    return (
      <Tooltip title={`In your library: ${track.library_path}${upload}${lengths(track)}.`
        + `${different} `
        + (ticked ? 'Ticked, so it downloads anyway.' : 'Left out — tick it to download it anyway.')}>
        <CheckCircleIcon sx={{ fontSize: 16,
          color: track.library_mismatch ? 'warning.main' : 'success.main' }} />
      </Tooltip>
    )
  }
  if (track.library === 'elsewhere') {
    const different = track.library_mismatch ? ', and looks like a different version' : ''
    return (
      <Tooltip title={`Also in your library on another album: ${track.library_path}`
        + `${lengths(track)}${different}. This album's copy still downloads.`}>
        <LibraryMusicOutlinedIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
      </Tooltip>
    )
  }
  return null
}

function Tracklist({ release, entry, force, query, onToggleTrack }) {
  const chosen = new Set((selectedTracks(release, entry, force) || []).map((t) => t.video_id))
  const credited = (release.artists || []).join(', ')
  const needle = query.trim().toLowerCase()
  return (
    <Box sx={{ pl: { xs: 0, sm: 8.5 }, pr: 1, pb: 1 }}>
      {release.tracks.map((t, i) => {
        const artists = t.artists.join(', ')
        const hit = needle && t.title.toLowerCase().includes(needle)
        return (
          <Box key={`${t.video_id}-${i}`} sx={{
            display: 'flex', alignItems: 'center', gap: 1, minHeight: 30,
            opacity: t.available ? 1 : 0.5,
            borderRadius: 1, '&:hover': { bgcolor: 'action.hover' },
          }}>
            <Checkbox size="small" sx={{ p: 0.5 }} disabled={!t.available}
              checked={chosen.has(t.video_id)}
              onChange={() => onToggleTrack(release, t.video_id)}
              inputProps={{ 'aria-label': `Select ${t.title}` }} />
            <Typography variant="caption" color="text.secondary"
              sx={{ width: 22, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
              {t.track_number}
            </Typography>
            <Box sx={{ flexGrow: 1, minWidth: 0 }}>
              <Typography variant="body2" noWrap
                sx={{ fontWeight: hit ? 600 : undefined, color: hit ? 'primary.main' : undefined }}>
                {t.title}
                {t.explicit && (
                  <Typography component="span" variant="caption" color="text.secondary"
                    sx={{ ml: 0.75, px: 0.5, border: 1, borderRadius: 0.5, fontSize: 10 }}>
                    E
                  </Typography>
                )}
              </Typography>
              {artists && artists !== credited && (
                <Typography variant="caption" color="text.secondary" noWrap component="div">
                  {artists}
                </Typography>
              )}
            </Box>
            <Box sx={{ width: 20, display: 'flex', justifyContent: 'center' }}>
              <TrackStatus track={t} ticked={chosen.has(t.video_id)} />
            </Box>
            <Typography variant="caption" color="text.secondary"
              sx={{ width: 40, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
              {trackTime(t.duration_sec)}
            </Typography>
          </Box>
        )
      })}
    </Box>
  )
}

function ReleaseRow({
  release, entry, expanded, fetching, titles, query, force,
  onToggle, onToggleTrack, onExpand, onOpenArtist, onDismiss,
}) {
  const r = release
  const chosen = selectedTracks(r, entry, force)
  const playable = r.tracks ? r.tracks.filter((t) => t.available).length : null
  const complete = !force && ownedFully(r)
  const hint = overlapHint(r, titles)
  const meta = [
    r.year,
    categoryLabel(r.category),
    // The artist browser prefetches tracklists; Discover (whose releases carry
    // a source) loads one only when the row is opened.
    r.tracks ? `${r.track_count} track${r.track_count === 1 ? '' : 's'}`
      : r.source ? null : 'loading tracklist…',
    albumLength(r.duration_sec),
    // Say how much of it downloads when that is not simply "all of it".
    entry && chosen && r.tracks && chosen.length < r.tracks.length
      && `${chosen.length} to download`,
  ].filter(Boolean).join(' · ')

  return (
    <Paper variant="outlined" sx={{
      mb: 0.75, overflow: 'hidden',
      borderColor: entry ? 'primary.main' : undefined,
      opacity: complete ? 0.6 : 1,
    }}>
      <Box onClick={() => onExpand(r)} sx={{
        display: 'flex', alignItems: 'center', gap: 1.25, p: 0.75, pr: 1,
        cursor: 'pointer', '&:hover': { bgcolor: 'action.hover' },
      }}>
        <Tooltip title={complete
          ? 'Already in your library — turn on "Re-download owned" to fetch it again' : ''}>
          <span onClick={(e) => e.stopPropagation()}>
            <Checkbox checked={!!entry && (!chosen || chosen.length > 0)}
              indeterminate={!!chosen && chosen.length > 0 && chosen.length < playable}
              disabled={complete && !entry} onChange={() => onToggle(r)}
              inputProps={{ 'aria-label': `Select ${r.title}` }} />
          </span>
        </Tooltip>
        {r.thumbnail ? (
          <Box component="img" src={r.thumbnail} alt="" loading="lazy"
            referrerPolicy="no-referrer"
            sx={{ width: 52, height: 52, borderRadius: 1, objectFit: 'cover', flexShrink: 0,
              bgcolor: 'action.hover' }} />
        ) : (
          <Box sx={{ width: 52, height: 52, borderRadius: 1, bgcolor: 'action.hover', flexShrink: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <AlbumIcon color="disabled" />
          </Box>
        )}
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="body2" sx={{ fontWeight: 500 }} noWrap>
            {r.title}
            {r.explicit && (
              <Typography component="span" variant="caption" color="text.secondary"
                sx={{ ml: 0.75, px: 0.5, border: 1, borderRadius: 0.5, fontSize: 10 }}>
                E
              </Typography>
            )}
          </Typography>
          <Typography variant="caption" color="text.secondary" noWrap component="div">
            {/* Lists mixing artists (Discover) say whose release it is. */}
            {onOpenArtist && r.artist_name && (
              <>
                <Link component="button" variant="caption" color="inherit" underline="hover"
                  sx={{ fontWeight: 600, verticalAlign: 'baseline' }}
                  onClick={(e) => { e.stopPropagation(); onOpenArtist(r.channel_id) }}>
                  {r.artist_name}
                </Link>
                {' · '}
              </>
            )}
            {meta}
          </Typography>
          {hint && (
            <Typography variant="caption" noWrap component="div"
              color={r.queued ? 'info.main' : 'text.secondary'} sx={{ fontStyle: 'italic' }}>
              {hint}
            </Typography>
          )}
        </Box>
        <Box sx={{ display: { xs: 'none', sm: 'flex' }, gap: 0.5 }}>
          {r.is_new && (
            <Tooltip title="Not on the artist's page when it was last checked">
              <Chip size="small" color="secondary" label="New" />
            </Tooltip>
          )}
          {r.queued && <Chip size="small" color="info" variant="outlined" label="Queued" />}
          {!r.library && r.in_library && (
            <Tooltip title="An album by this name is in your library">
              <Chip size="small" color="success" variant="outlined" label="In library" />
            </Tooltip>
          )}
          {!r.library && !r.in_library && r.downloaded && (
            <Tooltip title="Downloaded here before">
              <Chip size="small" color="success" variant="outlined" label="Downloaded" />
            </Tooltip>
          )}
          <LibraryChip library={r.library} />
        </Box>
        {onDismiss && (
          <Tooltip title="Not interested — hide it">
            <IconButton size="small" onClick={(e) => { e.stopPropagation(); onDismiss(r) }}>
              <CloseIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </Tooltip>
        )}
        <Tooltip title="Open in YouTube Music">
          <IconButton size="small" component="a" href={r.url} target="_blank" rel="noreferrer"
            onClick={(e) => e.stopPropagation()}>
            <OpenInNewIcon sx={{ fontSize: 16 }} />
          </IconButton>
        </Tooltip>
        {fetching
          ? <CircularProgress size={18} sx={{ m: 0.5 }} />
          : <ExpandMoreIcon sx={{ transition: 'transform .15s', color: 'text.secondary',
              transform: expanded ? 'rotate(180deg)' : 'none' }} />}
      </Box>
      <Collapse in={expanded && !!r.tracks} unmountOnExit>
        <Divider sx={{ mb: 0.5 }} />
        {r.tracks && (
          <Tracklist release={r} entry={entry} force={force} query={query}
            onToggleTrack={onToggleTrack} />
        )}
      </Collapse>
    </Paper>
  )
}

// Ticking one release should not redraw the other two hundred. Callers keep
// the callbacks and `titles` stable for this to hold.
export default memo(ReleaseRow)
