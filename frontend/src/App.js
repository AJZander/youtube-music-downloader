// frontend/src/App.js
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
	Alert, Box, Container, CssBaseline,
	Snackbar, ThemeProvider, createTheme,
} from '@mui/material';

import { api, WSService } from './api';
import Header from './components/Header';
import DownloadForm from './components/DownloadForm';
import DownloadList from './components/DownloadList';
import FormatSelector from './components/FormatSelector';

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
	const [toast, setToast] = useState(null); // { msg, sev }
	const show = useCallback((msg, sev = 'success') => setToast({ msg, sev }), []);
	const hide = useCallback(() => setToast(null), []);
	return { toast, show, hide };
}

export default function App() {
	const [downloads, setDownloads] = useState([]);
	const [loading, setLoading] = useState(true);

	// Format selection state
	const [formatDialogOpen, setFormatDialogOpen] = useState(false);
	const [formatLoading, setFormatLoading] = useState(false);
	const [currentUrl, setCurrentUrl] = useState('');
	const [availableFormats, setAvailableFormats] = useState(null);

	const { toast, show: showToast, hide: hideToast } = useToast();

	// Stable WS instance across renders
	const ws = useRef(null);
	if (!ws.current) ws.current = new WSService();

	// ── Initial fetch ───────────────────────────────────────────────────────────
	const fetchDownloads = useCallback(async () => {
		try {
			setDownloads(await api.getDownloads());
		} catch {
			showToast('Could not load download history', 'error');
		} finally {
			setLoading(false);
		}
	}, [showToast]);

	// ── WebSocket subscription ──────────────────────────────────────────────────
	useEffect(() => {
		fetchDownloads();

		const svc = ws.current;

		svc.connect().catch(() => showToast('Real-time updates unavailable', 'warning'));

		// Replace entire list on first connect
		const offInit = svc.on('initial_data', data => setDownloads(data));
		// Upsert on subsequent updates
		const offUpdate = svc.on('download_update', item =>
			setDownloads(prev => {
				const idx = prev.findIndex(d => d.id === item.id);
				if (idx >= 0) {
					const next = [...prev];
					next[idx] = item;
					return next;
				}
				return [item, ...prev];
			})
		);

		return () => {
			offInit();
			offUpdate();
			svc.disconnect();
		};
	}, [fetchDownloads, showToast]);

	// ── Handlers ────────────────────────────────────────────────────────────────

	/**
	 * Step 1: User submits URL -> Fetch available formats
	 */
	const handleUrlSubmit = useCallback(async (url) => {
		setCurrentUrl(url);
		setFormatLoading(true);
		setFormatDialogOpen(true);

		try {
			const formatData = await api.getFormats(url);
			setAvailableFormats(formatData);
			setFormatLoading(false);
		} catch (err) {
			setFormatLoading(false);
			setFormatDialogOpen(false);
			const detail = err.response?.data?.detail || 'Failed to fetch formats';
			showToast(detail, 'error');
		}
	}, [showToast]);

	/**
	 * Step 2: User selects format -> Start download
	 */
	const handleFormatSelect = useCallback(async (formatId) => {
		setFormatDialogOpen(false);

		try {
			await api.createDownload(currentUrl, formatId);
			showToast('Added to queue!');
			setCurrentUrl('');
			setAvailableFormats(null);
		} catch (err) {
			const detail = err.response?.data?.detail || 'Failed to add download';
			showToast(detail, 'error');
		}
	}, [currentUrl, showToast]);

	/**
	 * Close format dialog
	 */
	const handleFormatDialogClose = useCallback(() => {
		setFormatDialogOpen(false);
		setCurrentUrl('');
		setAvailableFormats(null);
	}, []);

	/**
	 * Cancel a download
	 */
	const handleCancel = useCallback(async (id) => {
		try {
			await api.cancelDownload(id);
		} catch {
			showToast('Failed to cancel', 'error');
		}
	}, [showToast]);

	// ── Render ──────────────────────────────────────────────────────────────────
	return (
		<ThemeProvider theme={theme}>
			<CssBaseline />
			<Box sx={{ minHeight: '100vh', bgcolor: '#0f0f1a', py: 3 }}>
				<Container maxWidth="lg">

				<Header />
					<Box sx={cardSx}>
						<DownloadForm onSubmit={handleUrlSubmit} />
					</Box>

					{/* Download queue / history */}
					<Box sx={{ ...cardSx, mt: 2 }}>
						<DownloadList
							downloads={downloads}
							loading={loading}
							onCancel={handleCancel}
						/>
					</Box>

				</Container>


				{/* Format selection dialog */}
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

				{/* Toast notifications */}
				<Snackbar
					open={!!toast}
					autoHideDuration={5000}
					onClose={hideToast}
					anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
				>
					{toast && (
						<Alert
							onClose={hideToast}
							severity={toast.sev}
							sx={{ width: '100%' }}
						>
							{toast.msg}
						</Alert>
					)}
				</Snackbar>
			</Box>
		</ThemeProvider>
	);
}

const cardSx = {
	p: 2.5,
	mt: 3,
	borderRadius: 2,
	bgcolor: '#1a1a24',
	border: '1px solid rgba(255,255,255,0.07)',
};