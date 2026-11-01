/** Where Discover is, in the address bar: #/discover/<view>/<parts>.
 *
 *   #/discover                         new releases
 *   #/discover/recommended             recommended artists
 *   #/discover/charts                  charts and genres
 *   #/discover/artist/<ref>[/<id>]     an artist, optionally opened at a release
 *   #/discover/search/<q>[/youtube]    search, with YouTube Music asked too
 *
 * Parts are URL-encoded, so a ref can be a channel id, an @handle or a link.
 */
export const DISCOVER_PREFIX = '#/discover'

const dec = (s) => { try { return s ? decodeURIComponent(s) : null } catch { return null } }

export function parseRoute(hash = window.location.hash) {
  if (!(hash || '').startsWith(DISCOVER_PREFIX)) return null
  const [view, a, b] = hash.slice(DISCOVER_PREFIX.length).split('/').filter(Boolean)
  if (view === 'artist' && a) return { view: 'artist', ref: dec(a), release: dec(b) }
  if (view === 'search' && a) return { view: 'search', q: dec(a), youtube: b === 'youtube' }
  if (view === 'recommended' || view === 'charts') return { view }
  return { view: 'feed' }
}

export function buildRoute(route) {
  const enc = encodeURIComponent
  switch (route?.view) {
    case 'artist':
      return `${DISCOVER_PREFIX}/artist/${enc(route.ref)}${route.release ? `/${enc(route.release)}` : ''}`
    case 'search':
      return `${DISCOVER_PREFIX}/search/${enc(route.q)}${route.youtube ? '/youtube' : ''}`
    case 'recommended':
    case 'charts':
      return `${DISCOVER_PREFIX}/${route.view}`
    default:
      return DISCOVER_PREFIX
  }
}
