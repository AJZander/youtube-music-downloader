// frontend/src/components/Header.js
import React from 'react';
import { Box, Typography } from '@mui/material';
import MusicNoteIcon from '@mui/icons-material/MusicNote';

export default function Header() {
  return (
    <Box sx={{ textAlign: 'center', position: 'relative', py: 2 }}>

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