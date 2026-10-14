import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  Alert, Box, Breadcrumbs, IconButton, Link, Paper, Snackbar, Tooltip, Typography,
  useMediaQuery,
} from '@mui/material'
import MenuIcon from '@mui/icons-material/Menu'
import MenuOpenIcon from '@mui/icons-material/MenuOpen'

import { api, subscribeEvents } from '../api.js'
import { STATS_BAR_HEIGHT, usePaneHeight } from './BottomPane.jsx'
import ArtistList from './library/ArtistList.jsx'
import ArtistView from './library/ArtistView.jsx'
import AttentionSidebar from './library/AttentionSidebar.jsx'
import DetailPane from './library/DetailPane.jsx'
import { ConfirmApplyDialog, IssueListDialog } from './library/IssueList.jsx'
import LibraryHeader from './library/LibraryHeader.jsx'
import {
  DupesManagerDialog, FullRunDialog, IgnoredDialog, MergeArtistDialog, OverrideDialog,
  TrashDialog,
} from './library/dialogs.jsx'
import { buildHash, parseHash } from './library/issues.js'

/** Which pane tab answers an issue of this kind. */
const TAB_FOR_KIND = {
  review_match: 'match', unresolved: 'match', unmatched: 'match', wrong_match: 'match',
  artist_unknown: 'match', folder_artist: 'match',
  dup_album: 'duplicates', dup_track: 'duplicates',
  move: 'files', move_conflict: 'files',
  extra_tracks: 'tracks', not_in_plex: 'tracks', plex_ghost: 'tracks',
  track_order: 'tracks', missing_tracks: 'tracks',
}

function stored(key) {
  try { return localStorage.getItem(key) } catch { return null }
}
function store(key, value) {
  try { localStorage.setItem(key, value) } catch { /* private mode */ }
}

/**
 * The library, managed in one place: every artist on the left, the selected
 * artist's albums and tracks in the middle, what needs attention on the
 * right, and the selected album's full story in the pane at the bottom.
 */
