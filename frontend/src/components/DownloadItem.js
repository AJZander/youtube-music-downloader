// frontend/src/components/DownloadItem.js
import React, { memo, useState } from 'react';
import {
  Box, Chip, Collapse, IconButton, LinearProgress,
  Tooltip, Typography,
} from '@mui/material';
import AlbumIcon        from '@mui/icons-material/Album';
import AudiotrackIcon   from '@mui/icons-material/Audiotrack';
import CancelIcon       from '@mui/icons-material/Cancel';
import CheckCircleIcon  from '@mui/icons-material/CheckCircle';
import ContentCopyIcon  from '@mui/icons-material/ContentCopy';
import DownloadingIcon  from '@mui/icons-material/Downloading';
import ErrorIcon        from '@mui/icons-material/Error';
import ExpandMoreIcon   from '@mui/icons-material/ExpandMore';
import PersonIcon       from '@mui/icons-material/Person';
import PlaylistPlayIcon from '@mui/icons-material/PlaylistPlay';
import QueueIcon        from '@mui/icons-material/Queue';
import RefreshIcon      from '@mui/icons-material/Refresh';

// ── Colour / icon mappings ────────────────────────────────────────────────────

const STATUS = {
  queued:      { color: '#F59E0B', label: 'Queued',      icon: <QueueIcon       sx={{ fontSize: 12 }} /> },
  downloading: { color: '#8B5CF6', label: 'Downloading', icon: <DownloadingIcon sx={{ fontSize: 12 }} /> },
  processing:  { color: '#8B5CF6', label: 'Processing',  icon: <DownloadingIcon sx={{ fontSize: 12 }} /> },
  completed:   { color: '#10B981', label: 'Completed',   icon: <CheckCircleIcon sx={{ fontSize: 12 }} /> },
  failed:      { color: '#EF4444', label: 'Failed',      icon: <ErrorIcon       sx={{ fontSize: 12 }} /> },
  cancelled:   { color: '#6B7280', label: 'Cancelled',   icon: null },
};

const TYPE_ICON = {
  album:    <AlbumIcon        sx={{ fontSize: 18, color: '#A78BFA' }} />,
  artist:   <PersonIcon       sx={{ fontSize: 18, color: '#A78BFA' }} />,
  playlist: <PlaylistPlayIcon sx={{ fontSize: 18, color: '#A78BFA' }} />,
  song:     <AudiotrackIcon   sx={{ fontSize: 18, color: '#A78BFA' }} />,
};

// ── Component ─────────────────────────────────────────────────────────────────

