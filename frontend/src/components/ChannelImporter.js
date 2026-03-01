// frontend/src/components/ChannelImporter.js
import React, { useState, useCallback, useEffect } from 'react';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Divider,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  InputAdornment,
  TextField,
  Tooltip,
  Typography,
  LinearProgress,
  Card,
  CardContent,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import LinkIcon from '@mui/icons-material/Link';
import QueueMusicIcon from '@mui/icons-material/QueueMusic';
import SelectAllIcon from '@mui/icons-material/SelectAll';
import DeselectIcon from '@mui/icons-material/Deselect';
import MusicNoteIcon from '@mui/icons-material/MusicNote';
import AlbumIcon from '@mui/icons-material/Album';
import AudiotrackIcon from '@mui/icons-material/Audiotrack';
import AnalyticsIcon from '@mui/icons-material/Analytics';
import CancelIcon from '@mui/icons-material/Cancel';
import CheckboxIcon from '@mui/icons-material/CheckBox';
import CheckboxOutlineBlankIcon from '@mui/icons-material/CheckBoxOutlineBlank';

import { api } from '../api';

// ── helpers ────────────────────────────────────────────────────────────────────

const trackLabel = (n) => {
  if (!n) return null;
  return `${n} track${n !== 1 ? 's' : ''}`;
};

// Placeholder when a playlist has no thumbnail
function PlaceholderThumb() {
  return (
    <Box
      sx={{
        width: 64,
        height: 64,
        borderRadius: 1,
        bgcolor: '#252530',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
      }}
    >
      <MusicNoteIcon sx={{ color: 'rgba(255,255,255,0.2)', fontSize: 28 }} />
    </Box>
  );
}

// ── ReleaseTypeChip ────────────────────────────────────────────────────────────

const RELEASE_STYLES = {
  album:    { label: 'Album',    bg: 'rgba(16,185,129,0.12)',  color: '#34D399', border: 'rgba(16,185,129,0.25)', icon: <AlbumIcon /> },
  single:   { label: 'Single',   bg: 'rgba(249,115,22,0.12)',  color: '#FB923C', border: 'rgba(249,115,22,0.25)', icon: <AudiotrackIcon /> },
  playlist: { label: 'Playlist', bg: 'rgba(59,130,246,0.12)',  color: '#60A5FA', border: 'rgba(59,130,246,0.25)', icon: <QueueMusicIcon /> },
};

function ReleaseTypeChip({ type }) {
  const style = RELEASE_STYLES[type] || RELEASE_STYLES.playlist;
  return (
    <Chip
      icon={style.icon && React.cloneElement(style.icon, { sx: { fontSize: '0.75rem' } })}
      label={style.label}
      size="small"
      sx={{
        height: 20,
        fontSize: '0.68rem',
        bgcolor: style.bg,
        color: style.color,
        border: `1px solid ${style.border}`,
        '& .MuiChip-icon': {
          fontSize: '0.75rem',
          marginLeft: '4px',
          color: style.color,
        },
      }}
    />
  );
}

// ── PlaylistRow ────────────────────────────────────────────────────────────────

