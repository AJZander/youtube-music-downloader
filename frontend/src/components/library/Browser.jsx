import { useCallback, useEffect, useState } from 'react'
import {
  Box, Chip, CircularProgress, Divider, Grid, InputAdornment, LinearProgress,
  List, ListItemButton, ListItemText, MenuItem, Paper, Select, Stack,
  TextField, Typography,
} from '@mui/material'
import SearchIcon from '@mui/icons-material/Search'

import { api } from '../../api.js'
import { HealthChips, QualityChip, bytes, duration } from './shared.jsx'

function AlbumCard({ album, expanded, onToggle }) {
  const [tracks, setTracks] = useState(null)

  useEffect(() => {
    if (expanded && !tracks) {
      api.libraryAlbumTracks(album.dirpath).then((d) => setTracks(d.tracks)).catch(() => {})
    }
  }, [expanded, tracks, album.dirpath])

  return (
    <Paper variant="outlined" sx={{ p: 1.5, mb: 1 }}>
      <Box onClick={onToggle} sx={{ cursor: 'pointer', display: 'flex',
        alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Box sx={{ flexGrow: 1, minWidth: 200 }}>
          <Typography variant="body2">
            {album.album} {album.year && (
              <Typography component="span" variant="caption" color="text.secondary">
                ({album.year})
              </Typography>
            )}
          </Typography>
          <Typography variant="caption" color="text.secondary"
            sx={{ fontFamily: 'monospace' }}>
            {album.dirpath}
          </Typography>
        </Box>
        <Chip size="small" label={`${album.track_count} tracks`} />
        {album.formats.map((f) => (
          <QualityChip key={f} format={f} bitrate={album.avg_bitrate} />
        ))}
        <Chip size="small" variant="outlined" label={bytes(album.bytes)} />
        <HealthChips issues={album.health} />
      </Box>
      {expanded && (
        <Box sx={{ mt: 1 }}>
          <Divider sx={{ mb: 1 }} />
          {!tracks && <LinearProgress />}
          {tracks?.map((t) => (
            <Box key={t.path} sx={{ display: 'flex', gap: 1, alignItems: 'center', py: 0.2 }}>
              <Typography variant="caption" color="text.secondary" sx={{ width: 26 }}>
                {t.track_number ?? '–'}
              </Typography>
              <Typography variant="body2" noWrap sx={{ flexGrow: 1, minWidth: 0 }}>
                {t.title}
              </Typography>
              <QualityChip format={t.format} bitrate={t.bitrate_kbps} />
              <Typography variant="caption" color="text.secondary" sx={{ width: 46, textAlign: 'right' }}>
                {duration(t.duration_sec)}
              </Typography>
            </Box>
          ))}
        </Box>
      )}
    </Paper>
  )
}

export default function Browser() {
  const [artists, setArtists] = useState(null)
  const [q, setQ] = useState('')
  const [sort, setSort] = useState('name')
  const [selected, setSelected] = useState(null)
  const [albums, setAlbums] = useState(null)
  const [openAlbum, setOpenAlbum] = useState(null)

  const refresh = useCallback(() => {
    api.libraryArtists({ q, sort, limit: 2000 }).then(setArtists).catch(() => {})
  }, [q, sort])
  useEffect(() => {
    const id = setTimeout(refresh, 250)
    return () => clearTimeout(id)
  }, [refresh])

  useEffect(() => {
    if (!selected) return
    setAlbums(null); setOpenAlbum(null)
    api.libraryAlbums(selected).then(setAlbums).catch(() => setAlbums({ albums: [] }))
  }, [selected])

  return (
    <Grid container spacing={2}>
      <Grid item xs={12} md={4}>
        <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
          <TextField size="small" fullWidth placeholder="Search artists"
            value={q} onChange={(e) => setQ(e.target.value)}
            InputProps={{ startAdornment: (
              <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>
            ) }} />
          <Select size="small" value={sort} onChange={(e) => setSort(e.target.value)}
            sx={{ minWidth: 110 }}>
            <MenuItem value="name">Name</MenuItem>
            <MenuItem value="tracks">Tracks</MenuItem>
            <MenuItem value="albums">Albums</MenuItem>
            <MenuItem value="size">Size</MenuItem>
          </Select>
        </Stack>
        <Paper variant="outlined" sx={{ maxHeight: '65vh', overflow: 'auto' }}>
          {!artists && <Box sx={{ p: 2, textAlign: 'center' }}><CircularProgress size={24} /></Box>}
          <List dense disablePadding>
            {artists?.items.map((a) => (
              <ListItemButton key={a.artist_dir} selected={a.artist_dir === selected}
                onClick={() => setSelected(a.artist_dir)}>
                <ListItemText
                  primary={a.artist_dir}
                  secondary={`${a.albums} albums · ${a.tracks} tracks · ${bytes(a.bytes)}`}
                  primaryTypographyProps={{ variant: 'body2', noWrap: true }}
                  secondaryTypographyProps={{ variant: 'caption' }} />
              </ListItemButton>
            ))}
          </List>
          {artists && !artists.items.length && (
            <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
              No artists match.
            </Typography>
          )}
        </Paper>
        {artists && (
          <Typography variant="caption" color="text.secondary">
            {artists.total} artists
          </Typography>
        )}
      </Grid>

      <Grid item xs={12} md={8}>
        {!selected && (
          <Typography color="text.secondary" sx={{ mt: 4, textAlign: 'center' }}>
            Pick an artist to see what is actually on disk.
          </Typography>
        )}
        {selected && !albums && <LinearProgress />}
        {albums && (
          <>
            <Typography variant="subtitle1" sx={{ mb: 1 }}>
              {selected}
              <Typography component="span" variant="caption" color="text.secondary"
                sx={{ ml: 1 }}>
                {albums.tracks} tracks · {bytes(albums.bytes)}
              </Typography>
            </Typography>
            {albums.albums?.map((album) => (
              <AlbumCard key={album.dirpath} album={album}
                expanded={openAlbum === album.dirpath}
                onToggle={() => setOpenAlbum(
                  openAlbum === album.dirpath ? null : album.dirpath)} />
            ))}
          </>
        )}
      </Grid>
    </Grid>
  )
}
