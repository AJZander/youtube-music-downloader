// frontend/src/App.js
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
	Alert, Box, Container, CssBaseline,
	Snackbar, ThemeProvider, createTheme,
	Drawer, List, ListItem, ListItemText, ListItemIcon,
	IconButton, useMediaQuery, Typography, Badge, Tooltip, alpha,
} from '@mui/material';
import DownloadIcon        from '@mui/icons-material/Download';
import QueueMusicIcon      from '@mui/icons-material/QueueMusic';
import DashboardIcon       from '@mui/icons-material/Dashboard';
import LibraryMusicIcon    from '@mui/icons-material/LibraryMusic';
import SettingsIcon        from '@mui/icons-material/Settings';
import HistoryIcon         from '@mui/icons-material/History';
import PlaylistPlayIcon    from '@mui/icons-material/PlaylistPlay';
import AnalyticsIcon       from '@mui/icons-material/Analytics';
import MenuIcon            from '@mui/icons-material/Menu';
import CloseIcon           from '@mui/icons-material/Close';

import { api, WSService } from './api';
import Header             from './components/Header';
import DownloadForm       from './components/DownloadForm';
import DownloadList       from './components/DownloadList';
import FormatSelector     from './components/FormatSelector';
import ChannelImporter    from './components/ChannelImporter';
import BatchStatusViewer  from './components/BatchStatusViewer';
import MetadataProcessor  from './components/MetadataProcessor';

