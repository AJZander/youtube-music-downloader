import React from 'react';
import { Box, Typography, IconButton, Tooltip } from '@mui/material';
import MusicNoteIcon from '@mui/icons-material/MusicNote';
import VpnKeyIcon from '@mui/icons-material/VpnKey';

function Header({ onAuthClick }) {
  return (
    <Box
      sx={{
        textAlign: 'center',
        color: 'var(--text-primary)',
        py: 3,
        position: 'relative',
      }}
    >
      {/* Auth button in top right */}
      <Box
        sx={{
          position: 'absolute',
          top: 16,
          right: 0,
        }}
      >
        <Tooltip title="YouTube Authentication (for age-restricted content)">
          <IconButton
            onClick={onAuthClick}
            sx={{
              color: 'var(--text-secondary)',
              '&:hover': {
                color: 'var(--accent-primary)',
                background: 'rgba(139, 92, 246, 0.1)',
              },
            }}
          >
            <VpnKeyIcon />
          </IconButton>
        </Tooltip>
      </Box>

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
