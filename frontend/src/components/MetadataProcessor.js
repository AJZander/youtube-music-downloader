// frontend/src/components/MetadataProcessor.js
import React, { useState, useCallback, useEffect, useMemo } from 'react';
import {
	Box, Button, TextField, Typography, Card, CardContent,
	LinearProgress, Chip, Alert, List, ListItem, ListItemText,
	ListItemSecondaryAction, Checkbox, FormControl, InputLabel,
	Select, MenuItem, Divider, IconButton, Tooltip, ToggleButtonGroup,
	ToggleButton, Badge, alpha, ButtonGroup,
} from '@mui/material';
import {
	PlaylistAdd as PlaylistAddIcon,
	Cancel as CancelIcon,
	CheckBox as SelectAllIcon,
	CheckBoxOutlineBlank as DeselectAllIcon,
	Download as DownloadIcon,
	Album as AlbumIcon,
	LibraryMusic as SingleIcon,
	VideoLibrary as PlaylistIcon,
	FilterList as FilterIcon,
	MusicNote as EPIcon,
} from '@mui/icons-material';
import { api } from '../api';

const getReleaseIcon = (releaseType) => {
	switch (releaseType) {
		case 'album': return <AlbumIcon fontSize="small" />;
		case 'ep': return <EPIcon fontSize="small" />;
		case 'single': return <SingleIcon fontSize="small" />;
		case 'playlist': return <PlaylistIcon fontSize="small" />;
		default: return <PlaylistIcon fontSize="small" />;
	}
};

const getReleaseColor = (releaseType) => {
	switch (releaseType) {
		case 'album': return 'primary';
		case 'ep': return 'info';
		case 'single': return 'secondary';
		case 'playlist': return 'default';
		default: return 'default';
	}
};