// ── Enhanced MUI dark theme with gradients ───────────────────────────────────
const theme = createTheme({
	palette: {
		mode: 'dark',
		primary: { 
			main: '#8B5CF6', 
			light: '#A78BFA',
			dark: '#7C3AED',
		},
		secondary: { 
			main: '#EC4899',
			light: '#F472B6',
			dark: '#DB2777',
		},
		success: {
			main: '#10B981',
			light: '#34D399',
			dark: '#059669',
		},
		background: { 
			default: '#0f0f1a', 
			paper: '#1a1a24',
		},
		text: {
			primary: '#F9FAFB',
			secondary: '#9CA3AF',
		},
	},
	typography: {
		fontFamily: '"Inter", "Segoe UI", system-ui, sans-serif',
		fontSize: 14,
		h1: {
			fontWeight: 700,
			fontSize: '2.5rem',
			letterSpacing: '-0.02em',
		},
		h2: {
			fontWeight: 700,
			fontSize: '2rem',
			letterSpacing: '-0.01em',
		},
		h3: {
			fontWeight: 600,
			fontSize: '1.5rem',
			letterSpacing: '-0.01em',
		},
		h4: {
			fontWeight: 600,
			fontSize: '1.25rem',
		},
		h5: {
			fontWeight: 600,
			fontSize: '1.125rem',
		},
		h6: {
			fontWeight: 600,
			fontSize: '1rem',
		},
		body1: {
			fontSize: '0.875rem',
			lineHeight: 1.6,
		},
		body2: {
			fontSize: '0.8125rem',
			lineHeight: 1.5,
		},
	},
	shape: {
		borderRadius: 12,
	},
	components: {
		MuiPaper: { 
			styleOverrides: { 
				root: { 
					backgroundImage: 'none',
					transition: 'all 0.3s ease',
				} 
			} 
		},
		MuiButton: {
			styleOverrides: {
				root: {
					textTransform: 'none',
					fontWeight: 600,
					borderRadius: 10,
					padding: '10px 20px',
				},
				contained: {
					boxShadow: 'none',
					'&:hover': {
						boxShadow: '0 4px 12px rgba(139, 92, 246, 0.4)',
					},
				},
			},
		},
		MuiCard: {
			styleOverrides: {
				root: {
					borderRadius: 16,
					border: '1px solid rgba(255, 255, 255, 0.05)',
					transition: 'all 0.3s ease',
					'&:hover': {
						borderColor: 'rgba(139, 92, 246, 0.3)',
					},
				},
			},
		},
		MuiLinearProgress: {
			styleOverrides: {
				root: {
					borderRadius: 10,
					height: 8,
				},
			},
		},
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
	const [mode,      setMode]                = useState('dashboard');  // Start with dashboard
	const [activeBatches, setActiveBatches]   = useState([]);
	const [metadataJobs, setMetadataJobs]     = useState([]);
	const [drawerOpen, setDrawerOpen]         = useState(false);
	const [downloadsPagination, setDownloadsPagination] = useState({
		total: 0,
		offset: 0,
		limit: 500,
		hasMore: false,
	});

	// Format selection state
	const [formatDialogOpen,  setFormatDialogOpen]  = useState(false);
	const [formatLoading,     setFormatLoading]      = useState(false);
	const [currentUrl,        setCurrentUrl]         = useState('');
	const [availableFormats,  setAvailableFormats]   = useState(null);

	const { toast, show: showToast, hide: hideToast } = useToast();
	const stats = useStats(downloads);
	
	// Responsive design check - use the theme directly instead of callback
	const isMobile = useMediaQuery(theme.breakpoints.down('md'));

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
		api.getDownloads().then(result => {
			setDownloads(result.downloads);
			setDownloadsPagination({
				total: result.total,
				offset: result.offset,
				limit: result.limit,
				hasMore: result.has_more,
			});
		}).catch(() =>
			showToast('Could not load download history', 'error')
		).finally(() => setLoading(false));

		const svc = ws.current;
		svc.connect().catch(() => showToast('Real-time updates unavailable', 'warning'));

		const offInit   = svc.on('initial_data',          data => { 
			if (data.downloads) {
				// New paginated format
				setDownloads(data.downloads);
				setDownloadsPagination({
					total: data.total,
					offset: data.offset,
					limit: data.limit,
					hasMore: data.has_more,
				});
			} else {
				// Legacy format (for compatibility)
				setDownloads(data);
				setDownloadsPagination({
					total: data.length,
					offset: 0,
					limit: data.length,
					hasMore: false,
				});
			}
			setLoading(false);
		});
		const offUpdate = svc.on('download_update',       upsert);
		const offMeta   = svc.on('metadata_job_update',   (job) => {
			setMetadataJobs(prev => {
				const idx = prev.findIndex(j => j.id === job.id);
				if (idx >= 0) {
					const next = [...prev];
					next[idx] = job;
					return next;
				}
				return [job, ...prev];
			});
		});

		return () => { offInit(); offUpdate(); offMeta(); svc.disconnect(); };
	}, [showToast, upsert]);

	// ── Poll batch status ───────────────────────────────────────────────────────
	useEffect(() => {
		if (activeBatches.length === 0) return;

		const pollBatches = async () => {
			const updates = await Promise.all(
				activeBatches.map(async (batch) => {
					try {
						const status = await api.getBatchStatus(batch.id);
						return status;
					} catch (err) {
						return batch; // Keep existing data on error
					}
				})
			);
			setActiveBatches(updates);

			// Refresh downloads when batches are processing
			if (updates.some(b => b.status === 'processing')) {
				api.getDownloads().then(setDownloads).catch(() => {});
			}
		};

		// Poll every 2 seconds
		const interval = setInterval(pollBatches, 2000);
		return () => clearInterval(interval);
	}, [activeBatches]);

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
	const handleChannelQueued = useCallback((count, channelName, batchId) => {
		const message = `Queuing ${count} playlist${count !== 1 ? 's' : ''}${channelName ? ` from ${channelName}` : ''} in background...`;
		showToast(message);
		setMode('download');
		
		// Add new batch to tracking
		setActiveBatches(prev => [...prev, {
			id: batchId,
			status: 'processing',
			total: count,
			queued: 0,
			skipped: 0,
			failed: 0,
			download_ids: [],
			created_at: new Date().toISOString(),
			updated_at: new Date().toISOString(),
		}]);
		
		// Refresh full list so new items appear as they're queued
		api.getDownloads().then(setDownloads).catch(() => {});
	}, [showToast]);

	// ── Remove batch from tracking ──────────────────────────────────────────────
	const handleRemoveBatch = useCallback((batchId) => {
		setActiveBatches(prev => prev.filter(b => b.id !== batchId));
	}, []);

	// ── Metadata processing handlers ────────────────────────────────────────────
	const handleMetadataJobStarted = useCallback((jobId) => {
		showToast(`Started metadata processing (Job: ${jobId.slice(0, 8)}...)`);
		setMode('metadata');  // Switch to metadata tab
	}, [showToast]);

	const handleMetadataError = useCallback((message, severity = 'error') => {
		showToast(message, severity);
	}, [showToast]);

	// ── Load more downloads (lazy loading) ──────────────────────────────────────
	const handleLoadMore = useCallback(async () => {
		if (loading || !downloadsPagination.hasMore) return;
		
		setLoading(true);
		try {
			const result = await api.getDownloads({
				limit: downloadsPagination.limit,
				offset: downloadsPagination.offset + downloadsPagination.limit,
			});
			
			setDownloads(prev => [...prev, ...result.downloads]);
			setDownloadsPagination({
				total: result.total,
				offset: result.offset,
				limit: result.limit,
				hasMore: result.has_more,
			});
		} catch (err) {
			showToast('Failed to load more downloads', 'error');
		} finally {
			setLoading(false);
		}
	}, [loading, downloadsPagination, showToast]);

	// ── Hamburger menu handlers ─────────────────────────────────────────────────
	const handleDrawerToggle = useCallback(() => {
		setDrawerOpen(prev => !prev);
	}, []);

	const handleModeChange = useCallback((newMode) => {
		setMode(newMode);
		if (isMobile) {
			setDrawerOpen(false);
		}
	}, [isMobile]);

	// ── Navigation items ────────────────────────────────────────────────────────
	const navigationItems = [
		{ 
			label: 'Dashboard', 
			value: 'dashboard', 
			icon: <DashboardIcon />,
			description: 'Overview & statistics',
			badge: null,
			available: true,
		},
		{ 
			label: 'Downloads', 
			value: 'download', 
			icon: <DownloadIcon />,
			description: 'Download songs & albums',
			badge: stats.downloading > 0 ? stats.downloading : null,
			available: true,
		},
		{ 
			label: 'Channel Import', 
			value: 'channel', 
			icon: <QueueMusicIcon />,
			description: 'Bulk channel imports',
			badge: activeBatches.filter(b => b.status === 'processing').length || null,
			available: true,
		},
		{ 
			label: 'Queue Manager', 
			value: 'queue', 
			icon: <PlaylistPlayIcon />,
			description: 'Manage download queue',
			badge: stats.queued > 0 ? stats.queued : null,
			available: false, // Future feature
		},
		{ 
			label: 'Library', 
			value: 'library', 
			icon: <LibraryMusicIcon />,
			description: 'Browse downloaded music',
			badge: null,
			available: false, // Future feature
		},
		{ 
			label: 'History', 
			value: 'history', 
			icon: <HistoryIcon />,
			description: 'Download history & logs',
			badge: null,
			available: false, // Future feature
		},
		{ 
			label: 'Analytics', 
			value: 'analytics', 
			icon: <AnalyticsIcon />,
			description: 'Stats & insights',
			badge: null,
			available: false, // Future feature
		},
		{ 
			label: 'Settings', 
			value: 'settings', 
			icon: <SettingsIcon />,
			description: 'App preferences',
			badge: null,
			available: false, // Future feature
		},
	];

	// ── Render ──────────────────────────────────────────────────────────────────
	const renderNavigationDrawer = () => (
		<Drawer
			variant={isMobile ? 'temporary' : 'permanent'}
			anchor="left"
			open={isMobile ? drawerOpen : true}
			onClose={handleDrawerToggle}
			sx={{
				'& .MuiDrawer-paper': {
					width: 280,
					background: 'linear-gradient(180deg, #1a1a24 0%, #16161f 100%)',
					borderRight: '1px solid rgba(139, 92, 246, 0.1)',
					pt: 2,
					boxShadow: '4px 0 24px rgba(0, 0, 0, 0.3)',
				},
			}}
		>
			{/* App Logo/Title */}
			<Box sx={{ px: 3, mb: 3, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
				<Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
					<Box 
						sx={{ 
							width: 40, 
							height: 40, 
							borderRadius: 2,
							background: 'linear-gradient(135deg, #8B5CF6 0%, #EC4899 100%)',
							display: 'flex',
							alignItems: 'center',
							justifyContent: 'center',
							boxShadow: '0 4px 12px rgba(139, 92, 246, 0.4)',
						}}
					>
						<QueueMusicIcon sx={{ color: 'white', fontSize: 24 }} />
					</Box>
					<Box>
						<Typography variant="h6" sx={{ fontWeight: 700, lineHeight: 1.2 }}>
							YTM Downloader
						</Typography>
						<Typography variant="caption" sx={{ color: 'text.secondary' }}>
							Music Manager
						</Typography>
					</Box>
				</Box>
				{isMobile && (
					<IconButton onClick={handleDrawerToggle} size="small" sx={{ color: 'text.secondary' }}>
						<CloseIcon />
					</IconButton>
				)}
			</Box>

			{/* Navigation Items */}
			<List sx={{ px: 1.5 }}>
				{navigationItems.map((item) => (
					<Tooltip
						key={item.value}
						title={item.available ? '' : 'Coming Soon'}
						placement="right"
						arrow
					>
						<Box sx={{ position: 'relative' }}>
							<ListItem
								button
								disabled={!item.available}
								selected={mode === item.value}
								onClick={() => item.available && handleModeChange(item.value)}
								sx={{
									mb: 0.5,
									borderRadius: 2,
									position: 'relative',
									overflow: 'hidden',
									transition: 'all 0.3s ease',
									'&::before': {
										content: '""',
										position: 'absolute',
										left: 0,
										top: 0,
										bottom: 0,
										width: 3,
										bgcolor: 'primary.main',
										opacity: 0,
										transition: 'opacity 0.3s ease',
									},
									'&.Mui-selected': {
										bgcolor: alpha('#8B5CF6', 0.15),
										'&::before': {
											opacity: 1,
										},
										'& .MuiListItemIcon-root': {
											color: 'primary.light',
										},
										'& .MuiListItemText-primary': {
											color: 'primary.light',
											fontWeight: 700,
										},
									},
									'&:hover:not(.Mui-selected):not(.Mui-disabled)': {
										bgcolor: alpha('#8B5CF6', 0.08),
									},
									'&.Mui-disabled': {
										opacity: 0.4,
									},
								}}
							>
								<ListItemIcon sx={{ 
									color: 'text.secondary',
									minWidth: 40,
									transition: 'color 0.3s ease',
								}}>
									{item.badge ? (
										<Badge badgeContent={item.badge} color="primary">
											{item.icon}
										</Badge>
									) : item.icon}
								</ListItemIcon>
								<ListItemText
									primary={item.label}
									secondary={item.description}
									primaryTypographyProps={{
										fontSize: '0.875rem',
										fontWeight: 600,
									}}
									secondaryTypographyProps={{
										fontSize: '0.75rem',
										color: 'text.secondary',
										mt: 0.25,
										sx: {
											display: '-webkit-box',
											WebkitLineClamp: 1,
											WebkitBoxOrient: 'vertical',
											overflow: 'hidden',
										},
									}}
								/>
								{!item.available && (
									<Typography
										variant="caption"
										sx={{
											bgcolor: alpha('#EC4899', 0.2),
											color: 'secondary.light',
											px: 1,
											py: 0.25,
											borderRadius: 1,
											fontSize: '0.6875rem',
											fontWeight: 600,
										}}
									>
										SOON
									</Typography>
								)}
							</ListItem>
						</Box>
					</Tooltip>
				))}
			</List>

			{/* Footer - Version Info */}
			<Box sx={{ 
				mt: 'auto', 
				p: 2, 
				borderTop: '1px solid rgba(255, 255, 255, 0.05)',
			}}>
				<Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
					Version 2.0.0
				</Typography>
				<Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
					© 2026 YTM Downloader
				</Typography>
			</Box>
		</Drawer>
	);

	return (
		<ThemeProvider theme={theme}>
			<CssBaseline />
			<Box sx={{ display: 'flex', minHeight: '100vh', bgcolor: 'background.default', overflow: 'hidden' }}>
				{/* Navigation Drawer */}
				{renderNavigationDrawer()}

				{/* Main Content */}
				<Box 
					component="main" 
					sx={{ 
						flexGrow: 1, 
						ml: isMobile ? 0 : '280px',
						transition: 'margin-left 0.3s ease',
						display: 'flex',
						flexDirection: 'column',
						width: isMobile ? '100%' : 'calc(100% - 280px)',
						overflow: 'auto',
					}}
				>
					{/* Top Bar for Mobile */}
					{isMobile && (
						<Box sx={{ 
							p: 2, 
							display: 'flex', 
							alignItems: 'center', 
							gap: 2,
							bgcolor: 'background.paper',
							borderBottom: '1px solid rgba(255, 255, 255, 0.05)',
						}}>
							<IconButton
								onClick={handleDrawerToggle}
								sx={{ 
									color: 'text.primary',
									'&:hover': { bgcolor: alpha('#8B5CF6', 0.1) }
								}}
							>
								<MenuIcon />
							</IconButton>
							<Typography variant="h6" sx={{ fontWeight: 700 }}>
								{navigationItems.find(item => item.value === mode)?.label || 'YTM Downloader'}
							</Typography>
						</Box>
					)}

					<Container 
						maxWidth="xl" 
						sx={{ 
							py: { xs: 2, sm: 3, md: 4 },
							px: { xs: 2, sm: 3, md: 4 },
							flexGrow: 1,
							width: '100%',
							maxWidth: '100%',
						}}
					>
						{/* Content based on mode */}
						{mode === 'dashboard' && (
							<Box>
								<Header stats={stats} />
								
								{/* Quick Stats Cards */}
								<Box sx={{ 
									display: 'grid', 
									gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', lg: '1fr 1fr 1fr 1fr' },
									gap: 2,
									mb: 3,
								}}>
									<StatCard
										label="Active Downloads"
										value={stats.downloading}
										icon={<DownloadIcon />}
										color="#8B5CF6"
									/>
									<StatCard
										label="In Queue"
										value={stats.queued}
										icon={<PlaylistPlayIcon />}
										color="#EC4899"
									/>
									<StatCard
										label="Completed"
										value={stats.completed}
										icon={<LibraryMusicIcon />}
										color="#10B981"
									/>
									<StatCard
										label="Failed"
										value={stats.failed}
										icon={<HistoryIcon />}
										color="#EF4444"
									/>
								</Box>

								{/* Recent Activity */}
								<Box sx={{ ...cardSx }}>
									<Typography variant="h6" sx={{ mb: 2, fontWeight: 700 }}>
										Recent Downloads
									</Typography>
									<DownloadList
										downloads={downloads.slice(0, 5)}
										loading={loading}
										pagination={{ ...downloadsPagination, hasMore: false }}
										onCancel={handleCancel}
										onRetry={handleRetry}
										onRetryAll={handleRetryAll}
										onBulkDelete={handleBulkDelete}
										onLoadMore={() => {}}
										compact
									/>
									<Box sx={{ mt: 2, textAlign: 'center' }}>
										<Typography
											component="button"
											onClick={() => setMode('download')}
											sx={{
												color: 'primary.light',
												cursor: 'pointer',
												background: 'none',
												border: 'none',
												fontSize: '0.875rem',
												fontWeight: 600,
												'&:hover': {
													textDecoration: 'underline',
												},
											}}
										>
											View All Downloads →
										</Typography>
									</Box>
								</Box>
							</Box>
						)}

						{mode === 'download' && (
							<Box>
								{/* Mobile: Show simplified header */}
								{!isMobile && <Header stats={stats} />}
								
								<Box sx={cardSx}>
									<DownloadForm onSubmit={handleUrlSubmit} />
								</Box>
								
								{/* Queue / history card */}
								<Box sx={{ ...cardSx, mt: 2 }}>
									<DownloadList
										downloads={downloads}
										loading={loading}
										pagination={downloadsPagination}
										onCancel={handleCancel}
										onRetry={handleRetry}
										onRetryAll={handleRetryAll}
										onBulkDelete={handleBulkDelete}
										onLoadMore={handleLoadMore}
									/>
								</Box>
							</Box>
						)}

						{mode === 'channel' && (
							<Box>
								{!isMobile && <Header stats={stats} />}
								<Box sx={cardSx}>
									<MetadataProcessor
										onError={handleMetadataError}
										onJobStarted={handleMetadataJobStarted}
									/>
								</Box>
							</Box>
						)}

						{/* Placeholder views for future features */}
						{mode === 'queue' && <PlaceholderView icon={<PlaylistPlayIcon />} title="Queue Manager" />}
						{mode === 'library' && <PlaceholderView icon={<LibraryMusicIcon />} title="Music Library" />}
						{mode === 'history' && <PlaceholderView icon={<HistoryIcon />} title="Download History" />}
						{mode === 'analytics' && <PlaceholderView icon={<AnalyticsIcon />} title="Analytics" />}
						{mode === 'settings' && <PlaceholderView icon={<SettingsIcon />} title="Settings" />}

						{/* Batch Status Viewer */}
						{activeBatches.length > 0 && (
							<BatchStatusViewer 
								batches={activeBatches} 
								onRemove={handleRemoveBatch} 
							/>
						)}
					</Container>
				</Box>

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
						<Alert 
							onClose={hideToast} 
							severity={toast.sev} 
							sx={{ 
								width: '100%',
								borderRadius: 2,
								border: '1px solid rgba(255, 255, 255, 0.1)',
							}}
						>
							{toast.msg}
						</Alert>
					)}
				</Snackbar>
			</Box>
		</ThemeProvider>
	);
}


