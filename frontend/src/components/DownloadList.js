import React from 'react';
import {
  Box,
  List,
  CircularProgress,
  Typography,
} from '@mui/material';
import DownloadItem from './DownloadItem';

function DownloadList({ downloads, loading, onCancel }) {
  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress size={32} sx={{ color: 'var(--accent-primary)' }} />
      </Box>
    );
  }

  if (downloads.length === 0) {
    return (
      <Box sx={{ textAlign: 'center', py: 6 }}>
        <Typography variant="body1" sx={{ color: 'var(--text-secondary)', fontSize: '0.875rem' }}>
          No downloads yet
        </Typography>
        <Typography variant="body2" sx={{ color: 'var(--text-tertiary)', mt: 0.5, fontSize: '0.8125rem' }}>
          Add a YouTube Music URL above to get started
        </Typography>
      </Box>
    );
  }

  return (
    <List sx={{ width: '100%', p: 0 }}>
      {downloads.map((download, index) => (
        <Box key={download.id} sx={{ mb: index < downloads.length - 1 ? 1.5 : 0 }}>
          <DownloadItem download={download} onCancel={onCancel} />
        </Box>
      ))}
    </List>
  );
}

export default DownloadList;
