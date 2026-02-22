// frontend/src/components/DownloadForm.js
import React, { useState } from 'react';
import { Box, Button, InputAdornment, TextField } from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import LinkIcon      from '@mui/icons-material/Link';

export default function DownloadForm({ onSubmit }) {
  const [url,     setUrl]     = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async e => {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    setLoading(true);
    try {
      await onSubmit(trimmed);
      setUrl('');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box
      component="form"
      onSubmit={handleSubmit}
      sx={{ display: 'flex', gap: 1.5, alignItems: 'center' }}
    >
      <TextField
        fullWidth
        placeholder="Paste a YouTube Music URL (song, album, playlist, artist)…"
        value={url}
        onChange={e => setUrl(e.target.value)}
        disabled={loading}
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
            '&::placeholder': { color: 'rgba(255,255,255,0.3)', opacity: 1 },
          },
        }}
      />

      <Button
        type="submit"
        variant="contained"
        disabled={loading || !url.trim()}
        startIcon={<DownloadIcon sx={{ fontSize: 18 }} />}
        sx={{
          minWidth: 120,
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
        {loading ? 'Queuing…' : 'Download'}
      </Button>
    </Box>
  );
}