// ── Helper Components ─────────────────────────────────────────────────────────

// Stat Card Component for Dashboard
function StatCard({ label, value, icon, color }) {
	return (
		<Box
			sx={{
				p: { xs: 2, sm: 3 },
				borderRadius: { xs: 2, sm: 3 },
				background: `linear-gradient(135deg, ${alpha(color, 0.1)} 0%, ${alpha(color, 0.05)} 100%)`,
				border: `1px solid ${alpha(color, 0.2)}`,
				display: 'flex',
				alignItems: 'center',
				gap: { xs: 1.5, sm: 2 },
				transition: 'all 0.3s ease',
				'&:hover': {
					transform: 'translateY(-2px)',
					boxShadow: `0 8px 24px ${alpha(color, 0.2)}`,
				},
			}}
		>
			<Box
				sx={{
					width: { xs: 40, sm: 48 },
					height: { xs: 40, sm: 48 },
					borderRadius: 2,
					background: `linear-gradient(135deg, ${color} 0%, ${alpha(color, 0.8)} 100%)`,
					display: 'flex',
					alignItems: 'center',
					justifyContent: 'center',
					color: 'white',
					flexShrink: 0,
				}}
			>
				{React.cloneElement(icon, { sx: { fontSize: { xs: 20, sm: 24 } } })}
			</Box>
			<Box sx={{ minWidth: 0 }}>
				<Typography variant="h4" sx={{ fontWeight: 700, color: color, fontSize: { xs: '1.5rem', sm: '2rem' } }}>
					{value}
				</Typography>
				<Typography variant="body2" sx={{ color: 'text.secondary', mt: 0.5, fontSize: { xs: '0.75rem', sm: '0.875rem' } }}>
					{label}
				</Typography>
			</Box>
		</Box>
	);
}

