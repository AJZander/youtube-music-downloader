import { useCallback, useEffect, useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, CircularProgress, FormControlLabel,
  LinearProgress, Paper, Radio, Stack, Typography,
} from '@mui/material'

import BlockIcon from '@mui/icons-material/Block'

import { api } from '../../api.js'
import { bytes } from './shared.jsx'

function Group({ group, onMerged }) {
  const [target, setTarget] = useState(group.suggested_keeper)
  const [rewriteTags, setRewriteTags] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(null)

  const sources = group.variants.map((v) => v.artist_dir).filter((n) => n !== target)

  const notTheSame = async () => {
    setBusy(true); setError(null)
    try {
      await api.libraryNotDuplicates(
        'artist', group.variants.map((v) => v.artist_dir), 'different artists')
      setDone({ dismissed: true })
      onMerged?.()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const merge = async () => {
    if (!window.confirm(
      `Move every album from ${sources.map((s) => `"${s}"`).join(', ')} into "${target}"`
      + (rewriteTags ? ', and rewrite the album artist tag to match?' : '?'))) return
    setBusy(true); setError(null)
    try {
      const r = await api.libraryMergeArtists(sources, target, rewriteTags)
      setDone(r); onMerged?.(r)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  if (done?.dismissed) {
    return (
      <Alert severity="success" sx={{ mb: 1.5 }}>
        Recorded as different artists — this will not be reported again.
        Undo it on the Ignored tab.
      </Alert>
    )
  }

  if (done) {
    return (
      <Alert severity="success" sx={{ mb: 1.5 }}>
        Merged into <strong>{target}</strong>: {done.moved} items moved
        {done.retagged > 0 && `, ${done.retagged} files re-tagged`}
        {done.trashed > 0
          && `, ${done.trashed} already present in the target and moved to trash`}.
        {done.failures?.length > 0 && ` ${done.failures.length} item(s) could not move.`}
        {' '}Rescan the library to refresh the index.
      </Alert>
    )
  }

  return (
    <Paper sx={{ p: 2, mb: 2 }}>
      <Typography variant="subtitle2" sx={{ mb: 1 }}>
        Same artist filed under {group.variants.length} spellings
      </Typography>
      {group.variants.map((v) => (
        <Box key={v.artist_dir}
          sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.5, flexWrap: 'wrap' }}>
          <Radio size="small" sx={{ p: 0.5 }} checked={v.artist_dir === target}
            onChange={() => setTarget(v.artist_dir)} />
          <Typography variant="body2" sx={{ fontFamily: 'monospace', flexGrow: 1, minWidth: 160 }}>
            {v.artist_dir}
          </Typography>
          <Chip size="small" label={`${v.album_count} albums`} />
          <Chip size="small" label={`${v.track_count} tracks`} />
          <Chip size="small" variant="outlined" label={bytes(v.bytes)} />
          <Chip size="small" variant="outlined"
            label={v.album_artist_tags.length
              ? `tagged "${v.album_artist_tags[0]}" (${v.tagged_pct}%)`
              : 'no album artist tag'} />
        </Box>
      ))}
      {error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}
      {busy && <LinearProgress sx={{ mt: 1 }} />}
      <Stack direction="row" spacing={1} sx={{ mt: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <FormControlLabel
          control={<Checkbox size="small" checked={rewriteTags}
            onChange={(e) => setRewriteTags(e.target.checked)} />}
          label={<Typography variant="caption">
            Also rewrite the album artist tag — without this Plex keeps showing
            two artists, because it groups on the tag, not the folder
          </Typography>} />
        <Box sx={{ flexGrow: 1 }} />
        <Button size="small" startIcon={<BlockIcon />} disabled={busy}
          onClick={notTheSame}
          title="Different artists whose folder names happen to normalise the same">
          Not the same artist
        </Button>
        <Button size="small" variant="contained" disabled={busy} onClick={merge}>
          Merge into &quot;{target}&quot;
        </Button>
      </Stack>
    </Paper>
  )
}

export default function SplitArtists({ onChanged }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    setLoading(true); setError(null)
    api.libraryDuplicates('artists', { limit: 200 })
      .then(setData).catch((e) => setError(e.message)).finally(() => setLoading(false))
  }, [])
  useEffect(() => { refresh() }, [refresh])

  if (loading) return <Box sx={{ textAlign: 'center', mt: 4 }}><CircularProgress /></Box>
  if (error) return <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>
  if (!data?.total) {
    return <Typography color="text.secondary" sx={{ mt: 4, textAlign: 'center' }}>
      Every artist has a single folder.
    </Typography>
  }

  return (
    <Box>
      <Alert severity="info" sx={{ mb: 2 }}>
        {data.total} artist{data.total === 1 ? '' : 's'} are split across folders
        that differ only in spacing or punctuation. Merging moves the albums together
        and, unless you turn it off, rewrites the album artist tag so Plex stops
        showing two artists. A track&apos;s own artist tag is only changed where it
        matches the old folder name, so featured guests survive.
      </Alert>
      {data.items.map((group) => (
        <Group key={group.id} group={group} onMerged={onChanged} />
      ))}
    </Box>
  )
}
