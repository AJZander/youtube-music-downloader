/** What each issue kind is called, how loud it is, and what fixing it means. */
export const KINDS = {
  not_in_plex: { label: 'Not in Plex', severity: 'warning', applyable: true,
    help: 'Files Plex has not picked up. Applying scans the folder in Plex.' },
  plex_ghost: { label: 'Plex entry without a file', severity: 'info', applyable: true,
    help: 'Plex still lists tracks whose files are gone. Applying refreshes the folder and empties Plex\'s trash.' },
  unmatched: { label: 'Unmatched in Plex', severity: 'warning', applyable: true,
    help: 'Plex has no online match for the album. Applying matches it to the MusicBrainz release.' },
  wrong_match: { label: 'Wrong Plex match', severity: 'warning', applyable: true,
    help: 'Plex matched a different release than the files are. Applying re-matches it.' },
  tags: { label: 'Tags to fix', severity: 'info', applyable: true,
    help: 'File tags differ from MusicBrainz. Applying rewrites them (undoable).' },
  track_order: { label: 'Track order', severity: 'warning', applyable: true,
    help: 'Track or disc numbers differ from the release — in the files or in Plex. Applying renumbers them (undoable).' },
  missing_tracks: { label: 'Missing tracks', severity: 'warning', applyable: true,
    help: 'The release has tracks the library does not. Applying finds the album on YouTube Music and queues just those tracks for download.' },
  move: { label: 'Rename / move', severity: 'info', applyable: true,
    help: 'Files are not at Artist/(Year) Album/NN - Title. Applying moves them (undoable).' },
  move_conflict: { label: 'Move blocked', severity: 'warning', applyable: false,
    help: 'The destination already exists — usually another copy of the album. Resolve it by hand.' },
  genres: { label: 'Genres', severity: 'info', applyable: true,
    help: 'Plex shows different genres from ours. Applying sets and locks ours.' },
  moods: { label: 'Moods', severity: 'info', applyable: true,
    help: 'The album has no moods in Plex. Applying adds them.' },
  poster: { label: 'Missing cover art', severity: 'info', applyable: true,
    help: 'No poster in Plex. Applying uploads one from Cover Art Archive.' },
  review_match: { label: 'Check match', severity: 'warning', applyable: false,
    help: 'MusicBrainz found a likely release but not a certain one. Pick it on the Match tab.' },
  unresolved: { label: 'Not identified', severity: 'warning', applyable: false,
    help: 'No MusicBrainz release fits these files. Search on the Match tab, or add it to MusicBrainz.' },
  folder_artist: { label: 'Folder ≠ tag artist', severity: 'warning', applyable: false,
    help: 'The folder is filed under a different artist than the tags say.' },
  extra_tracks: { label: 'Tracks not on release', severity: 'info', applyable: false,
    help: 'Some files are not on the matched release (bonus tracks, another edition).' },
  dup_album: { label: 'Duplicate album', severity: 'warning', applyable: false,
    help: 'The same album exists in more than one folder.' },
  dup_track: { label: 'Duplicate track', severity: 'warning', applyable: false,
    help: 'The same track on the same album exists more than once.' },
  split_artist: { label: 'Split artist', severity: 'warning', applyable: false,
    help: 'One artist filed under several folder spellings.' },
  artist_unmatched: { label: 'Artist unmatched in Plex', severity: 'warning', applyable: true,
    help: 'Plex has no online match for the artist. Applying matches it to MusicBrainz.' },
  artist_genres: { label: 'Artist genres', severity: 'info', applyable: true,
    help: 'Plex shows different artist genres from ours. Applying sets and locks ours.' },
  artist_moods: { label: 'Artist moods', severity: 'info', applyable: true,
    help: 'The artist has no moods in Plex. Applying adds them.' },
  artist_poster: { label: 'Missing artist image', severity: 'info', applyable: true,
    help: 'No artist image in Plex. Applying uploads one.' },
  artist_unknown: { label: 'Artist not on MusicBrainz', severity: 'info', applyable: false,
    help: 'MusicBrainz does not know this artist. You can add them from the Match tab.' },
}

export const DUPE_KINDS = new Set(['dup_album', 'dup_track', 'split_artist'])

export function kindLabel(kind) {
  return KINDS[kind]?.label || (kind || '').replace(/_/g, ' ')
}

/** MUI palette colour for a severity. */
export function severityColor(severity) {
  return { error: 'error', warning: 'warning', info: 'info' }[severity] || 'default'
}

export const STATUS = {
  pending: { label: 'not checked', color: 'default' },
  resolved: { label: 'identified', color: 'success' },
  review: { label: 'check match', color: 'warning' },
  unresolved: { label: 'not identified', color: 'error' },
  ignored: { label: 'ignored', color: 'default' },
}

export const PHASES = {
  idle: 'Idle',
  scanning: 'Scanning the library',
  plex_sync: 'Reading Plex',
  duplicates: 'Finding duplicates',
  checking: 'Checking albums',
  applying: 'Applying fixes',
  waiting: 'Waiting',
}

/** "2h 5m" / "12 min" / "40s" */
export function eta(seconds) {
  if (seconds == null) return null
  if (seconds < 90) return `${Math.round(seconds)}s`
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`
}

export function asText(value) {
  if (value == null || value === '') return '—'
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function when(value) {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

/** #/library/<artist>/<album key> — both parts URL-encoded. */
export function parseHash(hash = window.location.hash) {
  const m = /^#\/library(?:\/([^/]*))?(?:\/([^/]*))?/.exec(hash || '')
  if (!m) return null
  const dec = (s) => { try { return s ? decodeURIComponent(s) : null } catch { return null } }
  return { artistDir: dec(m[1]), albumKey: dec(m[2]) }
}

export function buildHash(artistDir, albumKey) {
  let h = '#/library'
  if (artistDir) h += `/${encodeURIComponent(artistDir)}`
  if (artistDir && albumKey) h += `/${encodeURIComponent(albumKey)}`
  return h
}
