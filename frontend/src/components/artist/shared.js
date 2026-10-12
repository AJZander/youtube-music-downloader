/** Shared helpers for the artist catalogue browser. */

/** Release categories, in the order the browser lists them. */
export const CATEGORIES = [
  { key: 'album', label: 'Albums', one: 'Album' },
  { key: 'ep', label: 'EPs', one: 'EP' },
  { key: 'single', label: 'Singles', one: 'Single' },
  { key: 'live', label: 'Live albums', one: 'Live album' },
  { key: 'compilation', label: 'Compilations & box sets', one: 'Compilation' },
  { key: 'soundtrack', label: 'Soundtracks', one: 'Soundtrack' },
]
const CATEGORY = Object.fromEntries(CATEGORIES.map((c) => [c.key, c]))

export const categoryLabel = (key) => CATEGORY[key]?.one || key

export const isSingleFamily = (r) => r.category === 'ep' || r.category === 'single'

/** A reference the server can open directly, rather than a name to search. */
export const looksLikeArtistRef = (v) =>
  /^https?:\/\//i.test(v) || v.startsWith('@') || /^UC[\w-]{22}$/.test(v)

export function trackTime(seconds) {
  if (!seconds) return ''
  const s = Math.round(seconds)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function albumLength(seconds) {
  if (!seconds) return null
  const m = Math.round(seconds / 60)
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`
}

/** Nothing left to download: every playable track is already in the library. */
export const ownedFully = (r) => !!r.library && r.library.have > 0 && r.library.new === 0

export const isOwned = (t) => t.library === 'have'

/** What "the whole release" means: every playable track, minus the owned
 *  ones unless re-downloading is on. An owned track can still be ticked by
 *  hand — that is how you take one anyway. */
export const inWholeRelease = (t, force) => t.available && (force || !isOwned(t))

/** The tracks a selection entry will download — 'all', or the video ids
 *  ticked by hand. null while the release's tracklist has not loaded. */
export function selectedTracks(release, entry, force = false) {
  if (!entry || !release.tracks) return null
  if (entry === 'all') return release.tracks.filter((t) => inWholeRelease(t, force))
  const ids = new Set(entry)
  return release.tracks.filter((t) => t.available && ids.has(t.video_id))
}

/** Owned tracks a whole-release pick leaves out. */
export function ownedLeftOut(release, entry, force) {
  if (entry !== 'all' || force || !release.tracks) return 0
  return release.tracks.filter((t) => t.available && isOwned(t)).length
}

/** Every song on it is already in the library, on some album. */
const songsOwnedElsewhere = (r) => !!r.tracks?.some((t) => t.available)
  && r.tracks.every((t) => !t.available || t.library)

/** "Select new": every release with something not in the library yet,
 *  without the redundant ones — a single whose songs are all on an album
 *  being selected or already in the library, or an edition that a bigger
 *  edition being selected holds. Only a default: ticking any of them by hand
 *  downloads it whole, since duplicates only count on the same album.
 *  A studio album is never dropped for a compilation that happens to hold it:
 *  which of those to keep is a choice, not a redundancy. */
export function pickNew(releases, alreadySelected) {
  const candidates = releases.filter((r) => !r.queued && !ownedFully(r))
  const chosen = new Set([...alreadySelected, ...candidates.map((r) => r.browse_id)])
  return candidates.filter((r) => {
    if (isSingleFamily(r)) {
      return !songsOwnedElsewhere(r) && !r.contained_in.some((id) => chosen.has(id))
    }
    const versions = new Set(r.versions)
    return !r.contained_in.some((id) => versions.has(id) && chosen.has(id))
  }).map((r) => r.browse_id)
}

const yearOf = (r) => Number.parseInt(r.year, 10) || 0

export function sortReleases(releases, sort) {
  const indexed = releases.map((r, i) => [r, i])
  const by = {
    newest: ([a, i], [b, j]) => yearOf(b) - yearOf(a) || i - j,
    oldest: ([a, i], [b, j]) => yearOf(a) - yearOf(b) || i - j,
    title: ([a], [b]) => a.title.localeCompare(b.title, undefined, { sensitivity: 'base' }),
  }
  return indexed.sort(by[sort] || by.newest).map(([r]) => r)
}

/** How long the selection takes at the download caps — the honest answer to
 *  "should I queue the whole catalogue?" */
export function downloadEstimate(tracks, caps) {
  const perHour = caps?.per_hour
  const perDay = caps?.per_day
  if (!tracks || !perHour) return null
  if (perDay && tracks > perDay) {
    const days = Math.ceil(tracks / perDay)
    return `about ${days} days at ${perDay} tracks/day`
  }
  const hours = tracks / perHour
  if (hours < 1) return `about ${Math.max(1, Math.round(hours * 60))} min`
  return `about ${hours < 10 ? hours.toFixed(1) : Math.round(hours)} h at ${perHour} tracks/hour`
}

const RECENT_KEY = 'mdl-recent-artists'

/** Recently opened artists — a per-browser convenience, so storage failing
 *  (private window, blocked site data) just means no list. */
export function loadRecent() {
  try {
    const items = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]')
    return Array.isArray(items) ? items : []
  } catch {
    return []
  }
}

export function saveRecent(items) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(items.slice(0, 10))) } catch { /* ignore */ }
}
