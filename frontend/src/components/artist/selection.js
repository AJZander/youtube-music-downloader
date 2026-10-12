/** Picking releases — whole, or track by track — and queueing them. Shared
 *  by the artist browser and Discover, which draw the same release rows. */
import { useCallback, useMemo, useState } from 'react'

import {
  inWholeRelease, isOwned, ownedFully, ownedLeftOut, selectedTracks,
} from './shared.js'

/**
 * Selection is `{browse_id: 'all' | [video_id, ...]}`. 'all' means every new
 * track — owned ones only when re-downloading (`force`) — and the server
 * works ownership out again at queue time, so a release whose tracklist has
 * not loaded is covered too. An owned track ticked by hand downloads anyway.
 */
export function useReleaseSelection(releases, force) {
  const [selection, setSelection] = useState({})

  const toggle = useCallback((r) => setSelection((s) => {
    const next = { ...s }
    if (next[r.browse_id]) delete next[r.browse_id]
    else next[r.browse_id] = 'all'
    return next
  }), [])

  const toggleTrack = useCallback((r, videoId) => setSelection((s) => {
    const whole = r.tracks.filter((t) => inWholeRelease(t, force)).map((t) => t.video_id)
    const current = new Set(s[r.browse_id] === 'all' ? whole : s[r.browse_id] || [])
    if (current.has(videoId)) current.delete(videoId)
    else current.add(videoId)
    const next = { ...s }
    const kept = r.tracks.filter((t) => t.available && current.has(t.video_id))
      .map((t) => t.video_id)
    if (!kept.length) delete next[r.browse_id]
    else if (kept.length === whole.length && whole.every((id) => current.has(id))) {
      next[r.browse_id] = 'all'
    } else next[r.browse_id] = kept
    return next
  }), [force])

  const selectMany = useCallback((ids, on) => setSelection((s) => {
    const next = { ...s }
    for (const id of ids) {
      if (on) next[id] = next[id] || 'all'
      else delete next[id]
    }
    return next
  }), [])

  // A whole release that is complete in the library has nothing to queue
  // (unless re-downloading) — but tracks ticked out of it by hand do.
  const queueable = useCallback((r, entry = 'all') =>
    force || entry !== 'all' || !ownedFully(r), [force])

  const summary = useMemo(() => {
    const s = { releases: 0, tracks: 0, owned: 0, reowned: 0, pending: 0 }
    for (const r of releases) {
      const entry = selection[r.browse_id]
      if (!entry || !queueable(r, entry)) continue
      s.releases += 1
      const chosen = selectedTracks(r, entry, force)
      if (!chosen) { s.pending += 1; continue }
      s.tracks += chosen.length
      s.owned += ownedLeftOut(r, entry, force)
      s.reowned += chosen.filter(isOwned).length
    }
    return s
  }, [releases, selection, force, queueable])

  /** The selected releases, in the order given, as queue picks. */
  const picks = useCallback((ordered, fallbackArtist) => ordered
    .filter((r) => selection[r.browse_id] && queueable(r, selection[r.browse_id]))
    .map((r) => ({
      browse_id: r.browse_id,
      video_ids: selection[r.browse_id] === 'all' ? null : selection[r.browse_id],
      title: r.title,
      artist: r.artists?.join(', ') || r.artist_name || fallbackArtist,
    })), [selection, queueable])

  /** Drop the releases a queue call settled, one way or the other. */
  const clearSettled = useCallback((result) => {
    const settled = new Set([...result.queued, ...result.skipped].map((q) => q.browse_id))
    setSelection((s) => Object.fromEntries(Object.entries(s).filter(([id]) => !settled.has(id))))
  }, [])

  return {
    selection, setSelection, toggle, toggleTrack, selectMany, queueable, summary, picks,
    clearSettled,
  }
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`

/** What a queue call did, in a sentence — and what it could not do. */
export function describeQueue(result, titles = {}) {
  const count = (reason) => result.skipped.filter((x) => x.reason === reason).length
  const tracks = result.queued.reduce((n, q) => n + (q.tracks || 0), 0)
  const forced = result.queued.reduce((n, q) => n + (q.forced || 0), 0)
  const complete = count('already in your library')
  const dupes = count('already in the queue')
  const videos = result.videos?.length || 0
  const notice = [
    result.queued.length > 0 && `Queued ${plural(result.queued.length, 'release', 'releases')}`
      + (tracks ? ` — ${plural(tracks, 'track', 'tracks')}.` : '.'),
    videos > 0 && `Queued ${plural(videos, 'song', 'songs')} that are not on an album here.`,
    result.left_out > 0 && `Left out ${plural(result.left_out, 'track', 'tracks')} `
      + 'already in your library.',
    forced > 0 && `Re-downloading ${plural(forced, 'owned track', 'owned tracks')} you ticked.`,
    complete && `${plural(complete, 'release was', 'releases were')} already complete.`,
    dupes && `${plural(dupes, 'was', 'were')} already in the queue.`,
  ].filter(Boolean).join(' ') || 'Nothing was queued.'
  const failed = result.skipped.filter((x) => ![
    'already in your library', 'already in the queue'].includes(x.reason))
  const error = failed.length
    ? `Not queued: ${failed.map((x) => `${titles[x.browse_id] || x.browse_id} (${x.reason})`)
      .join('; ')}`
    : null
  return { notice, error }
}
