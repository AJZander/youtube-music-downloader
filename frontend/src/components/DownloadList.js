// frontend/src/components/DownloadList.js
import React, { useEffect, useMemo, useState } from 'react';
import {
  Box, Button, CircularProgress,
  InputAdornment, MenuItem, Select, Tab, Tabs,
  TextField, Tooltip, Typography,
} from '@mui/material';
import ClearAllIcon        from '@mui/icons-material/ClearAll';
import ChevronLeftIcon     from '@mui/icons-material/ChevronLeft';
import ChevronRightIcon    from '@mui/icons-material/ChevronRight';
import FirstPageIcon       from '@mui/icons-material/FirstPage';
import LastPageIcon        from '@mui/icons-material/LastPage';
import RefreshIcon         from '@mui/icons-material/Refresh';
import SearchIcon          from '@mui/icons-material/Search';
import DownloadItem from './DownloadItem';

// ── Tab definition ────────────────────────────────────────────────────────────

const TABS = [
  { key: 'all',        label: 'All',        statuses: null },
  { key: 'active',     label: 'Active',     statuses: ['downloading', 'processing'] },
  { key: 'queued',     label: 'Queued',     statuses: ['queued'] },
  { key: 'completed',  label: 'Completed',  statuses: ['completed'] },
  { key: 'failed',     label: 'Failed',     statuses: ['failed'] },
  { key: 'cancelled',  label: 'Cancelled',  statuses: ['cancelled'] },
];

const TAB_COLORS = {
  active:    '#8B5CF6',
  queued:    '#F59E0B',
  completed: '#10B981',
  failed:    '#EF4444',
  cancelled: '#6B7280',
};

// ── Badge counts from downloads list ─────────────────────────────────────────

function useCounts(downloads) {
  return useMemo(() => {
    const c = { downloading: 0, processing: 0, queued: 0, completed: 0, failed: 0, cancelled: 0 };
    for (const d of downloads) c[d.status] = (c[d.status] ?? 0) + 1;
    return c;
  }, [downloads]);
}

// ── Empty state ─────────────────────────────────────────────────────────────

const EMPTY_MESSAGES = {
  all:       'No downloads yet — paste a URL above to get started.',
  active:    'Nothing downloading right now.',
  queued:    'Queue is empty.',
  completed: 'No completed downloads yet.',
  failed:    'No failed downloads.',
  cancelled: 'No cancelled downloads.',
};

// ── Pagination ───────────────────────────────────────────────────────────────

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];
const DEFAULT_PAGE_SIZE = 25;

