import React, { useState } from 'react';
import { 
  TextField, 
  Button, 
  Box, 
  InputAdornment
} from '@mui/material';
import LinkIcon from '@mui/icons-material/Link';
import DownloadIcon from '@mui/icons-material/Download';

function DownloadForm({ onSubmit }) {
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!url.trim()) return;

    setLoading(true);
    try {
      await onSubmit(url);
      setUrl('');
    } catch (error) {
      console.error('Submit error:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box component="form" onSubmit={handleSubmit} sx={{ display: 'flex', gap: 1.5, alignItems: 'stretch' }}>
      <TextField
        fullWidth
        variant="outlined"
        placeholder="Paste YouTube Music URL (song, album, or playlist)"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        disabled={loading}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <LinkIcon sx={{ fontSize: 18, color: 'var(--text-tertiary)' }} />
            </InputAdornment>
          ),
          sx: {
            fontSize: '0.8125rem',
            height: 40,
          },
        }}
        sx={{
          flex: 1,
          '& .MuiOutlinedInput-root': {
            background: 'var(--bg-tertiary)',
            borderRadius: 1,
            '& fieldset': {
              borderColor: 'var(--border-subtle)',
            },
            '&:hover fieldset': {
              borderColor: 'var(--border-medium)',
            },
            '&.Mui-focused fieldset': {
              borderColor: 'var(--accent-primary)',
              borderWidth: '1px',
            },
          },
          '& .MuiInputBase-input': {
            color: 'var(--text-primary)',
            padding: '10px 12px',
            '&::placeholder': {
              color: 'var(--text-tertiary)',
              opacity: 1,
            },
          },
        }}
      />
      <Button
        type="submit"
        variant="contained"
        disabled={loading || !url.trim()}
        startIcon={<DownloadIcon sx={{ fontSize: 18 }} />}
        sx={{
          minWidth: 110,
          height: 40,
          px: 2,
          fontSize: '0.8125rem',
          fontWeight: 600,
          textTransform: 'none',
          background: 'var(--accent-gradient)',
          borderRadius: 1,
          border: 'none',
          color: 'white',
          boxShadow: 'none',
          transition: 'all 0.2s',
          '&:hover': {
            background: 'var(--accent-gradient)',
            opacity: 0.9,
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.4)',
          },
          '&.Mui-disabled': {
            background: 'var(--bg-elevated)',
            color: 'var(--text-tertiary)',
          },
        }}
      >
        {loading ? 'Adding...' : 'Download'}
      </Button>
    </Box>
  );
}

export default DownloadForm;
