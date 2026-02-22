// frontend/src/components/DownloadList.js
import React from 'react';
import { Box, CircularProgress, Typography } from '@mui/material';
import DownloadItem from './DownloadItem';

export default function DownloadList({ downloads, loading, onCancel }) {
  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress size={28} sx={{ color: '#8B5CF6' }} />
      </Box>
    );
  }

  if (!downloads.length) {
    return (
      <Box sx={{ textAlign: 'center', py: 6 }}>
        <Typography sx={{ color: 'rgba(255,255,255,0.3)', fontSize: '0.875rem' }}>
          No downloads yet — add a URL above to get started
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      {downloads.map(d => (
        <DownloadItem key={d.id} download={d} onCancel={onCancel} />
      ))}
    </Box>
  );
}