function PlaylistRow({ item, checked, onToggle, onQueueOne, queuing }) {
  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: { xs: 1, sm: 1.5 },
        py: 1,
        px: { xs: 0.5, sm: 1 },
        borderRadius: 1.5,
        transition: 'background 0.15s',
        '&:hover': { bgcolor: 'rgba(139,92,246,0.06)' },
      }}
    >
      {/* Checkbox */}
      <Checkbox
        checked={checked}
        onChange={() => onToggle(item.id)}
        size="small"
        sx={{
          color: 'rgba(255,255,255,0.3)',
          '&.Mui-checked': { color: '#8B5CF6' },
          p: 0.5,
          flexShrink: 0,
        }}
      />

      {/* Thumbnail */}
      {item.thumbnail ? (
        <Box
          component="img"
          src={item.thumbnail}
          alt={item.title}
          sx={{
            width: { xs: 48, sm: 64 },
            height: { xs: 48, sm: 64 },
            borderRadius: 1,
            objectFit: 'cover',
            flexShrink: 0,
          }}
          onError={(e) => { e.currentTarget.style.display = 'none'; }}
        />
      ) : (
        <PlaceholderThumb />
      )}

      {/* Info */}
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          variant="body2"
          sx={{
            color: '#fff',
            fontWeight: 500,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            fontSize: { xs: '0.8rem', sm: '0.875rem' },
          }}
        >
          {item.title}
        </Typography>
        <Box sx={{ display: 'flex', gap: 0.5, mt: 0.5, flexWrap: 'wrap' }}>
          <ReleaseTypeChip type={item.release_type} />
          {item.track_count && (
            <Chip
              label={trackLabel(item.track_count)}
              size="small"
              sx={{
                height: 18,
                fontSize: '0.68rem',
                bgcolor: 'rgba(139,92,246,0.15)',
                color: '#A78BFA',
                border: '1px solid rgba(139,92,246,0.25)',
              }}
            />
          )}
        </Box>
      </Box>

      {/* Download button */}
      <Tooltip title={`Queue "${item.title}"`}>
        <span>
          <Button
            size="small"
            onClick={() => onQueueOne(item)}
            disabled={queuing}
            variant="outlined"
            sx={{
              height: { xs: 28, sm: 32 },
              minWidth: { xs: 28, sm: 32 },
              width: { xs: 28, sm: 32 },
              p: 0,
              flexShrink: 0,
              borderColor: 'rgba(139,92,246,0.4)',
              color: '#A78BFA',
              '&:hover': {
                borderColor: '#8B5CF6',
                bgcolor: 'rgba(139,92,246,0.1)',
              },
              '&.Mui-disabled': { opacity: 0.4 },
            }}
          >
            <DownloadIcon sx={{ fontSize: { xs: 14, sm: 16 } }} />
          </Button>
        </span>
      </Tooltip>
    </Box>
  );
}

// ── Main ChannelImporter component ─────────────────────────────────────────────

