// frontend/src/components/Header.js
import React from 'react';
import { Box, Divider, Tooltip, Typography } from '@mui/material';
import CheckCircleIcon  from '@mui/icons-material/CheckCircle';
import DownloadingIcon  from '@mui/icons-material/Downloading';
import ErrorIcon        from '@mui/icons-material/Error';
import MusicNoteIcon    from '@mui/icons-material/MusicNote';
import QueueIcon        from '@mui/icons-material/Queue';

// ── Stat pill ─────────────────────────────────────────────────────────────────

function StatPill({ icon, label, value, color, tooltip }) {
  return (
    <Tooltip title={tooltip} placement="bottom" arrow>
      <Box sx={{
        display: 'flex', alignItems: 'center', gap: 0.6,
        px: 1.25, py: 0.5, borderRadius: 2,
        bgcolor: `${color}12`,
        border: `1px solid ${color}28`,
        cursor: 'default',
        userSelect: 'none',
      }}>
        <Box sx={{ color, display: 'flex', alignItems: 'center' }}>
          {React.cloneElement(icon, { sx: { fontSize: 13 } })}
        </Box>
        <Typography sx={{ fontSize: '0.72rem', fontWeight: 700, color, lineHeight: 1 }}>
          {value}
        </Typography>
        <Typography sx={{ fontSize: '0.65rem', color: `${color}99`, lineHeight: 1 }}>
          {label}
        </Typography>
      </Box>
    </Tooltip>
  );
}

// ── Header ────────────────────────────────────────────────────────────────────

export default function Header({ stats }) {
  const active    = (stats?.downloading ?? 0) + (stats?.processing ?? 0);
  const queued    = stats?.queued    ?? 0;
  const completed = stats?.completed ?? 0;
  const failed    = stats?.failed    ?? 0;
  const total     = stats?.total     ?? 0;

  return (
    <Box sx={{ py: { xs: 1, sm: 2 } }}>
      {/* Logo row */}
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: { xs: 1, sm: 1.5 }, mb: 0.5 }}>
        <Box sx={{
          width: { xs: 32, sm: 40 },
          height: { xs: 32, sm: 40 },
          borderRadius: 1.5,
          background: 'linear-gradient(135deg, #8B5CF6, #6D28D9)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '0 0 20px rgba(139,92,246,0.35)',
        }}>
          <MusicNoteIcon sx={{ fontSize: { xs: 20, sm: 24 }, color: '#fff' }} />
        </Box>
        <Typography
          variant="h5"
          sx={{
            fontWeight: 700,
            fontSize: { xs: '1.1rem', sm: '1.5rem' },
            background: 'linear-gradient(135deg, #8B5CF6, #A78BFA)',
            backgroundClip: 'text',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
          }}
        >
          YouTube Music Downloader
        </Typography>
      </Box>

      <Typography variant="body2" sx={{ color: 'rgba(255,255,255,0.35)', fontSize: { xs: '0.7rem', sm: '0.78rem' }, textAlign: 'center', mb: { xs: 1, sm: 1.5 } }}>
        Songs · Albums · Playlists · Channels — high-quality audio
      </Typography>

      {/* Stats bar */}
      {total > 0 && (
        <Box sx={{ display: 'flex', justifyContent: 'center', flexWrap: 'wrap', gap: 1 }}>
          {active > 0 && (
            <StatPill icon={<DownloadingIcon />} label="active"    value={active}    color="#8B5CF6" tooltip="Currently downloading or processing" />
          )}
          {queued > 0 && (
            <StatPill icon={<QueueIcon />}       label="queued"    value={queued}    color="#F59E0B" tooltip="Waiting in queue" />
          )}
          <StatPill   icon={<CheckCircleIcon />}  label="done"      value={completed} color="#10B981" tooltip="Completed downloads" />
          {failed > 0 && (
            <StatPill icon={<ErrorIcon />}        label="failed"    value={failed}    color="#EF4444" tooltip="Failed — use Retry to re-queue" />
          )}
        </Box>
      )}
    </Box>
  );
}

