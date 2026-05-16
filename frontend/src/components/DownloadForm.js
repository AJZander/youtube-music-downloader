// frontend/src/components/DownloadForm.js
import React, { useState, useCallback } from 'react';
import { Box, Button, Chip, InputAdornment, TextField, Typography, alpha } from '@mui/material';
import DownloadIcon    from '@mui/icons-material/Download';
import LinkIcon        from '@mui/icons-material/Link';
import PlaylistAddIcon from '@mui/icons-material/PlaylistAdd';

// Returns true if the string looks like a YouTube / YouTube Music URL
function isYouTubeUrl(str) {
  return /^https?:\/\/(music\.|www\.|m\.)?youtube\.com\/|^https?:\/\/youtu\.be\//i.test(str.trim());
}

export default function DownloadForm({ onSubmit, onBulkSubmit }) {
  const [url,     setUrl]     = useState('');
  const [loading, setLoading] = useState(false);

  const lines   = url.split('\n').map(l => l.trim()).filter(Boolean);
  const isBulk  = lines.length > 1;
  const validUrls = lines.filter(isYouTubeUrl);

  const handleChange = useCallback((e) => {
    setUrl(e.target.value);
  }, []);

  const handleSubmit = async e => {
    e.preventDefault();
    if (loading) return;

    if (isBulk) {
      if (validUrls.length === 0) return;
      setLoading(true);
      try {
        await onBulkSubmit(validUrls);
        setUrl('');
      } finally {
        setLoading(false);
      }
    } else {
      const trimmed = url.trim();
      if (!trimmed) return;
      setLoading(true);
      try {
        await onSubmit(trimmed);
        setUrl('');
      } finally {
        setLoading(false);
      }
    }
  };

  const textFieldSx = {
    '& .MuiOutlinedInput-root': {
      bgcolor: '#252530',
      alignItems: isBulk ? 'flex-start' : 'center',
      '& fieldset': { borderColor: 'rgba(255,255,255,0.1)' },
      '&:hover fieldset': { borderColor: 'rgba(255,255,255,0.2)' },
      '&.Mui-focused fieldset': { borderColor: '#8B5CF6', borderWidth: 1 },
    },
    '& input, & textarea': {
      color: '#fff',
      fontSize: { xs: '0.875rem', sm: '1rem' },
      '&::placeholder': { color: 'rgba(255,255,255,0.3)', opacity: 1 },
    },
  };

  return (
    <Box
      component="form"
      onSubmit={handleSubmit}
      sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}
    >
      <Box sx={{ display: 'flex', gap: { xs: 1, sm: 1.5 }, alignItems: 'flex-start', flexDirection: { xs: 'column', sm: 'row' } }}>
        <TextField
          fullWidth
          placeholder={isBulk ? '' : 'Paste a YouTube Music URL (song, album, playlist, artist)…'}
          value={url}
          onChange={handleChange}
          disabled={loading}
          size="small"
          multiline={isBulk}
          minRows={isBulk ? 3 : 1}
          maxRows={10}
          InputProps={{
            startAdornment: !isBulk ? (
              <InputAdornment position="start">
                <LinkIcon sx={{ fontSize: 18, color: 'rgba(255,255,255,0.3)' }} />
              </InputAdornment>
            ) : null,
          }}
          sx={textFieldSx}
        />

        <Button
          type="submit"
          variant="contained"
          disabled={loading || (isBulk ? validUrls.length === 0 : !url.trim())}
          startIcon={isBulk ? <PlaylistAddIcon sx={{ fontSize: 18 }} /> : <DownloadIcon sx={{ fontSize: 18 }} />}
          sx={{
            minWidth: { xs: '100%', sm: 140 },
            height: 40,
            textTransform: 'none',
            fontWeight: 600,
            fontSize: '0.875rem',
            background: 'linear-gradient(135deg, #8B5CF6, #6D28D9)',
            boxShadow: 'none',
            borderRadius: 1.5,
            whiteSpace: 'nowrap',
            flexShrink: 0,
            '&:hover': { opacity: 0.9, boxShadow: '0 4px 16px rgba(109,40,217,0.4)' },
            '&.Mui-disabled': { bgcolor: '#252530', color: 'rgba(255,255,255,0.3)' },
          }}
        >
          {loading
            ? (isBulk ? 'Queueing…' : 'Queuing…')
            : (isBulk ? `Queue ${validUrls.length}` : 'Download')}
        </Button>
      </Box>

      {/* Bulk mode info bar */}
      {isBulk && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          <Chip
            label={`${validUrls.length} valid URL${validUrls.length !== 1 ? 's' : ''}`}
            size="small"
            color="primary"
            variant="outlined"
          />
          {lines.length !== validUrls.length && (
            <Chip
              label={`${lines.length - validUrls.length} invalid (skipped)`}
              size="small"
              color="warning"
              variant="outlined"
            />
          )}
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            Bulk mode — will use best audio quality for all
          </Typography>
        </Box>
      )}
    </Box>
  );
}