// Placeholder View for Future Features
function PlaceholderView({ icon, title }) {
	return (
		<Box
			sx={{
				display: 'flex',
				flexDirection: 'column',
				alignItems: 'center',
				justifyContent: 'center',
				minHeight: { xs: '50vh', sm: '60vh' },
				textAlign: 'center',
				px: { xs: 2, sm: 3 },
			}}
		>
			<Box
				sx={{
					width: { xs: 80, sm: 120 },
					height: { xs: 80, sm: 120 },
					borderRadius: { xs: 3, sm: 4 },
					background: 'linear-gradient(135deg, #8B5CF6 0%, #EC4899 100%)',
					display: 'flex',
					alignItems: 'center',
					justifyContent: 'center',
					mb: 3,
					boxShadow: '0 8px 32px rgba(139, 92, 246, 0.3)',
				}}
			>
				{React.cloneElement(icon, { sx: { fontSize: { xs: 48, sm: 64 }, color: 'white' } })}
			</Box>
			<Typography variant="h3" sx={{ fontWeight: 700, mb: 1, fontSize: { xs: '1.75rem', sm: '2.5rem', md: '3rem' } }}>
				{title}
			</Typography>
			<Typography variant="body1" sx={{ color: 'text.secondary', maxWidth: 480, mb: 3, fontSize: { xs: '0.875rem', sm: '1rem' } }}>
				This feature is coming soon! We're working hard to bring you an amazing experience.
			</Typography>
			<Box
				sx={{
					px: { xs: 2, sm: 3 },
					py: { xs: 1, sm: 1.5 },
					borderRadius: 2,
					bgcolor: alpha('#8B5CF6', 0.1),
					border: '1px solid rgba(139, 92, 246, 0.3)',
				}}
			>
				<Typography variant="body2" sx={{ color: 'primary.light', fontWeight: 600, fontSize: { xs: '0.8125rem', sm: '0.875rem' } }}>
					🚀 Under Development
				</Typography>
			</Box>
		</Box>
	);
}

const cardSx = {
	p: { xs: 2, sm: 3 },
	mt: { xs: 2, sm: 3 },
	borderRadius: 3,
	bgcolor: 'background.paper',
	border: '1px solid rgba(255, 255, 255, 0.05)',
	boxShadow: '0 4px 24px rgba(0, 0, 0, 0.2)',
	transition: 'all 0.3s ease',
	'&:hover': {
		borderColor: alpha('#8B5CF6', 0.2),
	},
};
