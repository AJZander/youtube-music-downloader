// frontend/src/components/FormatSelector.js
import React, { useState } from 'react';
import {
	Box,
	Button,
	Chip,
	CircularProgress,
	Dialog,
	DialogActions,
	DialogContent,
	DialogTitle,
	FormControlLabel,
	IconButton,
	Radio,
	RadioGroup,
	Typography,
	Alert,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import MusicNoteIcon from '@mui/icons-material/MusicNote';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import InfoIcon from '@mui/icons-material/Info';
import LockIcon from '@mui/icons-material/Lock';

const paperSx = {
	bgcolor: '#1a1a24',
	border: '1px solid rgba(255,255,255,0.08)',
	borderRadius: 2,
	minWidth: 600,
};

export default function FormatSelector({ open, url, formats, metadata, authenticated, onSelect, onClose, loading }) {
	const [selectedFormat, setSelectedFormat] = useState(null);

	// Auto-select the recommended format when dialog opens
	React.useEffect(() => {
		if (formats && formats.length > 0) {
			const recommended = formats.find(f => f.recommended);
			setSelectedFormat(recommended?.format_id || formats[0]?.format_id);
		}
	}, [formats]);

	const handleSelect = () => {
		if (selectedFormat) {
			onSelect(selectedFormat);
		}
	};

	const formatSize = (sizeMb) => {
		if (!sizeMb) return '~Unknown size';
		if (sizeMb < 1) return `~${Math.round(sizeMb * 1024)} KB`;
		return `~${sizeMb.toFixed(1)} MB`;
	};

	return (
		<Dialog open={open} onClose={onClose} maxWidth="md" fullWidth PaperProps={{ sx: paperSx }}>
			<DialogTitle sx={{
				display: 'flex',
				justifyContent: 'space-between',
				alignItems: 'center',
				color: '#fff',
				pb: 1,
			}}>
				<Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
					<MusicNoteIcon sx={{ color: '#8B5CF6' }} />
					Select Audio Format
					{authenticated && <LockIcon sx={{ fontSize: 18, color: '#10B981' }} />}
				</Box>
				<IconButton onClick={onClose} size="small" sx={{ color: 'rgba(255,255,255,0.35)' }}>
					<CloseIcon />
				</IconButton>
			</DialogTitle>

			<DialogContent>
				{loading ? (
					<Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', py: 4, gap: 2 }}>
						<CircularProgress size={40} sx={{ color: '#8B5CF6' }} />
						<Typography sx={{ color: 'rgba(255,255,255,0.5)', fontSize: '0.875rem' }}>
							Analyzing available formats...
						</Typography>
					</Box>
				) : (
					<>
						{/* Metadata */}
						{metadata && (
							<Box sx={{
								mb: 2,
								p: 1.5,
								borderRadius: 1.5,
								bgcolor: '#252530',
								border: '1px solid rgba(255,255,255,0.06)',
							}}>
								<Typography noWrap sx={{ color: '#fff', fontWeight: 600, fontSize: '0.875rem', mb: 0.5 }}>
									{metadata.title}
								</Typography>
								<Typography noWrap sx={{ color: 'rgba(255,255,255,0.4)', fontSize: '0.75rem' }}>
									{metadata.artist}
									{metadata.album && metadata.album !== 'Unknown Album' && ` · ${metadata.album}`}
									{metadata.total_tracks > 1 && ` · ${metadata.total_tracks} tracks`}
								</Typography>
							</Box>
						)}

						{/* Authentication Notice */}
						{authenticated ? (
							<Alert
								severity="info"
								icon={<LockIcon sx={{ fontSize: 18 }} />}
								sx={{
									mb: 2,
									bgcolor: 'rgba(16,185,129,0.08)',
									color: '#fff',
									border: '1px solid rgba(16,185,129,0.2)',
									'& .MuiAlert-icon': { color: '#10B981' },
								}}
							>
								<Typography sx={{ fontSize: '0.8rem', lineHeight: 1.4, mb: 0.5, fontWeight: 600 }}>
									🔒 Authenticated Session Active
								</Typography>
								<Typography sx={{ fontSize: '0.75rem', lineHeight: 1.4, color: 'rgba(255,255,255,0.7)' }}>
									YouTube restricts specific format selection when authenticated. Auto-select mode will provide the highest quality available for this content, including age-restricted material.
								</Typography>
							</Alert>
						) : (
							<Box sx={{
								mb: 2,
								p: 1.5,
								borderRadius: 1.5,
								bgcolor: 'rgba(139,92,246,0.08)',
								border: '1px solid rgba(139,92,246,0.2)',
								display: 'flex',
								gap: 1.5,
								alignItems: 'flex-start',
							}}>
								<InfoIcon sx={{ fontSize: 18, color: '#8B5CF6', mt: 0.25 }} />
								<Box>
									<Typography sx={{ color: '#fff', fontSize: '0.8rem', lineHeight: 1.4 }}>
										Different formats have different quality and file sizes.
										The recommended format is usually the best choice.
									</Typography>
								</Box>
							</Box>
						)}

						{/* Format Selection */}
						{formats && formats.length > 0 ? (
							<RadioGroup value={selectedFormat} onChange={(e) => setSelectedFormat(e.target.value)}>
								{formats.map((format) => (
									<Box
										key={format.format_id}
										onClick={() => setSelectedFormat(format.format_id)}
										sx={{
											mb: 1.5,
											p: 1.5,
											borderRadius: 1.5,
											bgcolor: selectedFormat === format.format_id
												? 'rgba(139,92,246,0.12)'
												: '#252530',
											border: selectedFormat === format.format_id
												? '1.5px solid #8B5CF6'
												: '1px solid rgba(255,255,255,0.06)',
											cursor: 'pointer',
											transition: 'all 0.2s',
											'&:hover': {
												borderColor: selectedFormat === format.format_id
													? '#8B5CF6'
													: 'rgba(255,255,255,0.12)',
												bgcolor: selectedFormat === format.format_id
													? 'rgba(139,92,246,0.12)'
													: 'rgba(255,255,255,0.02)',
											},
										}}
									>
										<Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.5 }}>
											<FormControlLabel
												value={format.format_id}
												control={
													<Radio
														sx={{
															color: 'rgba(255,255,255,0.2)',
															'&.Mui-checked': { color: '#8B5CF6' },
														}}
													/>
												}
												label=""
												sx={{ margin: 0 }}
											/>

											<Box sx={{ flex: 1, minWidth: 0 }}>
												<Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
													<Typography sx={{
														color: '#fff',
														fontWeight: 600,
														fontSize: '0.875rem',
													}}>
														{format.label}
													</Typography>
													{format.recommended && (
														<Chip
															icon={<AutoAwesomeIcon sx={{ fontSize: 12 }} />}
															label="RECOMMENDED"
															size="small"
															sx={{
																height: 20,
																fontSize: '0.65rem',
																fontWeight: 700,
																color: '#8B5CF6',
																bgcolor: 'rgba(139,92,246,0.15)',
																border: '1px solid rgba(139,92,246,0.3)',
																'& .MuiChip-icon': { color: '#8B5CF6', ml: '4px' },
															}}
														/>
													)}
												</Box>

												<Typography sx={{
													color: 'rgba(255,255,255,0.5)',
													fontSize: '0.75rem',
													lineHeight: 1.4,
													mb: 0.75,
												}}>
													{format.description}
												</Typography>

												<Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap' }}>
													{format.codec && (
														<Chip
															label={`Codec: ${format.codec.toUpperCase()}`}
															size="small"
															sx={{
																height: 18,
																fontSize: '0.65rem',
																color: 'rgba(255,255,255,0.4)',
																bgcolor: 'transparent',
																border: '1px solid rgba(255,255,255,0.12)',
															}}
														/>
													)}
													{format.bitrate && (
														<Chip
															label={`${format.bitrate} kbps`}
															size="small"
															sx={{
																height: 18,
																fontSize: '0.65rem',
																color: 'rgba(255,255,255,0.4)',
																bgcolor: 'transparent',
																border: '1px solid rgba(255,255,255,0.12)',
															}}
														/>
													)}
													{format.filesize_mb && (
														<Chip
															label={formatSize(format.filesize_mb)}
															size="small"
															sx={{
																height: 18,
																fontSize: '0.65rem',
																color: 'rgba(255,255,255,0.4)',
																bgcolor: 'transparent',
																border: '1px solid rgba(255,255,255,0.12)',
															}}
														/>
													)}
												</Box>
											</Box>
										</Box>
									</Box>
								))}
							</RadioGroup>
						) : (
							<Typography sx={{ color: 'rgba(255,255,255,0.5)', textAlign: 'center', py: 4 }}>
								No formats available
							</Typography>
						)}
					</>
				)}
			</DialogContent>

			<DialogActions sx={{ px: 3, pb: 2, gap: 1 }}>
				<Button
					onClick={onClose}
					disabled={loading}
					sx={{ color: 'rgba(255,255,255,0.4)', textTransform: 'none' }}
				>
					Cancel
				</Button>
				<Button
					onClick={handleSelect}
					disabled={loading || !selectedFormat}
					variant="contained"
					startIcon={<CheckCircleIcon />}
					sx={{
						textTransform: 'none',
						fontWeight: 600,
						background: 'linear-gradient(135deg, #8B5CF6, #6D28D9)',
						boxShadow: 'none',
						'&:hover': { opacity: 0.9 },
						'&.Mui-disabled': { bgcolor: '#252530', color: 'rgba(255,255,255,0.3)' },
					}}
				>
					{loading ? 'Loading...' : 'Download Selected Format'}
				</Button>
			</DialogActions>
		</Dialog>
	);
}