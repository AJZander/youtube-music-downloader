// frontend/src/components/SearchBar.js
import React, { useState, useCallback, useRef, useEffect } from 'react';
import {
  Box, Button, Card, CardContent, CircularProgress, Collapse,
  IconButton, InputAdornment, TextField, Tooltip, Typography, alpha,
} from '@mui/material';
import SearchIcon      from '@mui/icons-material/Search';
import AddIcon         from '@mui/icons-material/Add';
import CloseIcon       from '@mui/icons-material/Close';
import MusicNoteIcon   from '@mui/icons-material/MusicNote';
import { api } from '../api';

function formatDuration(seconds) {
  if (!seconds) return null;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatViews(count) {
  if (!count) return null;
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M views`;
  if (count >= 1_000)     return `${(count / 1_000).toFixed(0)}K views`;
  return `${count} views`;
}

export default function SearchBar({ onAdd }) {
  const [query,   setQuery]   = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [open,    setOpen]    = useState(false);
  const [adding,  setAdding]  = useState(new Set());
  const debounceRef = useRef(null);

  const runSearch = useCallback(async (q) => {
    if (!q.trim()) { setResults([]); setOpen(false); return; }
    setLoading(true);
    try {
      const data = await api.search(q.trim(), 10);
      setResults(data.results || []);
      setOpen(true);
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleChange = useCallback((e) => {
    const val = e.target.value;
    setQuery(val);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => runSearch(val), 500);
  }, [runSearch]);

  const handleClear = useCallback(() => {
    setQuery('');
    setResults([]);
    setOpen(false);
  }, []);

  const handleAdd = useCallback(async (result) => {
    setAdding(prev => new Set(prev).add(result.id));
    try {
      await onAdd(result.url);
    } finally {
      setAdding(prev => { const s = new Set(prev); s.delete(result.id); return s; });
    }
  }, [onAdd]);

  // Cleanup debounce on unmount
  useEffect(() => () => clearTimeout(debounceRef.current), []);

  return (
    <Box sx={{ mb: 2 }}>
      {/* Search input */}
      <TextField
        fullWidth
        size="small"
        placeholder="Search YouTube Music — artist, album or song…"
        value={query}
        onChange={handleChange}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              {loading
                ? <CircularProgress size={16} sx={{ color: 'primary.main' }} />
                : <SearchIcon sx={{ fontSize: 18, color: 'rgba(255,255,255,0.3)' }} />}
            </InputAdornment>
          ),
          endAdornment: query ? (
            <InputAdornment position="end">
              <IconButton size="small" onClick={handleClear} sx={{ color: 'text.secondary' }}>
                <CloseIcon fontSize="small" />
              </IconButton>
            </InputAdornment>
          ) : null,
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
            fontSize: '0.875rem',
            '&::placeholder': { color: 'rgba(255,255,255,0.3)', opacity: 1 },
          },
        }}
      />

      {/* Results */}
      <Collapse in={open && results.length > 0}>
        <Card sx={{
          mt: 1,
          bgcolor: '#1e1e2e',
          border: '1px solid rgba(139,92,246,0.2)',
          borderRadius: 2,
          overflow: 'hidden',
          maxHeight: 400,
          overflowY: 'auto',
        }}>
          {results.map((result, idx) => (
            <Box
              key={result.id || idx}
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 1.5,
                px: 2,
                py: 1,
                borderBottom: idx < results.length - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none',
                '&:hover': { bgcolor: alpha('#8B5CF6', 0.06) },
                transition: 'background 0.15s',
              }}
            >
              {/* Thumbnail */}
              {result.thumbnail ? (
                <Box
                  component="img"
                  src={result.thumbnail}
                  alt=""
                  sx={{ width: 40, height: 40, borderRadius: 1, objectFit: 'cover', flexShrink: 0 }}
                />
              ) : (
                <Box sx={{
                  width: 40, height: 40, borderRadius: 1, flexShrink: 0,
                  bgcolor: alpha('#8B5CF6', 0.15),
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  <MusicNoteIcon sx={{ fontSize: 18, color: 'primary.light' }} />
                </Box>
              )}

              {/* Info */}
              <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                <Typography
                  variant="body2"
                  sx={{ fontWeight: 600, color: 'text.primary', lineHeight: 1.3 }}
                  noWrap
                >
                  {result.title}
                </Typography>
                <Box sx={{ display: 'flex', gap: 1, mt: 0.25, flexWrap: 'wrap' }}>
                  <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                    {result.artist}
                  </Typography>
                  {result.album && (
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                      · {result.album}
                    </Typography>
                  )}
                  {result.duration && (
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                      · {formatDuration(result.duration)}
                    </Typography>
                  )}
                  {result.view_count && (
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                      · {formatViews(result.view_count)}
                    </Typography>
                  )}
                </Box>
              </Box>

              {/* Add button */}
              <Tooltip title="Add to queue">
                <IconButton
                  size="small"
                  onClick={() => handleAdd(result)}
                  disabled={adding.has(result.id)}
                  sx={{
                    color: 'primary.light',
                    flexShrink: 0,
                    '&:hover': { bgcolor: alpha('#8B5CF6', 0.15) },
                  }}
                >
                  {adding.has(result.id)
                    ? <CircularProgress size={16} />
                    : <AddIcon fontSize="small" />}
                </IconButton>
              </Tooltip>
            </Box>
          ))}
        </Card>
      </Collapse>

      {open && query && !loading && results.length === 0 && (
        <Typography variant="caption" sx={{ color: 'text.secondary', mt: 0.5, display: 'block' }}>
          No results found for "{query}"
        </Typography>
      )}
    </Box>
  );
}
