const json = async (res) => {
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* ignore */ }
    throw new Error(detail)
  }
  return res.json()
}

export const api = {
  createJob: (url, force = false) =>
    fetch('/api/v1/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, force }),
    }).then(json),
  listJobs: (params = {}) =>
    fetch('/api/v1/jobs?' + new URLSearchParams(params)).then(json),
  getJob: (id) => fetch(`/api/v1/jobs/${id}`).then(json),
  cancelJob: (id) => fetch(`/api/v1/jobs/${id}/cancel`, { method: 'POST' }).then(json),
  retryJob: (id) => fetch(`/api/v1/jobs/${id}/retry`, { method: 'POST' }).then(json),
  deleteJob: (id) => fetch(`/api/v1/jobs/${id}`, { method: 'DELETE' }).then(json),

  // --- queue ordering
  queue: () => fetch('/api/v1/queue').then(json),
  moveJob: (id, action) =>
    fetch(`/api/v1/jobs/${id}/move`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    }).then(json),
  reorderQueue: (jobIds) =>
    fetch('/api/v1/queue/reorder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_ids: jobIds }),
    }).then(json),
  pauseJob: (id) => fetch(`/api/v1/jobs/${id}/pause`, { method: 'POST' }).then(json),
  resumeJob: (id) => fetch(`/api/v1/jobs/${id}/resume`, { method: 'POST' }).then(json),

  needsReview: () => fetch('/api/v1/tracks?status=needs_review').then(json),
  skipped: (params = {}) =>
    fetch('/api/v1/tracks/skipped?' + new URLSearchParams(params)).then(json),
  acknowledgeSkipped: (trackIds = null) =>
    fetch('/api/v1/tracks/skipped/acknowledge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: trackIds }),
    }).then(json),
  skippedRecheckPreview: () => fetch('/api/v1/tracks/skipped/recheck').then(json),
  skippedRecheck: () =>
    fetch('/api/v1/tracks/skipped/recheck', { method: 'POST' }).then(json),
  resolveTrack: (id, action) =>
    fetch(`/api/v1/tracks/${id}/resolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    }).then(json),
  search: (q) => fetch('/api/v1/search?' + new URLSearchParams({ q })).then(json),
  spotifyResolve: (url) =>
    fetch('/api/v1/spotify/resolve?' + new URLSearchParams({ url })).then(json),
  spotifyPlaylists: () => fetch('/api/v1/spotify/playlists').then(json),
  spotifyAuthUrl: () => fetch('/api/v1/spotify/auth-url').then(json),

  // --- artist catalogue (YouTube Music)
  catalogSearch: (q) =>
    fetch('/api/v1/catalog/search?' + new URLSearchParams({ q })).then(json),
  catalogArtist: (ref, refresh = false) =>
    fetch('/api/v1/catalog/artist?' + new URLSearchParams({ ref, refresh })).then(json),
  catalogAlbum: (browseId) =>
    fetch(`/api/v1/catalog/album/${encodeURIComponent(browseId)}`).then(json),
  catalogQueue: (releases, force = false) =>
    fetch('/api/v1/catalog/queue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ releases, force }),
    }).then(json),

  // --- discover
  discoverStatus: () => fetch('/api/v1/discover/status').then(json),
  discoverScan: () => fetch('/api/v1/discover/scan', { method: 'POST' }).then(json),
  discoverReleases: (params = {}) =>
    fetch('/api/v1/discover/releases?' + new URLSearchParams(params)).then(json),
  discoverDismissRelease: (browseId) =>
    fetch(`/api/v1/discover/releases/${encodeURIComponent(browseId)}/dismiss`,
      { method: 'POST' }).then(json),
  discoverArtists: (limit = 40) =>
    fetch('/api/v1/discover/artists?' + new URLSearchParams({ limit })).then(json),
  discoverDismissArtist: (channelId, name = '') =>
    fetch(`/api/v1/discover/artists/${encodeURIComponent(channelId)}/dismiss?`
      + new URLSearchParams({ name }), { method: 'POST' }).then(json),
  discoverCharts: (country) =>
    fetch('/api/v1/discover/charts?' + new URLSearchParams(country ? { country } : {}))
      .then(json),
  discoverGenres: () => fetch('/api/v1/discover/genres').then(json),
  discoverGenre: (title) =>
    fetch(`/api/v1/discover/genres/${encodeURIComponent(title)}`).then(json),
  discoverQueueSongs: (songs, force = false) =>
    fetch('/api/v1/discover/queue-songs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ songs, force }),
    }).then(json),

  // --- library management
  libraryOverview: () => fetch('/api/v1/library/overview').then(json),
  libraryScan: (full = false) =>
    fetch('/api/v1/library/scan?' + new URLSearchParams({ full }), { method: 'POST' }).then(json),
  libraryScanStatus: () => fetch('/api/v1/library/scan').then(json),
  libraryDuplicates: (kind, params = {}) =>
    fetch(`/api/v1/library/duplicates/${kind}?` + new URLSearchParams(params)).then(json),
  libraryAlbumPlan: (dirpaths, keepers) =>
    fetch('/api/v1/library/duplicates/albums/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dirpaths, keepers }),
    }).then(json),
  libraryNotDuplicates: (scope, keys, reason = '') =>
    fetch('/api/v1/library/exclusions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope, keys, reason }),
    }).then(json),
  libraryExclusions: () => fetch('/api/v1/library/exclusions').then(json),
  libraryForgetExclusions: (ids) =>
    fetch('/api/v1/library/exclusions', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    }).then(json),
  libraryArtists: (params = {}) =>
    fetch('/api/v1/library/artists?' + new URLSearchParams(params)).then(json),
  libraryAlbums: (artistDir) =>
    fetch('/api/v1/library/albums?' + new URLSearchParams({ artist_dir: artistDir })).then(json),
  libraryAlbumTracks: (dirpath) =>
    fetch('/api/v1/library/tracks?' + new URLSearchParams({ dirpath })).then(json),
  libraryTrash: (paths, reason = '', keepTogether = null, scope = 'album') =>
    fetch('/api/v1/library/trash', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, reason, keep_together: keepTogether, scope }),
    }).then(json),
  libraryTrashList: () => fetch('/api/v1/library/trash').then(json),
  libraryTrashRestore: (batchId) =>
    fetch(`/api/v1/library/trash/${batchId}/restore`, { method: 'POST' }).then(json),
  libraryTrashPurge: (body) =>
    fetch('/api/v1/library/trash/purge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(json),
  libraryMergeArtists: (sources, target, rewriteTags = true) =>
    fetch('/api/v1/library/artists/merge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sources, target, rewrite_tags: rewriteTags }),
    }).then(json),
  plexEmptyTrash: () =>
    fetch('/api/v1/library/plex/empty-trash', { method: 'POST' }).then(json),

  health: () => fetch('/api/v1/health').then(json),
  stats: () => fetch('/api/v1/stats').then(json),
  governorResume: () => fetch('/api/v1/governor/resume', { method: 'POST' }).then(json),
}

// One EventSource for the whole page. Each subscriber used to open its own,
// and with the queue, the artist browser and Discover all mounted that was
// three or four held connections — over HTTP/1.1 a browser allows six per
// host, which left API calls queueing behind the streams.
let shared = null
let subscribers = 0

export function subscribeEvents(handlers) {
  if (!shared) shared = new EventSource('/api/v1/events')
  subscribers += 1
  const source = shared
  const listeners = Object.entries(handlers).map(([type, handler]) => {
    const listener = (e) => {
      try { handler(JSON.parse(e.data)) } catch { /* ignore malformed */ }
    }
    source.addEventListener(type, listener)
    return [type, listener]
  })
  return () => {
    for (const [type, listener] of listeners) source.removeEventListener(type, listener)
    subscribers -= 1
    if (!subscribers && shared === source) {
      shared.close()
      shared = null
    }
  }
}
