// frontend/src/components/Header.js
import React from 'react';
import { Box, IconButton, Tooltip, Typography } from '@mui/material';
import MusicNoteIcon from '@mui/icons-material/MusicNote';
import VpnKeyIcon    from '@mui/icons-material/VpnKey';

export default function Header({ onAuthClick }) {
  return (
    <Box sx={{ textAlign: 'center', position: 'relative', py: 2 }}>

      {/* Auth button — top-right */}
      <Tooltip title="YouTube authentication (age-restricted content)">
        <IconButton
          onClick={onAuthClick}
          sx={{
            position: 'absolute', top: 8, right: 0,
            color: 'rgba(255,255,255,0.4)',
            '&:hover': { color: '#8B5CF6', bgcolor: 'rgba(139,92,246,0.1)' },
          }}
        >
          <VpnKeyIcon />
        </IconButton>
      </Tooltip>

      {/* Logo + title */}
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 1.5, mb: 0.5 }}>
        <Box sx={{
          width: 40, height: 40, borderRadius: 1.5,
          background: 'linear-gradient(135deg, #8B5CF6, #6D28D9)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <MusicNoteIcon sx={{ fontSize: 24, color: '#fff' }} />
        </Box>

        <Typography
          variant="h5"
          sx={{
            fontWeight: 700,
            background: 'linear-gradient(135deg, #8B5CF6, #A78BFA)',
            backgroundClip: 'text',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
          }}
        >
          YouTube Music Downloader
        </Typography>
      </Box>

      <Typography variant="body2" sx={{ color: 'rgba(255,255,255,0.4)', fontSize: '0.8rem' }}>
        Songs · Albums · Playlists — high-quality MP3
      </Typography>
    </Box>
  );
}