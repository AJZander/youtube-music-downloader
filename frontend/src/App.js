// frontend/src/App.js
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
	Alert, Box, Container, CssBaseline,
	Snackbar, Tab, Tabs, ThemeProvider, createTheme,
} from '@mui/material';
import DownloadIcon   from '@mui/icons-material/Download';
import QueueMusicIcon from '@mui/icons-material/QueueMusic';

import { api, WSService } from './api';
import Header          from './components/Header';
import DownloadForm    from './components/DownloadForm';
import DownloadList    from './components/DownloadList';
import FormatSelector  from './components/FormatSelector';
import ChannelImporter from './components/ChannelImporter';

// ── MUI dark theme ────────────────────────────────────────────────────────────
const theme = createTheme({
	palette: {
		mode: 'dark',
		primary: { main: '#8B5CF6' },
		background: { default: '#0f0f1a', paper: '#1a1a24' },
	},
	typography: {
		fontFamily: '"Inter", "Segoe UI", system-ui, sans-serif',
		fontSize: 13,
	},
	components: {
		MuiPaper: { styleOverrides: { root: { backgroundImage: 'none' } } },
	},
});

// ── Toast helper ──────────────────────────────────────────────────────────────
function useToast() {
	const [toast, setToast] = useState(null);
	const show = useCallback((msg, sev = 'success') => setToast({ msg, sev }), []);
	const hide = useCallback(() => setToast(null), []);
	return { toast, show, hide };
}

// ── Derive stats from local downloads list ────────────────────────────────────
function useStats(downloads) {
	return useMemo(() => {
		const c = { queued: 0, downloading: 0, processing: 0, completed: 0, failed: 0, cancelled: 0 };
		for (const d of downloads) if (c[d.status] !== undefined) c[d.status]++;
		return { ...c, total: downloads.length };
	}, [downloads]);
}

