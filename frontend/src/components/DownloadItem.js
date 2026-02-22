// frontend/src/components/DownloadItem.js
import React, { memo } from 'react';
import {
  Box, Chip, IconButton, LinearProgress, Tooltip, Typography,
} from '@mui/material';
import AlbumIcon        from '@mui/icons-material/Album';
import AudiotrackIcon   from '@mui/icons-material/Audiotrack';
import CancelIcon       from '@mui/icons-material/Cancel';
import CheckCircleIcon  from '@mui/icons-material/CheckCircle';
import DownloadingIcon  from '@mui/icons-material/Downloading';
import ErrorIcon        from '@mui/icons-material/Error';
import PersonIcon       from '@mui/icons-material/Person';
import PlaylistPlayIcon from '@mui/icons-material/PlaylistPlay';
import QueueIcon        from '@mui/icons-material/Queue';

// ── Colour / icon mappings ────────────────────────────────────────────────────

const STATUS_COLOR = {
  queued:      '#F59E0B',
  downloading: '#8B5CF6',
  processing:  '#8B5CF6',
  completed:   '#10B981',
  failed:      '#EF4444',
  cancelled:   '#6B7280',
};

const STATUS_ICON = {
  queued:      <QueueIcon      sx={{ fontSize: 13 }} />,
  downloading: <DownloadingIcon sx={{ fontSize: 13 }} />,
  processing:  <DownloadingIcon sx={{ fontSize: 13 }} />,
  completed:   <CheckCircleIcon sx={{ fontSize: 13 }} />,
  failed:      <ErrorIcon       sx={{ fontSize: 13 }} />,
};

const TYPE_ICON = {
  album:    <AlbumIcon        sx={{ fontSize: 17, color: '#8B5CF6' }} />,
  artist:   <PersonIcon       sx={{ fontSize: 17, color: '#8B5CF6' }} />,
  playlist: <PlaylistPlayIcon sx={{ fontSize: 17, color: '#8B5CF6' }} />,
  song:     <AudiotrackIcon   sx={{ fontSize: 17, color: '#8B5CF6' }} />,
};

// ── Component ─────────────────────────────────────────────────────────────────

function DownloadItem({ download: d, onCancel }) {
  const canCancel = d.status === 'queued' || d.status === 'downloading';
  const color     = STATUS_COLOR[d.status] || '#8B5CF6';
  const isActive  = d.status === 'downloading' || d.status === 'processing';

  const trackLabel = d.total_tracks > 1
    ? `${d.done_tracks ?? 0}/${d.total_tracks} tracks`
    : null;

  const dateStr = d.created_at
    ? new Date(d.created_at).toLocaleString('en-AU', {
        month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
      })
    : '';

  return (
    <Box sx={{
      p: 1.5,
      borderRadius: 1.5,
      bgcolor: '#252530',
      border: '1px solid rgba(255,255,255,0.06)',
      transition: 'border-color .2s',
      '&:hover': { borderColor: 'rgba(255,255,255,0.12)' },
    }}>
      <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start' }}>

        {/* Type icon pill */}
        <Box sx={{
          width: 34, height: 34, minWidth: 34, borderRadius: 1,
          bgcolor: 'rgba(139,92,246,0.1)', border: '1px solid rgba(139,92,246,0.2)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          {TYPE_ICON[d.download_type] ?? TYPE_ICON.song}
        </Box>

        {/* Content */}
        <Box sx={{ flex: 1, minWidth: 0 }}>

          {/* Top row */}
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 1 }}>
            <Box sx={{ minWidth: 0 }}>
              <Typography
                noWrap
                sx={{ color: '#fff', fontWeight: 500, fontSize: '0.875rem', lineHeight: 1.4 }}
                title={d.title}
              >
                {d.title ?? 'Loading…'}
              </Typography>
              <Typography
                noWrap
                sx={{ color: 'rgba(255,255,255,0.4)', fontSize: '0.75rem', lineHeight: 1.3 }}
              >
                {d.artist}
                {d.album && d.album !== 'Unknown Album' ? ` · ${d.album}` : ''}
                {trackLabel ? ` · ${trackLabel}` : ''}
              </Typography>
            </Box>

            {/* Status chip + cancel */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <Chip
                icon={STATUS_ICON[d.status]}
                label={d.status.toUpperCase()}
                size="small"
                sx={{
                  height: 22, fontSize: '0.65rem', fontWeight: 700,
                  color: color,
                  bgcolor: `${color}18`,
                  border: `1px solid ${color}30`,
                  '& .MuiChip-icon': { color, ml: '4px' },
                }}
              />
              {canCancel && (
                <Tooltip title="Cancel">
                  <IconButton
                    size="small"
                    onClick={() => onCancel(d.id)}
                    sx={{
                      width: 26, height: 26, color: '#EF4444',
                      '&:hover': { bgcolor: 'rgba(239,68,68,0.1)' },
                    }}
                  >
                    <CancelIcon sx={{ fontSize: 16 }} />
                  </IconButton>
                </Tooltip>
              )}
            </Box>
          </Box>

          {/* Progress bar */}
          {isActive && (
            <Box sx={{ mt: 1 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography sx={{ fontSize: '0.65rem', color: 'rgba(255,255,255,0.3)' }}>
                  {d.status}
                </Typography>
                <Typography sx={{ fontSize: '0.65rem', color: '#8B5CF6', fontWeight: 600 }}>
                  {d.progress.toFixed(1)}%
                </Typography>
              </Box>
              <LinearProgress
                variant="determinate"
                value={d.progress}
                sx={{
                  height: 3, borderRadius: 2,
                  bgcolor: 'rgba(255,255,255,0.06)',
                  '& .MuiLinearProgress-bar': {
                    borderRadius: 2,
                    background: 'linear-gradient(90deg, #8B5CF6, #6D28D9)',
                  },
                }}
              />
            </Box>
          )}

          {/* Error message */}
          {d.error_message && (
            <Box sx={{
              mt: 1, p: 1, borderRadius: 1,
              bgcolor: 'rgba(239,68,68,0.08)',
              border: '1px solid rgba(239,68,68,0.2)',
            }}>
              <Typography sx={{ color: '#EF4444', fontSize: '0.72rem', lineHeight: 1.4 }}>
                {d.error_message}
              </Typography>
            </Box>
          )}

          {/* Footer meta */}
          <Box sx={{ display: 'flex', gap: 1.5, mt: 0.75, alignItems: 'center', flexWrap: 'wrap' }}>
            <Chip
              label={(d.download_type ?? 'song').toUpperCase()}
              size="small"
              sx={{
                height: 18, fontSize: '0.6rem', fontWeight: 500,
                color: 'rgba(255,255,255,0.35)',
                bgcolor: 'transparent',
                border: '1px solid rgba(255,255,255,0.12)',
                '& .MuiChip-label': { px: 0.75 },
              }}
            />
            <Typography sx={{ fontSize: '0.65rem', color: 'rgba(255,255,255,0.25)' }}>
              {dateStr}
            </Typography>
          </Box>
        </Box>
      </Box>
    </Box>
  );
}

export default memo(DownloadItem);