function Paginator({ page, totalPages, pageSize, onPage, onPageSize, totalItems }) {
  if (totalPages <= 1 && totalItems <= PAGE_SIZE_OPTIONS[0]) return null;
  const from = (page - 1) * pageSize + 1;
  const to   = Math.min(page * pageSize, totalItems);

  const btnSx = (disabled) => ({
    minWidth: 32, width: 32, height: 32, p: 0,
    color: disabled ? 'rgba(255,255,255,0.15)' : 'rgba(255,255,255,0.5)',
    '&:hover': disabled ? {} : { color: '#A78BFA', bgcolor: 'rgba(139,92,246,0.1)' },
  });

  return (
    <Box sx={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      mt: 1.5, flexWrap: 'wrap', gap: 1,
    }}>
      {/* Left: item range */}
      <Typography sx={{ fontSize: '0.7rem', color: 'rgba(255,255,255,0.25)', minWidth: 90 }}>
        {from}–{to} of {totalItems}
      </Typography>

      {/* Centre: page nav */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        <Button variant="text" disabled={page === 1}          onClick={() => onPage(1)}           sx={btnSx(page === 1)}><FirstPageIcon   sx={{ fontSize: 16 }} /></Button>
        <Button variant="text" disabled={page === 1}          onClick={() => onPage(page - 1)}    sx={btnSx(page === 1)}><ChevronLeftIcon  sx={{ fontSize: 16 }} /></Button>

        {/* Page number pills */}
        {Array.from({ length: totalPages }, (_, i) => i + 1)
          .filter(p => p === 1 || p === totalPages || Math.abs(p - page) <= 2)
          .reduce((acc, p, idx, arr) => {
            if (idx > 0 && p - arr[idx - 1] > 1)
              acc.push('…');
            acc.push(p);
            return acc;
          }, [])
          .map((p, idx) =>
            p === '…' ? (
              <Typography key={`e${idx}`} sx={{ px: 0.5, color: 'rgba(255,255,255,0.2)', fontSize: '0.75rem' }}>…</Typography>
            ) : (
              <Button
                key={p}
                variant={p === page ? 'contained' : 'text'}
                onClick={() => onPage(p)}
                sx={{
                  minWidth: 32, width: 32, height: 32, p: 0,
                  fontSize: '0.75rem', fontWeight: p === page ? 700 : 400,
                  ...(p === page
                    ? { bgcolor: '#8B5CF6', color: '#fff', '&:hover': { bgcolor: '#7C3AED' } }
                    : { color: 'rgba(255,255,255,0.5)', '&:hover': { color: '#A78BFA', bgcolor: 'rgba(139,92,246,0.1)' } }
                  ),
                }}
              >
                {p}
              </Button>
            )
          )
        }

        <Button variant="text" disabled={page === totalPages} onClick={() => onPage(page + 1)}    sx={btnSx(page === totalPages)}><ChevronRightIcon sx={{ fontSize: 16 }} /></Button>
        <Button variant="text" disabled={page === totalPages} onClick={() => onPage(totalPages)} sx={btnSx(page === totalPages)}><LastPageIcon    sx={{ fontSize: 16 }} /></Button>
      </Box>

      {/* Right: page-size selector */}
      <Select
        value={pageSize}
        onChange={e => { onPageSize(Number(e.target.value)); onPage(1); }}
        size="small"
        variant="outlined"
        sx={{
          height: 30, fontSize: '0.72rem', color: 'rgba(255,255,255,0.45)',
          '.MuiOutlinedInput-notchedOutline': { borderColor: 'rgba(255,255,255,0.1)' },
          '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: 'rgba(255,255,255,0.2)' },
          '.MuiSvgIcon-root': { color: 'rgba(255,255,255,0.3)' },
          bgcolor: '#252530',
        }}
      >
        {PAGE_SIZE_OPTIONS.map(n => (
          <MenuItem key={n} value={n} sx={{ fontSize: '0.75rem' }}>{n} / page</MenuItem>
        ))}
      </Select>
    </Box>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function DownloadList({ downloads, loading, onCancel, onRetry, onBulkDelete, onRetryAll }) {
  const [tab,      setTab]      = useState('all');
  const [search,   setSearch]   = useState('');
  const [page,     setPage]     = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);

  // Reset to page 1 whenever tab or search changes
  useEffect(() => { setPage(1); }, [tab, search]);

  const counts = useCounts(downloads);

  // Active = downloading + processing
  const activeCount    = (counts.downloading ?? 0) + (counts.processing ?? 0);
  const tabCount = (t) => {
    if (t.key === 'all')    return downloads.length;
    if (t.key === 'active') return activeCount;
    return counts[t.statuses[0]] ?? 0;
  };

  // Filter by tab then search
  const filtered = useMemo(() => {
    const current = TABS.find(t => t.key === tab);
    let list = downloads;
    if (current?.statuses) {
      list = list.filter(d => current.statuses.includes(d.status));
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(d =>
        (d.title  || '').toLowerCase().includes(q) ||
        (d.artist || '').toLowerCase().includes(q) ||
        (d.album  || '').toLowerCase().includes(q)
      );
    }
    return list;
  }, [downloads, tab, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage   = Math.min(page, totalPages);
  const visible    = filtered.slice((safePage - 1) * pageSize, safePage * pageSize);

  const failedCount     = counts.failed    ?? 0;
  const completedCount  = counts.completed ?? 0;
  const cancelledCount  = counts.cancelled ?? 0;
  const canRetryAll     = failedCount > 0 && tab === 'failed';
  const canClearTab     = (tab === 'completed' && completedCount > 0)
                       || (tab === 'failed'    && failedCount    > 0)
                       || (tab === 'cancelled' && cancelledCount > 0);

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress size={28} sx={{ color: '#8B5CF6' }} />
      </Box>
    );
  }

  return (
    <Box>
      {/* ── Toolbar ── */}
      <Box sx={{
        display: 'flex', alignItems: 'center', gap: 1.5,
        mb: 1.5, flexWrap: 'wrap',
      }}>

        {/* Search */}
        <TextField
          size="small"
          placeholder="Search title, artist, album…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          sx={{
            flex: '1 1 220px',
            '& .MuiOutlinedInput-root': {
              bgcolor: '#252530',
              '& fieldset': { borderColor: 'rgba(255,255,255,0.08)' },
              '&:hover fieldset': { borderColor: 'rgba(255,255,255,0.16)' },
              '&.Mui-focused fieldset': { borderColor: '#8B5CF6', borderWidth: 1 },
            },
            '& input': {
              color: '#fff', fontSize: '0.8rem',
              '&::placeholder': { color: 'rgba(255,255,255,0.25)', opacity: 1 },
            },
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon sx={{ fontSize: 16, color: 'rgba(255,255,255,0.25)' }} />
              </InputAdornment>
            ),
          }}
        />

        {/* Retry all failed */}
        {canRetryAll && (
          <Tooltip title="Re-queue all failed downloads">
            <Button
              size="small"
              variant="outlined"
              startIcon={<RefreshIcon sx={{ fontSize: 14 }} />}
              onClick={() => onRetryAll?.('failed')}
              sx={{
                textTransform: 'none', fontSize: '0.78rem', height: 34,
                borderColor: 'rgba(245,158,11,0.4)', color: '#F59E0B',
                '&:hover': { borderColor: '#F59E0B', bgcolor: 'rgba(245,158,11,0.08)' },
              }}
            >
              Retry All ({failedCount})
            </Button>
          </Tooltip>
        )}

        {/* Clear tab */}
        {canClearTab && (
          <Tooltip title={`Delete all ${tab} downloads`}>
            <Button
              size="small"
              variant="outlined"
              startIcon={<ClearAllIcon sx={{ fontSize: 14 }} />}
              onClick={() => onBulkDelete?.(tab)}
              sx={{
                textTransform: 'none', fontSize: '0.78rem', height: 34,
                borderColor: 'rgba(107,114,128,0.4)', color: '#9CA3AF',
                '&:hover': { borderColor: '#9CA3AF', bgcolor: 'rgba(107,114,128,0.08)' },
              }}
            >
              Clear {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </Button>
          </Tooltip>
        )}
      </Box>

      {/* ── Tabs ── */}
      <Tabs
        value={tab}
        onChange={(_, v) => setTab(v)}
        variant="scrollable"
        scrollButtons="auto"
        sx={{
          mb: 1.5, minHeight: 34,
          borderBottom: '1px solid rgba(255,255,255,0.07)',
          '& .MuiTabs-indicator': { bgcolor: '#8B5CF6', height: 2 },
          '& .MuiTab-root': {
            minHeight: 34, py: 0, textTransform: 'none',
            fontSize: '0.8rem', fontWeight: 500,
            color: 'rgba(255,255,255,0.35)',
            '&.Mui-selected': { color: '#C4B5FD' },
          },
        }}
      >
        {TABS.map(t => {
          const n = tabCount(t);
          const accent = TAB_COLORS[t.key] ?? '#8B5CF6';
          return (
            <Tab
              key={t.key}
              value={t.key}
              label={
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                  {t.label}
                  {n > 0 && (
                    <Box component="span" sx={{
                      px: 0.75, py: 0.1, borderRadius: 10,
                      bgcolor: tab === t.key ? `${accent}25` : 'rgba(255,255,255,0.07)',
                      color:   tab === t.key ? accent          : 'rgba(255,255,255,0.4)',
                      fontSize: '0.65rem', fontWeight: 700, lineHeight: 1.6,
                      minWidth: 18, textAlign: 'center',
                    }}>
                      {n}
                    </Box>
                  )}
                </Box>
              }
            />
          );
        })}
      </Tabs>

      {/* ── List ── */}
      {visible.length === 0 ? (
        <Box sx={{ textAlign: 'center', py: 6 }}>
          <Typography sx={{ color: 'rgba(255,255,255,0.25)', fontSize: '0.85rem' }}>
            {search.trim()
              ? `No results for "${search}"`
              : EMPTY_MESSAGES[tab]}
          </Typography>
        </Box>
      ) : (
        <>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            {visible.map(d => (
              <DownloadItem
                key={d.id}
                download={d}
                onCancel={onCancel}
                onRetry={onRetry}
              />
            ))}
          </Box>
          <Paginator
            page={safePage}
            totalPages={totalPages}
            pageSize={pageSize}
            totalItems={filtered.length}
            onPage={setPage}
            onPageSize={setPageSize}
          />
        </>
      )}
    </Box>
  );
}

