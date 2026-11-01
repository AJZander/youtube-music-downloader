/** Links pasted into Discover's search box, and what each one is. */
const URL_RE = /^https?:\/\/\S+$/i
// A YouTube / YouTube Music artist: a channel or an @handle.
const ARTIST_RE = /^https?:\/\/(?:www\.|m\.|music\.)?youtube\.com\/(?:channel\/UC[\w-]+|@[\w.-]+)\/?(?:[?#].*)?$/i
const ALBUM_RE = /\/browse\/(MPREb_[\w-]+)/
const SPOTIFY_RE = /^https?:\/\/open\.spotify\.com\/(?:intl-\w+\/)?(track|album|playlist|artist)\//i

const KINDS = {
  artist: 'Artist',
  album: 'Album',
  song: 'Song',
  playlist: 'Playlist',
  spotify: 'Spotify',
  link: 'Link',
}

function classify(url) {
  if (ARTIST_RE.test(url)) return { kind: 'artist', ref: url }
  const album = ALBUM_RE.exec(url)
  if (album) return { kind: 'album', ref: url, release: album[1] }
  const spotify = SPOTIFY_RE.exec(url)
  if (spotify) return { kind: 'spotify', detail: spotify[1] }
  if (/[?&]list=OLAK5uy_/.test(url)) return { kind: 'album' }
  if (/[?&]list=/.test(url) && !/[?&]v=/.test(url)) return { kind: 'playlist' }
  if (/youtu\.be\/|[?&]v=/.test(url)) return { kind: 'song' }
  return { kind: 'link' }
}

/** The links in `text`, one per line or space-separated; [] if any word in
 *  it is not a link (then it is a search). */
export function findLinks(text) {
  const words = (text || '').split(/\s+/).filter(Boolean)
  if (!words.length || !words.every((w) => URL_RE.test(w))) return []
  return [...new Set(words)].map((url) => {
    const c = classify(url)
    return { url, ...c, label: c.kind === 'spotify' ? `Spotify ${c.detail}` : KINDS[c.kind] }
  })
}

/** Opened in Discover rather than queued: artists, and albums we can place. */
export const opensHere = (link) => link.kind === 'artist' || (link.kind === 'album' && link.ref)