export default function MetadataProcessor({ onError, onJobStarted }) {
	const [url, setUrl] = useState('');
	const [loading, setLoading] = useState(false);
	const [jobs, setJobs] = useState([]);
	const [selectedJob, setSelectedJob] = useState(null);
	const [jobItems, setJobItems] = useState([]);
	const [selectedItems, setSelectedItems] = useState(new Set());
	const [formatId, setFormatId] = useState('bestaudio/best');
	const [loadingItems, setLoadingItems] = useState(false);
	const [queueing, setQueueing] = useState(false);
	
	// Filter state - tracks which types to show
	const [activeFilters, setActiveFilters] = useState(['album', 'ep', 'single', 'playlist']);

	// Define loadJobs callback before it's used in useEffect hooks
	const loadJobs = useCallback(async () => {
		try {
			const jobsList = await api.listMetadataJobs();
			setJobs(jobsList);
		} catch (err) {
			onError?.('Failed to load metadata jobs');
		}
	}, [onError]);

	// Load jobs on component mount
	useEffect(() => {
		loadJobs();
	}, [loadJobs]);

	// Poll for job updates when there are processing jobs
	useEffect(() => {
		const hasProcessingJobs = jobs.some(job => 
			job.status === 'processing' || job.status === 'pending'
		);
		
		if (!hasProcessingJobs) return;
		
		// Poll every 2 seconds
		const interval = setInterval(() => {
			loadJobs();
		}, 2000);
		
		return () => clearInterval(interval);
	}, [jobs, loadJobs]);

	// Compute filtered items and stats
	const { filteredItems, stats } = useMemo(() => {
		const filtered = jobItems.filter(item => 
			activeFilters.includes(item.release_type)
		);
		
		const statsByType = {
			album: 0,
			ep: 0,
			single: 0,
			playlist: 0,
			other: 0,
		};
		
		jobItems.forEach(item => {
			const type = item.release_type || 'other';
			if (statsByType[type] !== undefined) {
				statsByType[type]++;
			} else {
				statsByType.other++;
			}
		});
		
		return { filteredItems: filtered, stats: statsByType };
	}, [jobItems, activeFilters]);

	const handleUrlSubmit = useCallback(async (e) => {
		e.preventDefault();
		if (!url.trim()) return;

		setLoading(true);
		try {
			const response = await api.startMetadataProcessing(url.trim());
			setUrl('');
			onJobStarted?.(response.job_id);
			
			// Refresh jobs list
			await loadJobs();
		} catch (err) {
			onError?.(err.response?.data?.detail || 'Failed to start metadata processing');
		} finally {
			setLoading(false);
		}
	}, [url, onError, onJobStarted, loadJobs]);

	const loadJobItems = useCallback(async (jobId) => {
		setLoadingItems(true);
		try {
			const items = await api.getMetadataJobItems(jobId);
			setJobItems(items);
			setSelectedItems(new Set());
		} catch (err) {
			onError?.('Failed to load job items');
		} finally {
			setLoadingItems(false);
		}
	}, [onError]);

	const handleJobSelect = useCallback(async (job) => {
		setSelectedJob(job);
		if (job.status === 'completed') {
			await loadJobItems(job.id);
		}
	}, [loadJobItems]);

	const handleItemToggle = useCallback((itemId) => {
		setSelectedItems(prev => {
			const newSet = new Set(prev);
			if (newSet.has(itemId)) {
				newSet.delete(itemId);
			} else {
				newSet.add(itemId);
			}
			return newSet;
		});
	}, []);

	const handleSelectAll = useCallback(() => {
		setSelectedItems(new Set(filteredItems.map(item => item.id)));
	}, [filteredItems]);

	const handleDeselectAll = useCallback(() => {
		setSelectedItems(new Set());
	}, []);

	// Select all items of a specific type
	const handleSelectType = useCallback((releaseType) => {
		const typeItems = jobItems
			.filter(item => item.release_type === releaseType)
			.map(item => item.id);
		
		setSelectedItems(prev => {
			const newSet = new Set(prev);
			typeItems.forEach(id => newSet.add(id));
			return newSet;
		});
	}, [jobItems]);

	// Deselect all items of a specific type
	const handleDeselectType = useCallback((releaseType) => {
		const typeItemIds = new Set(
			jobItems
				.filter(item => item.release_type === releaseType)
				.map(item => item.id)
		);
		
		setSelectedItems(prev => {
			const newSet = new Set(prev);
			typeItemIds.forEach(id => newSet.delete(id));
			return newSet;
		});
	}, [jobItems]);

	// Check if all items of a type are selected
	const isTypeFullySelected = useCallback((releaseType) => {
		const typeItems = jobItems.filter(item => item.release_type === releaseType);
		if (typeItems.length === 0) return false;
		return typeItems.every(item => selectedItems.has(item.id));
	}, [jobItems, selectedItems]);

	// Toggle filter visibility
	const handleFilterToggle = useCallback((event, newFilters) => {
		// Don't allow deselecting all filters
		if (newFilters.length > 0) {
			setActiveFilters(newFilters);
		}
	}, []);

	const handleQueueSelected = useCallback(async () => {
		if (selectedItems.size === 0 || !selectedJob) return;

		setQueueing(true);
		try {
			// Update item selection in backend
			await api.updateMetadataItemSelection([...selectedItems], true);
			
			// Queue selected items
			const response = await api.queueSelectedMetadataItems(selectedJob.id, formatId);
			
			// Clear selection
			setSelectedItems(new Set());
			
			onError?.(
				`Queued ${response.total} items for download!`,
				'success'
			);
		} catch (err) {
			// Handle Pydantic validation errors (422)
			let errorMessage = 'Failed to queue items';
			if (err.response?.data?.detail) {
				const detail = err.response.data.detail;
				if (Array.isArray(detail)) {
					// Pydantic validation error format
					errorMessage = detail.map(e => `${e.loc.join('.')}: ${e.msg}`).join(', ');
				} else if (typeof detail === 'string') {
					errorMessage = detail;
				} else {
					errorMessage = JSON.stringify(detail);
				}
			}
			onError?.(errorMessage);
		} finally {
			setQueueing(false);
		}
	}, [selectedItems, selectedJob, formatId, onError]);

	const handleCancelJob = useCallback(async (jobId) => {
		try {
			await api.cancelMetadataJob(jobId);
			await loadJobs();
		} catch (err) {
			onError?.('Failed to cancel job');
		}
	}, [onError, loadJobs]);

	return (
		<Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
			{/* URL Input */}
			<Card>
				<CardContent>
					<Typography variant="h6" gutterBottom>
						Channel Metadata Processing
					</Typography>
					<Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
						Start background processing to get detailed metadata for all releases on a channel.
						This will analyze track counts and filter out music videos.
					</Typography>
					
					<form onSubmit={handleUrlSubmit}>
						<Box sx={{ display: 'flex', gap: 1, flexDirection: { xs: 'column', sm: 'row' } }}>
							<TextField
								fullWidth
								variant="outlined"
								label="YouTube Channel URL"
								placeholder="https://www.youtube.com/@ArtistName"
								value={url}
								onChange={(e) => setUrl(e.target.value)}
								disabled={loading}
								size="small"
							/>
							<Button
								type="submit"
								variant="contained"
								disabled={loading || !url.trim()}
								startIcon={<PlaylistAddIcon />}
								sx={{ minWidth: { xs: 'auto', sm: 120 }, width: { xs: '100%', sm: 'auto' } }}
							>
								{loading ? 'Starting...' : 'Start'}
							</Button>
						</Box>
					</form>
				</CardContent>
			</Card>

			{/* Jobs List */}
			<Card>
				<CardContent>
					<Typography variant="h6" gutterBottom>
						Processing Jobs
					</Typography>
					
					{jobs.length === 0 ? (
						<Alert severity="info">
							No metadata processing jobs yet. Start one above!
						</Alert>
					) : (
						<List>
							{jobs.map((job, index) => (
								<React.Fragment key={job.id}>
									{index > 0 && <Divider />}
									<ListItem
										button={job.status === 'completed'}
										onClick={() => job.status === 'completed' && handleJobSelect(job)}
										selected={selectedJob?.id === job.id}
									>
										<ListItemText
											primary={job.channel_name || 'Unknown Channel'}
											secondary={
												<Box>
													<Typography variant="caption" component="div">
														Created: {new Date(job.created_at).toLocaleString()}
													</Typography>
													{job.status === 'processing' && (
														<LinearProgress
															variant="determinate"
															value={job.progress}
															sx={{ mt: 1, width: '100%' }}
														/>
													)}
													{job.status === 'completed' && (
														<Typography variant="caption" color="text.secondary">
															Found {job.total_items || 0} items
														</Typography>
													)}
													{job.error_message && (
														<Typography variant="caption" color="error">
															Error: {job.error_message}
														</Typography>
													)}
												</Box>
											}
										/>
										<ListItemSecondaryAction>
											<Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
												<Chip
													label={job.status}
													color={
														job.status === 'completed' ? 'success' :
														job.status === 'processing' ? 'primary' :
														job.status === 'failed' ? 'error' : 'default'
													}
													size="small"
												/>
												{job.status === 'processing' && (
													<Tooltip title="Cancel Job">
														<IconButton
															size="small"
															onClick={(e) => {
																e.stopPropagation();
																handleCancelJob(job.id);
															}}
														>
															<CancelIcon />
														</IconButton>
													</Tooltip>
												)}
											</Box>
										</ListItemSecondaryAction>
									</ListItem>
								</React.Fragment>
							))}
						</List>
					)}
				</CardContent>
			</Card>

			{/* Job Items (when job is selected and completed) */}
			{selectedJob && selectedJob.status === 'completed' && (
				<Card>
					<CardContent>
						{/* Header with title and format selector */}
					<Box sx={{ mb: 2 }}>
						<Box sx={{ mb: 2 }}>
							<Typography variant="h6">
								{selectedJob.channel_name} - Releases
							</Typography>
							<Typography variant="caption" color="text.secondary">
								Showing {filteredItems.length} of {jobItems.length} releases
							</Typography>
						</Box>
						<Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
							<FormControl size="small" sx={{ minWidth: { xs: 120, sm: 140 }, flexGrow: { xs: 1, sm: 0 } }}>
								<InputLabel>Format</InputLabel>
								<Select
									value={formatId}
									label="Format"
									onChange={(e) => setFormatId(e.target.value)}
								>
									<MenuItem value="bestaudio/best">Best Audio</MenuItem>
									<MenuItem value="320">MP3 320k</MenuItem>
									<MenuItem value="256">MP3 256k</MenuItem>
									<MenuItem value="192">MP3 192k</MenuItem>
								</Select>
							</FormControl>
							
							<Button
								variant="contained"
								startIcon={<DownloadIcon />}
								onClick={handleQueueSelected}
								disabled={selectedItems.size === 0 || queueing}
								sx={{ minWidth: { xs: 120, sm: 140 }, flexGrow: { xs: 1, sm: 0 } }}
							>
								{queueing ? 'Queueing...' : `Queue (${selectedItems.size})`}
							</Button>
					</Box>

					{/* Filter Toggles */}
					<Box sx={{ mb: 2 }}>
						<Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
							<FilterIcon fontSize="small" color="action" />
							<Typography variant="body2" color="text.secondary">
								Show:
							</Typography>
						</Box>
						<ToggleButtonGroup
							value={activeFilters}
							onChange={handleFilterToggle}
							size="small"
							aria-label="release type filter"
							sx={{ 
								display: 'flex', 
								flexWrap: 'wrap', 
								gap: 0.5,
								'& .MuiToggleButtonGroup-grouped': {
									border: '1px solid rgba(255,255,255,0.12)',
									marginLeft: 0,
									'&:not(:first-of-type)': {
										borderRadius: 1,
									},
									'&:first-of-type': {
										borderRadius: 1,
									},
								}
							}}
						>
							<ToggleButton value="album" sx={{ textTransform: 'none', px: { xs: 1, sm: 2 } }}>
								<Badge badgeContent={stats.album} color="primary" sx={{ mr: { xs: 0.5, sm: 1 } }}>
									<AlbumIcon fontSize="small" />
								</Badge>
								<Box component="span" sx={{ display: { xs: 'none', sm: 'inline' } }}>Albums</Box>
							</ToggleButton>
							<ToggleButton value="ep" sx={{ textTransform: 'none', px: { xs: 1, sm: 2 } }}>
								<Badge badgeContent={stats.ep} color="info" sx={{ mr: { xs: 0.5, sm: 1 } }}>
									<EPIcon fontSize="small" />
								</Badge>
								<Box component="span" sx={{ display: { xs: 'none', sm: 'inline' } }}>EPs</Box>
							</ToggleButton>
							<ToggleButton value="single" sx={{ textTransform: 'none', px: { xs: 1, sm: 2 } }}>
								<Badge badgeContent={stats.single} color="secondary" sx={{ mr: { xs: 0.5, sm: 1 } }}>
									<SingleIcon fontSize="small" />
								</Badge>
								<Box component="span" sx={{ display: { xs: 'none', sm: 'inline' } }}>Singles</Box>
							</ToggleButton>
							<ToggleButton value="playlist" sx={{ textTransform: 'none', px: { xs: 1, sm: 2 } }}>
								<Badge badgeContent={stats.playlist} color="default" sx={{ mr: { xs: 0.5, sm: 1 } }}>
									<PlaylistIcon fontSize="small" />
								</Badge>
								<Box component="span" sx={{ display: { xs: 'none', sm: 'inline' } }}>Playlists</Box>
						</ToggleButton>
					</ToggleButtonGroup>
				</Box>
						bgcolor: alpha('#8B5CF6', 0.05),
						borderRadius: 2,
						border: '1px solid',
						borderColor: alpha('#8B5CF6', 0.1),
					}}>
						<Typography variant="body2" sx={{ mb: 1.5, fontWeight: 600, fontSize: { xs: '0.8125rem', sm: '0.875rem' } }}>
							Quick Selection:
						</Typography>
						<Box sx={{ display: 'flex', gap: { xs: 0.75, sm: 1 }, flexWrap: 'wrap' }}>
								{/* Albums */}
								{stats.album > 0 && (
									<Chip
										icon={<AlbumIcon />}
										label={`${isTypeFullySelected('album') ? 'Deselect' : 'Select'} ${stats.album} Album${stats.album > 1 ? 's' : ''}`}
										onClick={() => isTypeFullySelected('album') ? handleDeselectType('album') : handleSelectType('album')}
										color={isTypeFullySelected('album') ? 'primary' : 'default'}
										variant={isTypeFullySelected('album') ? 'filled' : 'outlined'}
										sx={{ cursor: 'pointer', fontSize: { xs: '0.75rem', sm: '0.8125rem' } }}
										size="small"
									/>
								)}
								
								{/* EPs */}
								{stats.ep > 0 && (
									<Chip
										icon={<EPIcon />}
										label={`${isTypeFullySelected('ep') ? 'Deselect' : 'Select'} ${stats.ep} EP${stats.ep > 1 ? 's' : ''}`}
										onClick={() => isTypeFullySelected('ep') ? handleDeselectType('ep') : handleSelectType('ep')}
										color={isTypeFullySelected('ep') ? 'info' : 'default'}
										variant={isTypeFullySelected('ep') ? 'filled' : 'outlined'}
										sx={{ cursor: 'pointer', fontSize: { xs: '0.75rem', sm: '0.8125rem' } }}
										size="small"
									/>
								)}
								
								{/* Singles */}
								{stats.single > 0 && (
									<Chip
										icon={<SingleIcon />}
										label={`${isTypeFullySelected('single') ? 'Deselect' : 'Select'} ${stats.single} Single${stats.single > 1 ? 's' : ''}`}
										onClick={() => isTypeFullySelected('single') ? handleDeselectType('single') : handleSelectType('single')}
										color={isTypeFullySelected('single') ? 'secondary' : 'default'}
										variant={isTypeFullySelected('single') ? 'filled' : 'outlined'}
										sx={{ cursor: 'pointer', fontSize: { xs: '0.75rem', sm: '0.8125rem' } }}
										size="small"
									/>
								)}
								
								{/* Playlists */}
								{stats.playlist > 0 && (
									<Chip
										icon={<PlaylistIcon />}
										label={`${isTypeFullySelected('playlist') ? 'Deselect' : 'Select'} ${stats.playlist} Playlist${stats.playlist > 1 ? 's' : ''}`}
										onClick={() => isTypeFullySelected('playlist') ? handleDeselectType('playlist') : handleSelectType('playlist')}
										color={isTypeFullySelected('playlist') ? 'default' : 'default'}
										variant={isTypeFullySelected('playlist') ? 'filled' : 'outlined'}
										sx={{ cursor: 'pointer', fontSize: { xs: '0.75rem', sm: '0.8125rem' } }}
										size="small"
									/>
								)}
								
								<Divider orientation="vertical" flexItem sx={{ mx: 1, display: { xs: 'none', sm: 'block' } }} />
								
								{/* Select/Deselect All */}
								<Chip
									icon={<SelectAllIcon />}
									label="All Visible"
									onClick={handleSelectAll}
									disabled={filteredItems.length === 0}
									color="success"
									variant="outlined"
									sx={{ cursor: 'pointer', fontSize: { xs: '0.75rem', sm: '0.8125rem' } }}
									size="small"
								/>
								
								<Chip
									icon={<DeselectAllIcon />}
									label="Clear"
									onClick={handleDeselectAll}
									disabled={selectedItems.size === 0}
									variant="outlined"
									sx={{ cursor: 'pointer', fontSize: { xs: '0.75rem', sm: '0.8125rem' } }}
									size="small"
								/>
							</Box>
						</Box>

						{loadingItems ? (
							<Box sx={{ textAlign: 'center', py: 2 }}>
								<LinearProgress />
								<Typography variant="body2" sx={{ mt: 1 }}>
									Loading items...
								</Typography>
							</Box>
						) : filteredItems.length === 0 ? (
							<Alert severity="info" sx={{ mt: 2 }}>
								No items match the current filters. Try enabling more release types or clearing your filters.
							</Alert>
						) : (
							<List dense>
								{filteredItems.map((item, index) => (
									<React.Fragment key={item.id}>
										{index > 0 && <Divider />}
										<ListItem sx={{ 
											pl: 0,
											bgcolor: selectedItems.has(item.id) ? alpha('#8B5CF6', 0.08) : 'transparent',
											borderRadius: 1,
											transition: 'background-color 0.2s',
											'&:hover': {
												bgcolor: selectedItems.has(item.id) ? alpha('#8B5CF6', 0.12) : alpha('#8B5CF6', 0.04)
											}
										}}>
											<Checkbox
												checked={selectedItems.has(item.id)}
												onChange={() => handleItemToggle(item.id)}
											/>
											<ListItemText
												primary={
													<Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
														{getReleaseIcon(item.release_type)}
														<Typography variant="body2" sx={{ fontWeight: 500, flexGrow: 1 }}>
															{item.title}
														</Typography>
														<Chip
															label={item.release_type || 'unknown'}
															size="small"
															color={getReleaseColor(item.release_type)}
														/>
													</Box>
												}
												secondary={
													<Box sx={{ display: 'flex', gap: 2, mt: 0.5, flexWrap: 'wrap' }}>
														<Typography variant="caption" color="text.secondary">
															{item.track_count ? `${item.track_count} tracks` : 'Unknown tracks'}
														</Typography>
														{item.release_year && (
															<Typography variant="caption" color="text.secondary">
																{item.release_year}
															</Typography>
														)}
														{item.view_count && (
															<Typography variant="caption" color="text.secondary">
																{item.view_count.toLocaleString()} views
															</Typography>
														)}
													</Box>
												}
											/>
										</ListItem>
									</React.Fragment>
								))}
							</List>
						)}
					</CardContent>
				</Card>
			)}
		</Box>
	);
}