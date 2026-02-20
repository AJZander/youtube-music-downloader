import React from 'react';
import { Box, Typography } from '@mui/material';
import MusicNoteIcon from '@mui/icons-material/MusicNote';

function Header() {
  return (
    <Box
      sx={{
        textAlign: 'center',
        color: 'var(--text-primary)',
        py: 3,
      }}
    >
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          mb: 0.5,
          gap: 1.5,
        }}
      >
        <Box
          sx={{
            width: 40,
            height: 40,
            borderRadius: 1,
            background: 'var(--accent-gradient)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.3)',
          }}
        >
          <MusicNoteIcon sx={{ fontSize: 24, color: 'white' }} />
        </Box>
        <Typography 
          variant="h4" 
          component="h1" 
          sx={{ 
            fontWeight: 700,
            fontSize: { xs: '1.5rem', sm: '1.75rem' },
            background: 'var(--accent-gradient)',
            backgroundClip: 'text',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            letterSpacing: '-0.02em',
          }}
        >
          YouTube Music Downloader
        </Typography>
      </Box>
      <Typography 
        variant="body2" 
        sx={{ 
          color: 'var(--text-secondary)',
          fontSize: '0.8125rem',
          fontWeight: 400,
        }}
      >
        Download music from YouTube Music in high quality
      </Typography>
    </Box>
  );
}

export default Header;
