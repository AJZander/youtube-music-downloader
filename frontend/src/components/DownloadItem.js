import React from 'react';
import {
  Box,
  Typography,
  LinearProgress,
  Chip,
  IconButton,
} from '@mui/material';
import CancelIcon from '@mui/icons-material/Cancel';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import QueueIcon from '@mui/icons-material/Queue';
import DownloadingIcon from '@mui/icons-material/Downloading';
import AlbumIcon from '@mui/icons-material/Album';
import AudiotrackIcon from '@mui/icons-material/Audiotrack';
import PersonIcon from '@mui/icons-material/Person';
import PlaylistPlayIcon from '@mui/icons-material/PlaylistPlay';

const getStatusColor = (status) => {
  switch (status) {
    case 'completed':
      return '#10B981';
    case 'failed':
      return '#EF4444';
    case 'downloading':
      return 'var(--accent-primary)';
    case 'queued':
      return '#F59E0B';
    case 'cancelled':
      return '#6B7280';
    default:
      return 'var(--accent-primary)';
  }
};

const getStatusIcon = (status) => {
  const iconStyle = { fontSize: 14 };
  switch (status) {
    case 'completed':
      return <CheckCircleIcon sx={iconStyle} />;
    case 'failed':
      return <ErrorIcon sx={iconStyle} />;
    case 'downloading':
      return <DownloadingIcon sx={iconStyle} />;
    case 'queued':
      return <QueueIcon sx={iconStyle} />;
    default:
      return null;
  }
};

const getTypeIcon = (type) => {
  const iconStyle = { fontSize: 16, color: 'var(--accent-primary)' };
  switch (type) {
    case 'album':
      return <AlbumIcon sx={iconStyle} />;
    case 'artist':
      return <PersonIcon sx={iconStyle} />;
    case 'playlist':
      return <PlaylistPlayIcon sx={iconStyle} />;
    case 'song':
    default:
      return <AudiotrackIcon sx={iconStyle} />;
  }
};

function DownloadItem({ download, onCancel }) {
  const canCancel = download.status === 'queued' || download.status === 'downloading';

  const formatDate = (dateString) => {
    if (!dateString) return '';
    const date = new Date(dateString);
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <Box
      sx={{
        background: 'var(--bg-tertiary)',
        border: '1px solid var(--border-subtle)',
        borderRadius: 1,
        p: 1.5,
        transition: 'all 0.2s',
        '&:hover': {
          borderColor: 'var(--border-medium)',
          background: 'var(--bg-elevated)',
        },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.5 }}>
        {/* Type Icon */}
        <Box
          sx={{
            minWidth: 32,
            width: 32,
            height: 32,
            borderRadius: 0.75,
            background: 'rgba(139, 92, 246, 0.1)',
            border: '1px solid rgba(139, 92, 246, 0.2)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            mt: 0.25,
          }}
        >
          {getTypeIcon(download.type)}
        </Box>

        {/* Content */}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {/* Title and Status Row */}
          <Box
            sx={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'flex-start',
              gap: 1.5,
              mb: 0.5,
            }}
          >
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography
                variant="body1"
                noWrap
                sx={{
                  color: 'var(--text-primary)',
                  fontSize: '0.875rem',
                  fontWeight: 500,
                  mb: 0.25,
                  lineHeight: 1.4,
                }}
                title={download.title}
              >
                {download.title}
              </Typography>
              <Typography
                variant="body2"
                noWrap
                sx={{
                  color: 'var(--text-secondary)',
                  fontSize: '0.75rem',
                  lineHeight: 1.3,
                }}
              >
                {download.artist}
                {download.album !== 'Unknown Album' && ` • ${download.album}`}
              </Typography>
            </Box>

            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <Chip
                icon={getStatusIcon(download.status)}
                label={download.status.toUpperCase()}
                size="small"
                sx={{
                  height: 22,
                  fontSize: '0.6875rem',
                  fontWeight: 600,
                  letterSpacing: '0.02em',
                  color: getStatusColor(download.status),
                  background: `${getStatusColor(download.status)}15`,
                  border: `1px solid ${getStatusColor(download.status)}30`,
                  '& .MuiChip-icon': {
                    color: getStatusColor(download.status),
                    marginLeft: '4px',
                  },
                }}
              />
              {canCancel && (
                <IconButton
                  onClick={() => onCancel(download.id)}
                  size="small"
                  sx={{
                    width: 26,
                    height: 26,
                    color: '#EF4444',
                    '&:hover': {
                      background: 'rgba(239, 68, 68, 0.1)',
                    },
                  }}
                >
                  <CancelIcon sx={{ fontSize: 16 }} />
                </IconButton>
              )}
            </Box>
          </Box>

          {/* Progress Bar */}
          {(download.status === 'downloading' || download.status === 'queued') && (
            <Box sx={{ mt: 1 }}>
              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  mb: 0.5,
                }}
              >
                <Typography variant="caption" sx={{ color: 'var(--text-tertiary)', fontSize: '0.6875rem' }}>
                  {download.status === 'queued' ? 'Queued' : 'Downloading'}
                </Typography>
                <Typography
                  variant="caption"
                  sx={{
                    color: 'var(--accent-primary)',
                    fontSize: '0.6875rem',
                    fontWeight: 600,
                  }}
                >
                  {download.progress.toFixed(1)}%
                </Typography>
              </Box>
              <LinearProgress
                variant="determinate"
                value={download.progress}
                sx={{
                  height: 4,
                  borderRadius: 2,
                  background: 'rgba(255, 255, 255, 0.05)',
                  '& .MuiLinearProgress-bar': {
                    borderRadius: 2,
                    background: 'var(--accent-gradient)',
                  },
                }}
              />
            </Box>
          )}

          {/* Error Message */}
          {download.error_message && (
            <Typography
              variant="body2"
              sx={{
                mt: 1,
                p: 1,
                borderRadius: 0.75,
                background: 'rgba(239, 68, 68, 0.1)',
                border: '1px solid rgba(239, 68, 68, 0.2)',
                color: '#EF4444',
                fontSize: '0.75rem',
                lineHeight: 1.4,
              }}
            >
              {download.error_message}
            </Typography>
          )}

          {/* Metadata Row */}
          <Box
            sx={{
              display: 'flex',
              gap: 1.5,
              mt: 1,
              flexWrap: 'wrap',
              alignItems: 'center',
            }}
          >
            <Chip
              label={download.type.toUpperCase()}
              size="small"
              sx={{
                height: 18,
                fontSize: '0.6875rem',
                fontWeight: 500,
                color: 'var(--text-tertiary)',
                background: 'transparent',
                border: '1px solid var(--border-medium)',
                '& .MuiChip-label': {
                  px: 0.75,
                },
              }}
            />
            <Typography variant="caption" sx={{ color: 'var(--text-tertiary)', fontSize: '0.6875rem' }}>
              {formatDate(download.created_at)}
            </Typography>
            {download.status === 'completed' && download.updated_at && (
              <Typography variant="caption" sx={{ color: '#10B981', fontSize: '0.6875rem' }}>
                ✓ {formatDate(download.updated_at)}
              </Typography>
            )}
          </Box>
        </Box>
      </Box>
    </Box>
  );
}

export default DownloadItem;