export default function ChannelImporter({ onQueued, onError }) {
  // Basic UI state
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  
  // Metadata processing state
  const [metadataJob, setMetadataJob] = useState(null);
  const [processingJobId, setProcessingJobId] = useState(null);
  const [includeUploads, setIncludeUploads] = useState(true);
  const [format, setFormat] = useState('mp3');
  
  // Results and selection state
  const [playlists, setPlaylists] = useState(null);
  const [channelName, setChannelName] = useState('');
  const [selected, setSelected] = useState(new Set());
  const [queuing, setQueuing] = useState(false);

  const playlistKey = (p) => p.id ?? p.url;

  // ── Metadata processing workflow ──────────────────────────────────────────────

  const startMetadataProcessing = async () => {
    const trimmed = url.trim();
    if (!trimmed) return;

    setError('');
    setMetadataJob(null);
    setProcessingJobId(null);
    setPlaylists(null);
    setSelected(new Set());
    setChannelName('');

    try {
      const response = await api.startMetadataProcessing(trimmed, { 
        include_uploads: includeUploads, 
        format 
      });
      setProcessingJobId(response.job_id);
      setMetadataJob({ status: 'running', progress: 0 });
    } catch (err) {
      const detail = err.response?.data?.detail || 'Failed to start metadata processing';
      setError(detail);
      onError?.(detail);
    }
  };

  // Poll for metadata job status
  useEffect(() => {
    if (!processingJobId) return;

    const pollInterval = setInterval(async () => {
      try {
        const job = await api.getMetadataJob(processingJobId);
        setMetadataJob(job);

        if (job.status === 'completed' || job.status === 'failed') {
          clearInterval(pollInterval);
          if (job.status === 'completed' && job.results?.playlists) {
            setPlaylists(job.results.playlists);
            setChannelName(job.results.channel_name || '');
            // Pre-select all items
            setSelected(new Set(job.results.playlists.map(playlistKey)));
          } else if (job.status === 'failed') {
            setError(job.error || 'Metadata processing failed');
          }
        }
      } catch (err) {
        console.error('Failed to poll metadata job:', err);
        clearInterval(pollInterval);
        setError('Failed to check processing status');
      }
    }, 1000);

    return () => clearInterval(pollInterval);
  }, [processingJobId]);

  const handleCancelProcessing = async () => {
    if (!processingJobId) return;
    try {
      await api.cancelMetadataJob(processingJobId);
      setMetadataJob(null);
      setProcessingJobId(null);
    } catch (err) {
      console.error('Failed to cancel job:', err);
    }
  };

  // ── Selection helpers ─────────────────────────────────────────────────────────
  const toggleOne = (key) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  const selectAll = () =>
    setSelected(new Set((playlists || []).map(playlistKey)));

  const deselectAll = () => setSelected(new Set());

  const selectAlbumsOnly = () => {
    const albums = (playlists || []).filter(p => p.release_type === 'album');
    setSelected(new Set(albums.map(playlistKey)));
  };

  const selectSinglesOnly = () => {
    const singles = (playlists || []).filter(p => p.release_type === 'single');
    setSelected(new Set(singles.map(playlistKey)));
  };

  // ── Queue helpers ─────────────────────────────────────────────────────────────
  const doQueue = async (toQueue) => {
    if (!toQueue.length) return;
    setQueuing(true);
    try {
      const result = await api.queueChannelPlaylists(toQueue);
      onQueued?.(result.total, channelName, result.batch_id);
      // Reset after successful queue
      setPlaylists(null);
      setUrl('');
      setSelected(new Set());
      setChannelName('');
      setMetadataJob(null);
      setProcessingJobId(null);
    } catch (err) {
      const detail = err.response?.data?.detail || 'Failed to queue playlists';
      setError(detail);
      onError?.(detail);
    } finally {
      setQueuing(false);
    }
  };

  const handleQueueSelected = () => {
    const toQueue = (playlists || []).filter((p) => selected.has(playlistKey(p)));
    doQueue(toQueue);
  };

  const handleQueueOne = (playlist) => doQueue([playlist]);

  // ── Render helpers ─────────────────────────────────────────────────────────────
  const isProcessing = metadataJob?.status === 'running';
  const hasResults = playlists !== null;
  const canStartProcessing = url.trim() && !isProcessing && !hasResults && !queuing;

  return (
    <Box>
      {/* URL input and controls */}
      <Card sx={{ bgcolor: '#1a1a1a', border: '1px solid rgba(255,255,255,0.1)' }}>
        <CardContent sx={{ p: 2.5 }}>
          <Box sx={{ display: 'flex', gap: { xs: 1, sm: 1.5 }, alignItems: 'center', flexDirection: { xs: 'column', sm: 'row' } }}>
            <TextField
              fullWidth
              placeholder="Paste a YouTube channel URL (e.g. https://www.youtube.com/@ArtistName)…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && canStartProcessing && startMetadataProcessing()}
              disabled={isProcessing || queuing}
              size="small"
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <LinkIcon sx={{ fontSize: 18, color: 'rgba(255,255,255,0.3)' }} />
                  </InputAdornment>
                ),
              }}
              sx={{
                '& .MuiOutlinedInput-root': {
                  bgcolor: '#252530',
                  '& fieldset': { borderColor: 'rgba(255,255,255,0.1)' },
                  '&:hover fieldset': { borderColor: 'rgba(255,255,255,0.2)' },
                  '&.Mui-focused fieldset': { borderColor: '#8B5CF6', borderWidth: 1 },
                },
                '& input': {
                  color: '#fff',
                  fontSize: { xs: '0.875rem', sm: '1rem' },
                  '&::placeholder': { color: 'rgba(255,255,255,0.3)', opacity: 1 },
                },
              }}
            />

            <Button
              variant="contained"
              onClick={startMetadataProcessing}
              disabled={!canStartProcessing}
              startIcon={
                isProcessing ? (
                  <CircularProgress size={14} sx={{ color: 'rgba(255,255,255,0.5)' }} />
                ) : (
                  <AnalyticsIcon sx={{ fontSize: 18 }} />
                )
              }
              sx={{
                minWidth: { xs: '100%', sm: 160 },
                height: 40,
                textTransform: 'none',
                fontWeight: 600,
                fontSize: '0.875rem',
                background: 'linear-gradient(135deg, #8B5CF6, #6D28D9)',
                boxShadow: 'none',
                borderRadius: 1.5,
                whiteSpace: 'nowrap',
                '&:hover': { opacity: 0.9, boxShadow: '0 4px 16px rgba(109,40,217,0.4)' },
                '&.Mui-disabled': { bgcolor: '#252530', color: 'rgba(255,255,255,0.3)' },
              }}
            >
              {isProcessing ? 'Processing…' : 'Analyze Channel'}
            </Button>
          </Box>

          {/* Processing options */}
          <Box sx={{ display: 'flex', gap: 2, mt: 2, alignItems: 'center', flexWrap: 'wrap' }}>
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <InputLabel sx={{ color: 'rgba(255,255,255,0.6)' }}>Format</InputLabel>
              <Select
                value={format}
                onChange={(e) => setFormat(e.target.value)}
                disabled={isProcessing || hasResults}
                label="Format"
                sx={{
                  color: '#fff',
                  '& .MuiOutlinedInput-notchedOutline': {
                    borderColor: 'rgba(255,255,255,0.1)',
                  },
                  '&:hover .MuiOutlinedInput-notchedOutline': {
                    borderColor: 'rgba(255,255,255,0.2)',
                  },
                  '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                    borderColor: '#8B5CF6',
                  },
                }}
              >
                <MenuItem value="mp3">MP3</MenuItem>
                <MenuItem value="flac">FLAC</MenuItem>
                <MenuItem value="wav">WAV</MenuItem>
              </Select>
            </FormControl>

            <Box sx={{ display: 'flex', alignItems: 'center' }}>
              <Checkbox
                checked={includeUploads}
                onChange={(e) => setIncludeUploads(e.target.checked)}
                disabled={isProcessing || hasResults}
                size="small"
                sx={{
                  color: 'rgba(255,255,255,0.3)',
                  '&.Mui-checked': { color: '#8B5CF6' },
                }}
              />
              <Typography
                variant="body2"
                sx={{ color: 'rgba(255,255,255,0.6)', ml: 0.5 }}
              >
                Include uploads playlist
              </Typography>
            </Box>
          </Box>
        </CardContent>
      </Card>

      {/* Error display */}
      {error && (
        <Alert severity="error" sx={{ mt: 1.5, bgcolor: '#1a0a0a' }}>
          {error}
        </Alert>
      )}

      {/* Processing status */}
      {metadataJob && (
        <Card sx={{ mt: 2, bgcolor: '#1a1a1a', border: '1px solid rgba(255,255,255,0.1)' }}>
          <CardContent sx={{ p: 2.5 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}>
              <Typography variant="h6" sx={{ color: '#fff', fontSize: '1rem', fontWeight: 600 }}>
                Scanning Channel Metadata
              </Typography>
              {isProcessing && (
                <Button
                  size="small"
                  onClick={handleCancelProcessing}
                  startIcon={<CancelIcon sx={{ fontSize: 14 }} />}
                  sx={{
                    color: 'rgba(239,68,68,0.8)',
                    '&:hover': { color: '#EF4444', bgcolor: 'rgba(239,68,68,0.1)' },
                  }}
                >
                  Cancel
                </Button>
              )}
            </Box>

            {metadataJob.status === 'running' && (
              <>
                <LinearProgress
                  variant="determinate"
                  value={metadataJob.progress || 0}
                  sx={{
                    mb: 1,
                    '& .MuiLinearProgress-bar': { bgcolor: '#8B5CF6' },
                    bgcolor: 'rgba(255,255,255,0.1)',
                  }}
                />
                <Typography variant="body2" sx={{ color: 'rgba(255,255,255,0.6)' }}>
                  {metadataJob.current_playlist || 'Initializing...'}
                </Typography>
              </>
            )}

            {metadataJob.status === 'completed' && (
              <Typography variant="body2" sx={{ color: '#10B981' }}>
                ✓ Analysis complete! Found {playlists?.length || 0} playlists
              </Typography>
            )}

            {metadataJob.status === 'failed' && (
              <Typography variant="body2" sx={{ color: '#EF4444' }}>
                ✗ Failed: {metadataJob.error || 'Unknown error'}
              </Typography>
            )}
          </CardContent>
        </Card>
      )}

      {/* Results */}
      {hasResults && (
        <Box sx={{ mt: 2 }}>
          <Divider sx={{ borderColor: 'rgba(255,255,255,0.07)', mb: 1.5 }} />

          {/* Header row */}
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              mb: 1,
              flexWrap: 'wrap',
              gap: 1,
            }}
          >
            <Typography
              variant="body2"
              sx={{ color: 'rgba(255,255,255,0.6)', fontWeight: 500 }}
            >
              {channelName && (
                <Box component="span" sx={{ color: '#A78BFA', mr: 0.5 }}>
                  {channelName}
                </Box>
              )}
              {playlists.length === 0
                ? 'No playlists found'
                : `${playlists.length} playlist${playlists.length !== 1 ? 's' : ''} analyzed`}
            </Typography>

            {playlists.length > 0 && (
              <Box sx={{ display: 'flex', gap: 0.75, alignItems: 'center', flexWrap: 'wrap' }}>
                <Button
                  size="small"
                  startIcon={<SelectAllIcon sx={{ fontSize: 14 }} />}
                  onClick={selectAll}
                  sx={{
                    textTransform: 'none',
                    fontSize: '0.75rem',
                    color: 'rgba(255,255,255,0.5)',
                    '&:hover': { color: '#fff' },
                    minWidth: 'auto',
                    px: 1,
                  }}
                >
                  All
                </Button>
                <Button
                  size="small"
                  startIcon={<DeselectIcon sx={{ fontSize: 14 }} />}
                  onClick={deselectAll}
                  sx={{
                    textTransform: 'none',
                    fontSize: '0.75rem',
                    color: 'rgba(255,255,255,0.5)',
                    '&:hover': { color: '#fff' },
                    minWidth: 'auto',
                    px: 1,
                  }}
                >
                  None
                </Button>
                <Box sx={{ width: 1, height: 16, bgcolor: 'rgba(255,255,255,0.1)', mx: 0.5 }} />
                <Button
                  size="small"
                  startIcon={<AlbumIcon sx={{ fontSize: 14 }} />}
                  onClick={selectAlbumsOnly}
                  sx={{
                    textTransform: 'none',
                    fontSize: '0.75rem',
                    color: 'rgba(52,211,153,0.7)',
                    '&:hover': { color: '#34D399' },
                    minWidth: 'auto',
                    px: 1,
                  }}
                >
                  Albums
                </Button>
                <Button
                  size="small"
                  startIcon={<AudiotrackIcon sx={{ fontSize: 14 }} />}
                  onClick={selectSinglesOnly}
                  sx={{
                    textTransform: 'none',
                    fontSize: '0.75rem',
                    color: 'rgba(251,146,60,0.7)',
                    '&:hover': { color: '#FB923C' },
                    minWidth: 'auto',
                    px: 1,
                  }}
                >
                  Singles
                </Button>

                <Button
                  variant="contained"
                  size="small"
                  disabled={selected.size === 0 || queuing}
                  onClick={handleQueueSelected}
                  startIcon={
                    queuing ? (
                      <CircularProgress size={12} sx={{ color: 'inherit' }} />
                    ) : (
                      <DownloadIcon sx={{ fontSize: 16 }} />
                    )
                  }
                  sx={{
                    textTransform: 'none',
                    fontWeight: 600,
                    fontSize: '0.8rem',
                    background: 'linear-gradient(135deg, #8B5CF6, #6D28D9)',
                    boxShadow: 'none',
                    borderRadius: 1.5,
                    '&:hover': { opacity: 0.9 },
                    '&.Mui-disabled': { bgcolor: '#252530', color: 'rgba(255,255,255,0.3)' },
                    ml: 0.5,
                  }}
                >
                  {queuing ? 'Queuing…' : `Queue ${selected.size}`}
                </Button>
              </Box>
            )}
          </Box>

          {/* Playlist grid / list */}
          {playlists.length === 0 ? (
            <Typography
              variant="body2"
              sx={{ color: 'rgba(255,255,255,0.3)', textAlign: 'center', py: 3 }}
            >
              This channel has no analyzeable playlists. The channel might be private or have no music content.
            </Typography>
          ) : (
            <Box
              sx={{
                maxHeight: 420,
                overflowY: 'auto',
                pr: 0.5,
                '&::-webkit-scrollbar': { width: 4 },
                '&::-webkit-scrollbar-thumb': {
                  bgcolor: 'rgba(139,92,246,0.3)',
                  borderRadius: 2,
                },
              }}
            >
              {playlists.map((p) => (
                <PlaylistRow
                  key={playlistKey(p)}
                  item={p}
                  checked={selected.has(playlistKey(p))}
                  onToggle={toggleOne}
                  onQueueOne={handleQueueOne}
                  queuing={queuing}
                />
              ))}
            </Box>
          )}
        </Box>
      )}
    </Box>
  );
}