export default function Library() {
  const initial = useRef(parseHash()).current
  const [artistDir, setArtistDir] = useState(initial?.artistDir || null)
  const [albumKey, setAlbumKey] = useState(initial?.albumKey || null)
  const [paneOpen, setPaneOpen] = useState(!!initial?.albumKey)
  const [paneTab, setPaneTab] = useState('compare')
  const [paneHeight, setPaneHeight] = usePaneHeight('mdl-library-pane-height', 320)

  const [overview, setOverview] = useState(null)
  const [scan, setScan] = useState(null)
  const [meta, setMeta] = useState(null)
  const [artist, setArtist] = useState(null)
  const [artistLoading, setArtistLoading] = useState(false)
  const [artistError, setArtistError] = useState(null)
  const [album, setAlbum] = useState(null)
  const [albumLoading, setAlbumLoading] = useState(false)
  const [albumError, setAlbumError] = useState(null)
  const [versions, setVersions] = useState({ list: 0, artist: 0, album: 0, attention: 0 })
  const [modal, setModal] = useState(null)
  const [toast, setToast] = useState(null)

  const bump = useCallback((...keys) => setVersions((v) => {
    const next = { ...v }
    keys.forEach((k) => { next[k] += 1 })
    return next
  }), [])
  const notify = useCallback((message, severity = 'success') => {
    if (message) setToast({ message, severity, key: Date.now() })
  }, [])

  // ------------------------------------------------------------ layout
  const wide = useMediaQuery('(min-width:1200px)')
  const medium = useMediaQuery('(min-width:900px)')
  const [sidebarOpen, setSidebarOpen] = useState(
    () => window.innerWidth >= 1200 && stored('mdl-lib-sidebar') !== '0')
  const [listOpen, setListOpen] = useState(() => window.innerWidth >= 900)
  const [sidebarWidth, setSidebarWidth] = usePaneHeight('mdl-lib-sidebar-width', 320)
  useEffect(() => { setSidebarOpen(wide && stored('mdl-lib-sidebar') !== '0') }, [wide])
  useEffect(() => { setListOpen(medium) }, [medium])
  const toggleSidebar = () => setSidebarOpen((v) => {
    if (wide) store('mdl-lib-sidebar', v ? '0' : '1')
    return !v
  })

  const workspace = useRef(null)
  const [top, setTop] = useState(0)
  const [winHeight, setWinHeight] = useState(window.innerHeight)
  useEffect(() => {
    const resize = () => setWinHeight(window.innerHeight)
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [])
  // The header above the workspace grows and shrinks (alerts, progress), so
  // the columns are re-fitted to the window after every render. setTop only
  // fires when the position really moved, so this settles after one pass.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    if (!workspace.current) return
    const t = workspace.current.getBoundingClientRect().top + window.scrollY
    if (Math.abs(t - top) > 1) setTop(t)
  })
  const paneVisible = paneOpen && !!(albumKey || artistDir)
  const workspaceHeight = Math.max(
    360, winHeight - top - STATS_BAR_HEIGHT - (paneVisible ? paneHeight : 0) - 12)

  // ------------------------------------------------------------ navigation
  const selection = useRef({ artistDir, albumKey })
  selection.current = { artistDir, albumKey }

  const navigate = useCallback((dir, key = null, opts = {}) => {
    const { albumKey: currentKey } = selection.current
    setArtistDir(dir || null)
    setAlbumKey(key || null)
    if (key) {
      if (opts.tab) setPaneTab(opts.tab)
      else if (key !== currentKey) setPaneTab('compare')
      if (opts.pane !== false) setPaneOpen(true)
    } else if (opts.pane) {
      setPaneTab(opts.tab || 'overview')
      setPaneOpen(true)
    } else {
      setPaneOpen(false)
    }
    const hash = buildHash(dir, key)
    if (window.location.hash !== hash) window.history.pushState(null, '', hash)
  }, [])

  useEffect(() => {
    const sync = () => {
      const parsed = parseHash()
      if (!parsed) return
      setArtistDir(parsed.artistDir)
      setAlbumKey(parsed.albumKey)
      if (!parsed.albumKey) setPaneOpen(false)
    }
    window.addEventListener('popstate', sync)
    window.addEventListener('hashchange', sync)
    if (!window.location.hash.startsWith('#/library')) {
      window.history.replaceState(null, '', buildHash(artistDir, albumKey))
    }
    return () => {
      window.removeEventListener('popstate', sync)
      window.removeEventListener('hashchange', sync)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const navigateIssue = useCallback((issue) => {
    if (issue.kind === 'split_artist') {
      setModal({ type: 'merge', artistDir: issue.artist_dir || issue.ref })
      return
    }
    const key = issue.album_key || (issue.entity === 'album' ? issue.ref : null)
    if (key) {
      navigate(issue.artist_dir, key, { tab: TAB_FOR_KIND[issue.kind] || 'compare' })
    } else {
      navigate(issue.artist_dir || issue.ref, null, { pane: true, tab: 'overview' })
    }
  }, [navigate])

  // ------------------------------------------------------------ data
  const refreshOverview = useCallback(() => {
    api.libraryOverview().then((d) => {
      setOverview(d)
      if (d.scan) setScan(d.scan)
      if (d.metadata) setMeta(d.metadata)
    }).catch(() => {})
  }, [])
  useEffect(() => {
    refreshOverview()
    api.metadataStatus().then(setMeta).catch(() => {})
  }, [refreshOverview])

  useEffect(() => {
    if (!artistDir) { setArtist(null); setArtistError(null); return undefined }
    let live = true
    setArtist((prev) => (prev?.artist_dir === artistDir ? prev : null))
    setArtistLoading(true); setArtistError(null)
    api.libraryArtist(artistDir)
      .then((d) => { if (live) setArtist(d) })
      .catch((e) => { if (live) setArtistError(e.message) })
      .finally(() => { if (live) setArtistLoading(false) })
    return () => { live = false }
  }, [artistDir, versions.artist])

  useEffect(() => {
    if (!albumKey) { setAlbum(null); setAlbumError(null); return undefined }
    let live = true
    setAlbum((prev) => (prev?.key === albumKey ? prev : null))
    setAlbumLoading(true); setAlbumError(null)
    api.libraryAlbum(albumKey)
      .then((d) => { if (live) setAlbum(d) })
      .catch((e) => { if (live) setAlbumError(e.message) })
      .finally(() => { if (live) setAlbumLoading(false) })
    return () => { live = false }
  }, [albumKey, versions.album])

  // Several events arrive in bursts while a run applies fixes; each kind of
  // refetch waits for the burst to settle.
  const timers = useRef({})
  const later = useCallback((what, delay = 800) => {
    clearTimeout(timers.current[what])
    timers.current[what] = setTimeout(() => {
      if (what === 'overview') refreshOverview(); else bump(what)
    }, delay)
  }, [bump, refreshOverview])
  useEffect(() => () => Object.values(timers.current).forEach(clearTimeout), [])

  const onChanged = useCallback(() => {
    bump('artist', 'album', 'attention')
    later('list', 1500)
    later('overview', 2000)
  }, [bump, later])

  useEffect(() => subscribeEvents({
    'library.scan': setScan,
    'metadata.status': setMeta,
    'library.issues': (d) => {
      setOverview((o) => (o ? { ...o, issues: d } : o))
      later('attention')
    },
    'library.changed': (d) => {
      const { artistDir: dir, albumKey: key } = selection.current
      const moved = key && d?.renamed?.[key]
      if (moved) {
        // the album on screen was moved: follow it to its new key
        navigate(moved.artist_dir || dir, moved.key, { pane: true })
        later('list', 3000)
        later('attention')
        return
      }
      if (dir && d?.artist_dirs?.includes(dir)) later('artist')
      if (key && d?.album_keys?.includes(key)) later('album')
      later('attention')
      later('list', 3000)
      later('overview', 5000)
    },
  }), [later, navigate])

  // SSE carries the scan, but a page opened mid-scan has to catch up somehow.
  useEffect(() => {
    if (!scan?.running) return undefined
    const id = setInterval(() => {
      api.libraryScanStatus().then(setScan).catch(() => {})
    }, 3000)
    return () => clearInterval(id)
  }, [scan?.running])

  // When a long job ends, everything on screen is stale.
  const wasBusy = useRef({ scan: false, meta: false })
  useEffect(() => {
    const scanning = !!scan?.running
    const running = !!meta?.running
    if ((wasBusy.current.scan && !scanning) || (wasBusy.current.meta && !running)) {
      refreshOverview()
      bump('list', 'artist', 'album', 'attention')
    }
    wasBusy.current = { scan: scanning, meta: running }
  }, [scan?.running, meta?.running, refreshOverview, bump])

  // ------------------------------------------------------------ actions
  const metaAction = async (fn, message) => {
    try { setMeta(await fn()); notify(message) } catch (e) { notify(e.message, 'error') }
  }
  const startScan = async (full) => {
    try {
      await api.libraryScan(full)
      setScan((s) => ({ ...(s || {}), running: true, phase: 'walking', total: 0, done: 0 }))
    } catch (e) { notify(e.message, 'error') }
  }

  const fixMatch = () => {
    if (!artist) return
    const target = (artist.albums || []).find((a) => ['review', 'unresolved'].includes(a.status)
      || (a.flags || []).some((f) => ['unmatched', 'wrong_match'].includes(f.kind)))
      || (artist.albums || []).find((a) => a.plex && !a.plex.matched)
    if (target) {
      navigate(artist.artist_dir, target.key, { tab: 'match' })
    } else if ((artist.issues || []).some((i) => i.kind === 'artist_unmatched' && i.state === 'open')) {
      navigate(artist.artist_dir, null, { pane: true, tab: 'overview' })
    } else {
      notify('Nothing to fix — every album of this artist is matched', 'info')
    }
  }

  const onOverrideSaved = (entity) => (detail) => {
    if (!detail) return
    if (entity === 'album' && detail.key) setAlbum(detail)
    if (entity === 'artist' && detail.artist_dir) setArtist(detail)
    bump(entity === 'album' ? 'artist' : 'album', 'attention')
  }

  const crumbArtist = artist?.name || artistDir
  const crumbAlbum = album?.key === albumKey ? album?.album : null

  return (
    <Box>
      <LibraryHeader overview={overview} scan={scan} meta={meta}
        onScan={startScan}
        onFullRun={() => setModal({ type: 'fullrun' })}
        onCheckNew={() => metaAction(() => api.metadataRun(false, !meta?.auto_apply),
          'Checking what is new')}
        onPause={() => metaAction(api.metadataPause, 'Paused')}
        onResume={() => metaAction(api.metadataResume, 'Resumed')}
        onCancel={() => {
          if (window.confirm('Cancel this run? Albums already checked keep their results.')) {
            metaAction(api.metadataCancel, 'Run cancelled')
          }
        }}
        onAutoApply={(enabled) => {
          if (enabled && !window.confirm('Turn on auto-apply? Confident fixes — tags, '
            + 'renames and moves, Plex matches, genres — will be applied as albums are '
            + 'checked, without asking. Every change is logged under Activity and can be '
            + 'undone.')) return
          metaAction(() => api.metadataAutoApply(enabled),
            enabled ? 'Auto-apply is on' : 'Auto-apply is off')
        }}
        onPlexEmptyTrash={async () => {
          try {
            const r = await api.plexEmptyTrash()
            notify(r.ok ? 'Plex trash emptied' : 'Plex did not accept the request', r.ok ? 'success' : 'warning')
          } catch (e) { notify(e.message, 'error') }
        }} />

      <Box ref={workspace} sx={{
        display: 'grid', gap: 1, height: workspaceHeight,
        gridTemplateColumns: `${listOpen ? '280px ' : ''}minmax(0, 1fr) ${
          sidebarOpen ? sidebarWidth + 5 : 44}px`,
      }}>
        {listOpen && (
          <Paper variant="outlined" sx={{ overflow: 'hidden', minHeight: 0 }}>
            <ArtistList selected={artistDir} version={versions.list}
              onSelect={(dir) => navigate(dir, null)} />
          </Paper>
        )}

        <Paper variant="outlined" sx={{ overflow: 'auto', minHeight: 0 }}>
          <Box sx={{
            display: 'flex', alignItems: 'center', gap: 0.5, px: 0.5, py: 0.25,
            borderBottom: 1, borderColor: 'divider', position: 'sticky', top: 0, zIndex: 2,
            bgcolor: 'background.paper',
          }}>
            <Tooltip title={listOpen ? 'Hide artists' : 'Show artists'}>
              <IconButton size="small" onClick={() => setListOpen((v) => !v)}>
                {listOpen ? <MenuOpenIcon fontSize="small" /> : <MenuIcon fontSize="small" />}
              </IconButton>
            </Tooltip>
            <Breadcrumbs sx={{ fontSize: '0.8rem', minWidth: 0 }} maxItems={3}>
              <Link component="button" underline="hover" color="inherit" variant="body2"
                onClick={() => navigate(null, null)}>
                Library
              </Link>
              {artistDir && (
                <Link component="button" underline="hover" variant="body2"
                  color={albumKey ? 'inherit' : 'text.primary'}
                  onClick={() => navigate(artistDir, null)}>
                  {crumbArtist}
                </Link>
              )}
              {albumKey && (
                <Typography variant="body2" color="text.primary" noWrap sx={{ maxWidth: 320 }}>
                  {crumbAlbum || '…'}
                </Typography>
              )}
            </Breadcrumbs>
          </Box>
          <ArtistView artist={artist} loading={artistLoading} error={artistError}
            selectedKey={albumKey} albumDetail={album}
            onSelectAlbum={(key, opts) => navigate(artistDir, key, opts || {})}
            onOpenPane={() => navigate(artistDir, null, { pane: true, tab: 'overview' })}
            onEdit={(m) => setModal({ type: 'override', ...m })}
            onMerge={() => setModal({ type: 'merge', artistDir })}
            onFixMatch={fixMatch}
            onChanged={onChanged} notify={notify}
            onNavigateIssue={navigateIssue} />
        </Paper>

        <Paper variant="outlined" sx={{ overflow: 'hidden', minHeight: 0 }}>
          <AttentionSidebar open={sidebarOpen} onToggle={toggleSidebar}
            width={sidebarWidth} onWidth={setSidebarWidth}
            artistDir={artistDir} meta={meta} overview={overview}
            version={versions.attention}
            onApply={(group, dir) => setModal({
              type: 'apply', kind: group.kind, count: group.auto_count, artistDir: dir })}
            onReview={(group, dir) => setModal({ type: 'issues', kind: group.kind, artistDir: dir })}
            onResolveDupes={(tab) => setModal({ type: 'dupes', tab })}
            onTrash={() => setModal({ type: 'trash' })}
            onIgnored={() => setModal({ type: 'ignored' })}
            onChanged={onChanged} notify={notify} />
        </Paper>
      </Box>

      {paneVisible && (
        <DetailPane mode={albumKey ? 'album' : 'artist'}
          album={album?.key === albumKey ? album : null} artist={artist}
          loading={albumKey ? albumLoading : artistLoading}
          error={albumKey ? albumError : artistError}
          height={paneHeight} onHeight={setPaneHeight}
          onClose={() => setPaneOpen(false)}
          tab={paneTab} onTab={setPaneTab}
          onEdit={(m) => setModal({ type: 'override', ...m })}
          onChanged={onChanged}
          onAlbumDetail={(d) => { if (d?.key) setAlbum(d); bump('artist', 'attention') }}
          notify={notify} version={versions.artist} />
      )}

      {modal?.type === 'fullrun' && (
        <FullRunDialog status={meta} notify={notify} onClose={() => setModal(null)}
          onStarted={setMeta} />
      )}
      {modal?.type === 'apply' && (
        <ConfirmApplyDialog kind={modal.kind} count={modal.count} artistDir={modal.artistDir}
          notify={notify} onClose={() => setModal(null)} onDone={onChanged} />
      )}
      {modal?.type === 'issues' && (
        <IssueListDialog kind={modal.kind} artistDir={modal.artistDir} notify={notify}
          onClose={() => setModal(null)} onNavigate={navigateIssue} onChanged={onChanged} />
      )}
      {modal?.type === 'dupes' && (
        <DupesManagerDialog initial={modal.tab} scanRunning={scan?.running}
          onClose={() => { setModal(null); onChanged() }} onChanged={onChanged} />
      )}
      {modal?.type === 'trash' && (
        <TrashDialog onClose={() => setModal(null)}
          onChanged={() => { onChanged(); refreshOverview() }} />
      )}
      {modal?.type === 'ignored' && (
        <IgnoredDialog onClose={() => setModal(null)} onChanged={onChanged} />
      )}
      {modal?.type === 'merge' && (
        <MergeArtistDialog artistDir={modal.artistDir}
          onClose={() => setModal(null)} onChanged={onChanged} />
      )}
      {modal?.type === 'override' && (
        <OverrideDialog entity={modal.entity} refKey={modal.refKey} field={modal.field}
          label={modal.label} value={modal.value} overridden={modal.overridden}
          notify={notify} onClose={() => setModal(null)}
          onSaved={onOverrideSaved(modal.entity)} />
      )}

      <Snackbar open={!!toast} key={toast?.key} autoHideDuration={4000}
        anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
        onClose={(_, reason) => { if (reason !== 'clickaway') setToast(null) }}>
        {toast ? (
          <Alert severity={toast.severity} variant="filled" onClose={() => setToast(null)}
            sx={{ width: '100%' }}>
            {toast.message}
          </Alert>
        ) : <span />}
      </Snackbar>
    </Box>
  )
}
