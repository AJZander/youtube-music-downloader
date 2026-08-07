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
  needsReview: () => fetch('/api/v1/tracks?status=needs_review').then(json),
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
  artistReleases: (url) =>
    fetch('/api/v1/artists/releases?' + new URLSearchParams({ url })).then(json),
  health: () => fetch('/api/v1/health').then(json),
  stats: () => fetch('/api/v1/stats').then(json),
  governorResume: () => fetch('/api/v1/governor/resume', { method: 'POST' }).then(json),
}

export function subscribeEvents(handlers) {
  const source = new EventSource('/api/v1/events')
  for (const [type, handler] of Object.entries(handlers)) {
    source.addEventListener(type, (e) => {
      try { handler(JSON.parse(e.data)) } catch { /* ignore malformed */ }
    })
  }
  return () => source.close()
}