// ── App ────────────────────────────────────────────────────────────────────────
export default function App() {
	const [downloads, setDownloads]           = useState([]);
	const [loading,   setLoading]             = useState(true);
	const [mode,      setMode]                = useState('download');  // 'download' | 'channel'

	// Format selection state
	const [formatDialogOpen,  setFormatDialogOpen]  = useState(false);
	const [formatLoading,     setFormatLoading]      = useState(false);
	const [currentUrl,        setCurrentUrl]         = useState('');
	const [availableFormats,  setAvailableFormats]   = useState(null);

	const { toast, show: showToast, hide: hideToast } = useToast();
	const stats = useStats(downloads);

	// Stable WS instance across renders
	const ws = useRef(null);
	if (!ws.current) ws.current = new WSService();

	// ── Upsert helper ───────────────────────────────────────────────────────────
	const upsert = useCallback((item) =>
		setDownloads(prev => {
			const idx = prev.findIndex(d => d.id === item.id);
			if (idx >= 0) { const next = [...prev]; next[idx] = item; return next; }
			return [item, ...prev];
		}), []);

	// ── Initial fetch + WS ──────────────────────────────────────────────────────
	useEffect(() => {
		api.getDownloads().then(setDownloads).catch(() =>
			showToast('Could not load download history', 'error')
		).finally(() => setLoading(false));

		const svc = ws.current;
		svc.connect().catch(() => showToast('Real-time updates unavailable', 'warning'));

		const offInit   = svc.on('initial_data',    data => { setDownloads(data); setLoading(false); });
		const offUpdate = svc.on('download_update', upsert);

		return () => { offInit(); offUpdate(); svc.disconnect(); };
	}, [showToast, upsert]);

	// ── URL submit: open format selector ───────────────────────────────────────
	const handleUrlSubmit = useCallback(async (url) => {
		setCurrentUrl(url);
		setFormatLoading(true);
		setFormatDialogOpen(true);
		try {
			setAvailableFormats(await api.getFormats(url));
		} catch (err) {
			setFormatDialogOpen(false);
			showToast(err.response?.data?.detail || 'Failed to fetch formats', 'error');
		} finally {
			setFormatLoading(false);
		}
	}, [showToast]);

	// ── Format selected: create download ───────────────────────────────────────
	const handleFormatSelect = useCallback(async (formatId) => {
		setFormatDialogOpen(false);
		try {
			const dl = await api.createDownload(currentUrl, formatId);
			upsert(dl);
			showToast('Added to queue!');
		} catch (err) {
			showToast(err.response?.data?.detail || 'Failed to add download', 'error');
		} finally {
			setCurrentUrl('');
			setAvailableFormats(null);
		}
	}, [currentUrl, showToast, upsert]);

	const handleFormatDialogClose = useCallback(() => {
		setFormatDialogOpen(false);
		setCurrentUrl('');
		setAvailableFormats(null);
	}, []);

	// ── Cancel ─────────────────────────────────────────────────────────────────
	const handleCancel = useCallback(async (id) => {
		try {
			await api.cancelDownload(id);
			// WS will deliver the status update; optimistically mark cancelled
			setDownloads(prev => prev.map(d =>
				d.id === id ? { ...d, status: 'cancelled' } : d
			));
		} catch {
			showToast('Failed to cancel', 'error');
		}
	}, [showToast]);

	// ── Retry single ───────────────────────────────────────────────────────────
	const handleRetry = useCallback(async (id) => {
		try {
			const dl = await api.retryDownload(id);
			upsert(dl);
			showToast('Re-queued!');
		} catch (err) {
			showToast(err.response?.data?.detail || 'Failed to retry', 'error');
		}
	}, [showToast, upsert]);

	// ── Retry all failed ───────────────────────────────────────────────────────
	const handleRetryAll = useCallback(async () => {
		const failed = downloads.filter(d => d.status === 'failed');
		let count = 0;
		for (const d of failed) {
			try { const dl = await api.retryDownload(d.id); upsert(dl); count++; }
			catch { /* skip errored */ }
		}
		showToast(`Re-queued ${count} download${count !== 1 ? 's' : ''}`);
	}, [downloads, showToast, upsert]);

	// ── Bulk delete ────────────────────────────────────────────────────────────
	const handleBulkDelete = useCallback(async (statusValue) => {
		// Map tab 'active' → not deletable; tab keys match status for the others
		const deletable = ['completed', 'failed', 'cancelled'];
		if (!deletable.includes(statusValue)) return;
		try {
			await api.bulkDelete(statusValue);
			setDownloads(prev => prev.filter(d => d.status !== statusValue));
			showToast(`Cleared all ${statusValue} downloads`);
		} catch (err) {
			showToast(err.response?.data?.detail || 'Failed to clear', 'error');
		}
	}, [showToast]);

	// ── Channel queued callback ─────────────────────────────────────────────────
	const handleChannelQueued = useCallback((count, channelName) => {
		showToast(`Queued ${count} playlist${count !== 1 ? 's' : ''}${channelName ? ` from ${channelName}` : ''}!`);
		setMode('download');
		// Refresh full list so new items appear
		api.getDownloads().then(setDownloads).catch(() => {});
	}, [showToast]);

	// ── Render ──────────────────────────────────────────────────────────────────
	return (
		<ThemeProvider theme={theme}>
			<CssBaseline />
			<Box sx={{ minHeight: '100vh', bgcolor: '#0f0f1a', py: { xs: 1.5, sm: 3 } }}>
				<Container maxWidth="lg" sx={{ px: { xs: 1.5, sm: 3 } }}>

					<Header stats={stats} />

					{/* Input card */}
					<Box sx={cardSx}>
						<Tabs
							value={mode}
							onChange={(_, v) => setMode(v)}
							sx={{
								mb: 2, minHeight: 36,
								borderBottom: '1px solid rgba(255,255,255,0.07)',
								'& .MuiTabs-indicator': { bgcolor: '#8B5CF6' },
								'& .MuiTab-root': {
									minHeight: 36, textTransform: 'none',
									fontSize: { xs: '0.75rem', sm: '0.85rem' },
									fontWeight: 500,
									color: 'rgba(255,255,255,0.4)',
									'&.Mui-selected': { color: '#A78BFA' },
									minWidth: { xs: 80, sm: 120 },
									px: { xs: 1, sm: 2 },
								},
								'& .MuiTab-iconWrapper': {
									fontSize: { xs: 14, sm: 16 },
								},
							}}
						>
							<Tab value="download" label="Download"       icon={<DownloadIcon   sx={{ fontSize: { xs: 14, sm: 16 } }} />} iconPosition="start" />
							<Tab value="channel"  label="Channel Import" icon={<QueueMusicIcon sx={{ fontSize: { xs: 14, sm: 16 } }} />} iconPosition="start" />
						</Tabs>

						{mode === 'download' && <DownloadForm onSubmit={handleUrlSubmit} />}
						{mode === 'channel'  && (
							<ChannelImporter
								onQueued={handleChannelQueued}
								onError={msg => showToast(msg, 'error')}
							/>
						)}
					</Box>

					{/* Queue / history card */}
					<Box sx={{ ...cardSx, mt: 2 }}>
						<DownloadList
							downloads={downloads}
							loading={loading}
							onCancel={handleCancel}
							onRetry={handleRetry}
							onRetryAll={handleRetryAll}
							onBulkDelete={handleBulkDelete}
						/>
					</Box>

				</Container>

				{/* Format dialog */}
				<FormatSelector
					open={formatDialogOpen}
					url={currentUrl}
					formats={availableFormats?.formats}
					metadata={availableFormats?.metadata}
					authenticated={availableFormats?.authenticated}
					loading={formatLoading}
					onSelect={handleFormatSelect}
					onClose={handleFormatDialogClose}
				/>

				{/* Toasts */}
				<Snackbar
					open={!!toast}
					autoHideDuration={4000}
					onClose={hideToast}
					anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
				>
					{toast && (
						<Alert onClose={hideToast} severity={toast.sev} sx={{ width: '100%' }}>
							{toast.msg}
						</Alert>
					)}
				</Snackbar>
			</Box>
		</ThemeProvider>
	);
}

const cardSx = {
	p: { xs: 1.5, sm: 2.5 },
	mt: { xs: 1.5, sm: 3 },
	borderRadius: 2,
	bgcolor: '#1a1a24',
	border: '1px solid rgba(255,255,255,0.07)',
};
