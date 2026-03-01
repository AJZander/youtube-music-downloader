// frontend/src/components/BatchStatusViewer.js
import React, { useEffect, useState } from 'react';
import {
  Box,
  Card,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  LinearProgress,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import QueueMusicIcon from '@mui/icons-material/QueueMusic';
import SkipNextIcon from '@mui/icons-material/SkipNext';

export default function BatchStatusViewer({ batches, onRemove }) {
  const [expanded, setExpanded] = useState({});

  // Auto-expand processing batches, collapse completed ones
  useEffect(() => {
    const newExpanded = {};
    batches.forEach(batch => {
      if (batch.status === 'processing' && expanded[batch.id] === undefined) {
        newExpanded[batch.id] = true;
      } else if (batch.status === 'completed' && expanded[batch.id] === undefined) {
        newExpanded[batch.id] = false;
      } else {
        newExpanded[batch.id] = expanded[batch.id] ?? true;
      }
    });
    setExpanded(newExpanded);
  }, [batches]);

  const toggleExpand = (batchId) => {
    setExpanded(prev => ({ ...prev, [batchId]: !prev[batchId] }));
  };

  if (batches.length === 0) return null;

  return (
    <Box sx={{ mb: 2 }}>
      {batches.map((batch) => (
        <BatchCard
          key={batch.id}
          batch={batch}
          expanded={expanded[batch.id]}
          onToggle={() => toggleExpand(batch.id)}
          onRemove={onRemove}
        />
      ))}
    </Box>
  );
}

function BatchCard({ batch, expanded, onToggle, onRemove }) {
  const progress = batch.total > 0 
    ? ((batch.queued + batch.skipped + batch.failed) / batch.total) * 100 
    : 0;
  
  const isComplete = batch.status === 'completed';
  const isProcessing = batch.status === 'processing';

  // Auto-remove completed batches after 10 seconds
  useEffect(() => {
    if (isComplete) {
      const timer = setTimeout(() => {
        onRemove(batch.id);
      }, 10000);
      return () => clearTimeout(timer);
    }
  }, [isComplete, batch.id, onRemove]);

  return (
    <Card
      sx={{
        mb: 1.5,
        bgcolor: '#1a1a24',
        border: '1px solid rgba(139,92,246,0.2)',
        borderRadius: 2,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1.5,
          p: 1.5,
          cursor: 'pointer',
          '&:hover': { bgcolor: 'rgba(139,92,246,0.05)' },
        }}
        onClick={onToggle}
      >
        {/* Status Icon */}
        {isProcessing ? (
          <CircularProgress size={20} sx={{ color: '#8B5CF6' }} />
        ) : (
          <CheckCircleIcon sx={{ color: '#34D399', fontSize: 20 }} />
        )}

        {/* Info */}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
            <Typography variant="body2" sx={{ color: '#fff', fontWeight: 500, fontSize: '0.875rem' }}>
              Batch Processing
            </Typography>
            <Chip
              label={isComplete ? 'Completed' : 'Processing'}
              size="small"
              sx={{
                height: 18,
                fontSize: '0.65rem',
                bgcolor: isComplete ? 'rgba(52,211,153,0.15)' : 'rgba(139,92,246,0.15)',
                color: isComplete ? '#34D399' : '#A78BFA',
                border: isComplete ? '1px solid rgba(52,211,153,0.25)' : '1px solid rgba(139,92,246,0.25)',
              }}
            />
          </Box>

          {/* Progress Stats */}
          <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
            <StatChip icon={<QueueMusicIcon />} label={batch.queued} color="#8B5CF6" />
            {batch.skipped > 0 && (
              <StatChip icon={<SkipNextIcon />} label={batch.skipped} color="#F59E0B" />
            )}
            {batch.failed > 0 && (
              <StatChip icon={<ErrorIcon />} label={batch.failed} color="#EF4444" />
            )}
            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.4)', fontSize: '0.7rem' }}>
              {batch.queued + batch.skipped + batch.failed} / {batch.total}
            </Typography>
          </Box>
        </Box>

        {/* Expand Icon */}
        <IconButton
          size="small"
          sx={{
            transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
            transition: 'transform 0.2s',
            color: 'rgba(255,255,255,0.3)',
          }}
        >
          <ExpandMoreIcon />
        </IconButton>
      </Box>

      {/* Progress Bar */}
      {isProcessing && (
        <LinearProgress
          variant="determinate"
          value={progress}
          sx={{
            height: 3,
            bgcolor: 'rgba(139,92,246,0.1)',
            '& .MuiLinearProgress-bar': {
              bgcolor: '#8B5CF6',
            },
          }}
        />
      )}

      {/* Expanded Details */}
      <Collapse in={expanded}>
        <Box sx={{ p: 1.5, pt: 1, bgcolor: 'rgba(0,0,0,0.2)' }}>
          <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 1 }}>
            <DetailRow label="Total Playlists" value={batch.total} />
            <DetailRow label="Queued" value={batch.queued} color="#34D399" />
            <DetailRow label="Skipped" value={batch.skipped} color="#F59E0B" />
            <DetailRow label="Failed" value={batch.failed} color="#EF4444" />
          </Box>
          
          {batch.download_ids && batch.download_ids.length > 0 && (
            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.3)', mt: 1, display: 'block', fontSize: '0.65rem' }}>
              Download IDs: {batch.download_ids.slice(0, 5).join(', ')}
              {batch.download_ids.length > 5 && ` +${batch.download_ids.length - 5} more`}
            </Typography>
          )}
        </Box>
      </Collapse>
    </Card>
  );
}

function StatChip({ icon, label, color }) {
  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 0.5,
        px: 1,
        py: 0.25,
        borderRadius: 1,
        bgcolor: `${color}15`,
        border: `1px solid ${color}40`,
      }}
    >
      {React.cloneElement(icon, { sx: { fontSize: 12, color } })}
      <Typography variant="caption" sx={{ color, fontSize: '0.7rem', fontWeight: 600 }}>
        {label}
      </Typography>
    </Box>
  );
}

function DetailRow({ label, value, color = 'rgba(255,255,255,0.6)' }) {
  return (
    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.4)', fontSize: '0.7rem' }}>
        {label}
      </Typography>
      <Typography variant="caption" sx={{ color, fontSize: '0.7rem', fontWeight: 600 }}>
        {value}
      </Typography>
    </Box>
  );
}