function DownloadItem({ download: d, onCancel, onRetry }) {
  const [copied,   setCopied]   = useState(false);
  const [errOpen,  setErrOpen]  = useState(false);

  const canCancel  = d.status === 'queued' || d.status === 'downloading';
  const canRetry   = d.status === 'failed' || d.status === 'cancelled';
  const isActive   = d.status === 'downloading' || d.status === 'processing';
  const hasError   = !!d.error_message;

  const meta       = STATUS[d.status] || STATUS.queued;
  const color      = meta.color;

  const trackLabel = d.total_tracks > 1
    ? `${d.done_tracks ?? 0} / ${d.total_tracks} tracks`
    : null;

  const dateStr = d.created_at
    ? new Date(d.created_at).toLocaleString('en-AU', {
        day: 'numeric', month: 'short',
        hour: '2-digit', minute: '2-digit',
      })
    : '';

  const copyUrl = () => {
    navigator.clipboard.writeText(d.url).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <Box sx={{
      borderRadius: 1.5,
      bgcolor: '#1e1e2a',
      border: '1px solid rgba(255,255,255,0.06)',
      overflow: 'hidden',
      transition: 'border-color .15s',
      '&:hover': { borderColor: 'rgba(255,255,255,0.11)' },
    }}>
      {/* Active download: top progress stripe */}
      {isActive && (
        <LinearProgress
          variant="determinate"
          value={d.progress}
          sx={{
            height: 2,
            bgcolor: 'rgba(139,92,246,0.12)',
            '& .MuiLinearProgress-bar': {
              background: 'linear-gradient(90deg,#8B5CF6,#6D28D9)',
            },
          }}
        />
      )}

      <Box sx={{ p: 1.5, display: 'flex', gap: 1.5, alignItems: 'flex-start' }}>

        {/* Type icon */}
        <Box sx={{
          width: 36, height: 36, minWidth: 36, borderRadius: 1,
          bgcolor: 'rgba(139,92,246,0.1)',
          border: '1px solid rgba(139,92,246,0.18)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          mt: 0.25,
        }}>
          {TYPE_ICON[d.download_type] ?? TYPE_ICON.song}
        </Box>

        {/* Body */}
        <Box sx={{ flex: 1, minWidth: 0 }}>

          {/* Title row */}
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 1 }}>

            {/* Left: title + artist */}
            <Box sx={{ minWidth: 0 }}>
              <Typography
                noWrap
                title={d.title}
                sx={{ color: '#fff', fontWeight: 600, fontSize: '0.875rem', lineHeight: 1.35 }}
              >
                {d.title ?? 'Loading…'}
              </Typography>
              <Typography noWrap sx={{ color: 'rgba(255,255,255,0.45)', fontSize: '0.75rem', lineHeight: 1.3, mt: 0.15 }}>
                {[d.artist, d.album && d.album !== 'Unknown Album' ? d.album : null]
                  .filter(Boolean).join(' · ')}
                {trackLabel ? ` · ${trackLabel}` : ''}
              </Typography>
            </Box>

            {/* Right: status + actions */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexShrink: 0 }}>
              {/* Status chip */}
              <Chip
                icon={meta.icon ?? undefined}
                label={meta.label}
                size="small"
                sx={{
                  height: 22, fontSize: '0.65rem', fontWeight: 700,
                  color, bgcolor: `${color}18`, border: `1px solid ${color}30`,
                  '& .MuiChip-icon': { color, ml: '4px' },
                  '& .MuiChip-label': { px: meta.icon ? '6px' : '8px' },
                }}
              />

              {/* Progress % when active */}
              {isActive && (
                <Typography sx={{ fontSize: '0.7rem', color: '#8B5CF6', fontWeight: 700, minWidth: 36, textAlign: 'right' }}>
                  {d.progress.toFixed(1)}%
                </Typography>
              )}

              {/* Copy URL */}
              <Tooltip title={copied ? 'Copied!' : 'Copy URL'}>
                <IconButton size="small" onClick={copyUrl}
                  sx={{ width: 26, height: 26, color: copied ? '#10B981' : 'rgba(255,255,255,0.25)',
                        '&:hover': { color: '#fff', bgcolor: 'rgba(255,255,255,0.06)' } }}>
                  <ContentCopyIcon sx={{ fontSize: 13 }} />
                </IconButton>
              </Tooltip>

              {/* Retry */}
              {canRetry && (
                <Tooltip title="Retry">
                  <IconButton size="small" onClick={() => onRetry(d.id)}
                    sx={{ width: 26, height: 26, color: '#F59E0B',
                          '&:hover': { bgcolor: 'rgba(245,158,11,0.1)' } }}>
                    <RefreshIcon sx={{ fontSize: 15 }} />
                  </IconButton>
                </Tooltip>
              )}

              {/* Cancel */}
              {canCancel && (
                <Tooltip title="Cancel">
                  <IconButton size="small" onClick={() => onCancel(d.id)}
                    sx={{ width: 26, height: 26, color: '#EF4444',
                          '&:hover': { bgcolor: 'rgba(239,68,68,0.1)' } }}>
                    <CancelIcon sx={{ fontSize: 15 }} />
                  </IconButton>
                </Tooltip>
              )}

              {/* Error expand */}
              {hasError && (
                <Tooltip title={errOpen ? 'Hide error' : 'Show error'}>
                  <IconButton size="small" onClick={() => setErrOpen(o => !o)}
                    sx={{ width: 26, height: 26, color: '#EF4444',
                          transform: errOpen ? 'rotate(180deg)' : 'none',
                          transition: 'transform .2s',
                          '&:hover': { bgcolor: 'rgba(239,68,68,0.1)' } }}>
                    <ExpandMoreIcon sx={{ fontSize: 15 }} />
                  </IconButton>
                </Tooltip>
              )}
            </Box>
          </Box>

          {/* Progress bar (active) */}
          {isActive && (
            <Box sx={{ mt: 0.75 }}>
              <LinearProgress
                variant="determinate"
                value={d.progress}
                sx={{
                  height: 4, borderRadius: 2,
                  bgcolor: 'rgba(255,255,255,0.06)',
                  '& .MuiLinearProgress-bar': {
                    borderRadius: 2,
                    background: 'linear-gradient(90deg,#8B5CF6,#6D28D9)',
                  },
                }}
              />
            </Box>
          )}

          {/* Expandable error */}
          <Collapse in={errOpen && hasError}>
            <Box sx={{
              mt: 1, p: 1, borderRadius: 1,
              bgcolor: 'rgba(239,68,68,0.07)',
              border: '1px solid rgba(239,68,68,0.18)',
            }}>
              <Typography sx={{ color: '#F87171', fontSize: '0.72rem', lineHeight: 1.5, wordBreak: 'break-word' }}>
                {d.error_message}
              </Typography>
            </Box>
          </Collapse>

          {/* Footer meta */}
          <Box sx={{ display: 'flex', gap: 1.5, mt: 0.75, alignItems: 'center', flexWrap: 'wrap' }}>
            <Chip
              label={(d.download_type ?? 'song').toUpperCase()}
              size="small"
              sx={{
                height: 17, fontSize: '0.58rem', fontWeight: 500,
                color: 'rgba(255,255,255,0.3)',
                bgcolor: 'transparent',
                border: '1px solid rgba(255,255,255,0.1)',
                '& .MuiChip-label': { px: 0.75 },
              }}
            />
            {d.format_id && d.format_id !== 'bestaudio/best' && (
              <Chip
                label={d.format_id}
                size="small"
                sx={{
                  height: 17, fontSize: '0.58rem',
                  color: 'rgba(139,92,246,0.6)',
                  bgcolor: 'transparent',
                  border: '1px solid rgba(139,92,246,0.2)',
                  '& .MuiChip-label': { px: 0.75 },
                }}
              />
            )}
            <Typography sx={{ fontSize: '0.63rem', color: 'rgba(255,255,255,0.2)', ml: 'auto' }}>
              {dateStr}
            </Typography>
          </Box>
        </Box>
      </Box>
    </Box>
  );
}

export default memo(DownloadItem);
