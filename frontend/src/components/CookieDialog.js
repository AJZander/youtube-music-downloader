// frontend/src/components/CookieDialog.js
import React, { useEffect, useState } from 'react';
import {
	Alert, Box, Button, Collapse, Dialog, DialogActions,
	DialogContent, DialogTitle, IconButton, Link, TextField, Typography,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import CloseIcon from '@mui/icons-material/Close';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';

import { api } from '../api';

// Paper styling shared across all dialogs in the app
const paperSx = {
	bgcolor: '#1a1a24',
	border: '1px solid rgba(255,255,255,0.08)',
	borderRadius: 2,
};

export default function CookieDialog({ open, onClose, onSuccess }) {
	const [info, setInfo] = useState(null);
	const [content, setContent] = useState('');
	const [busy, setBusy] = useState(false);
	const [err, setErr] = useState(null);
	const [help, setHelp] = useState(false);

	// Load cookie status whenever dialog opens
	useEffect(() => {
		if (open) {
			api.getCookiesInfo()
				.then(setInfo)
				.catch(() => setInfo(null));
			setErr(null);
			setContent('');
		}
	}, [open]);

	const handleSave = async () => {
		setErr(null);
		setBusy(true);
		try {
			const res = await api.saveCookies(content.trim());
			onSuccess(res.message || 'Cookies saved!');
			onClose();
		} catch (e) {
			setErr(e.response?.data?.detail || 'Failed to save cookies');
		} finally {
			setBusy(false);
		}
	};

	const handleDelete = async () => {
		setBusy(true);
		try {
			await api.deleteCookies();
			setInfo({ exists: false });
			onSuccess('Cookies removed');
		} catch {
			setErr('Failed to remove cookies');
		} finally {
			setBusy(false);
		}
	};

	return (
		<Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth PaperProps={{ sx: paperSx }}>

			<DialogTitle sx={{
				display: 'flex', justifyContent: 'space-between', alignItems: 'center',
				color: '#fff', pb: 1,
			}}>
				<Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
					YouTube Authentication
					<IconButton size="small" onClick={() => setHelp(h => !h)}
						sx={{ color: 'rgba(255,255,255,0.35)', '&:hover': { color: '#8B5CF6' } }}>
						<HelpOutlineIcon fontSize="small" />
					</IconButton>
				</Box>
				<IconButton onClick={onClose} size="small" sx={{ color: 'rgba(255,255,255,0.35)' }}>
					<CloseIcon />
				</IconButton>
			</DialogTitle>

			<DialogContent>

				{/* Cookie status banner */}
				{info && (
					<Box mb={2}>
						{info.exists ? (
							<Alert
								severity="success"
								icon={<CheckCircleIcon />}
								action={
									<Button size="small" color="error" onClick={handleDelete} disabled={busy}>
										Remove
									</Button>
								}
								sx={{ bgcolor: 'rgba(16,185,129,0.08)', color: '#fff', border: '1px solid rgba(16,185,129,0.3)' }}
							>
								Cookies active — age-restricted downloads enabled.
							</Alert>
						) : (
							<Alert
								severity="warning"
								icon={<WarningAmberIcon />}
								sx={{ bgcolor: 'rgba(245,158,11,0.08)', color: '#fff', border: '1px solid rgba(245,158,11,0.3)' }}
							>
								No cookies. Age-restricted content cannot be downloaded.
							</Alert>
						)}
					</Box>
				)}

				{/* How-to guide */}
				<Collapse in={help}>
					<Box sx={{ mb: 2, p: 1.5, borderRadius: 1, bgcolor: '#252530', border: '1px solid rgba(255,255,255,0.07)' }}>
						<Typography sx={{ color: '#fff', fontWeight: 600, fontSize: '0.8rem', mb: 0.75 }}>
							How to export cookies
						</Typography>
						{[
							<>Install a cookie-export browser extension:<br />
								&nbsp;• Chrome/Edge: <Link href="https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc" target="_blank" rel="noopener" sx={{ color: '#8B5CF6' }}>Get cookies.txt LOCALLY</Link><br />
								&nbsp;• Firefox: <Link href="https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/" target="_blank" rel="noopener" sx={{ color: '#8B5CF6' }}>cookies.txt</Link>
							</>,
							'Sign in to YouTube.',
							'Use the extension to export cookies from youtube.com.',
							'Paste the full exported text into the box below.',
						].map((step, i) => (
							<Typography key={i} sx={{ color: 'rgba(255,255,255,0.5)', fontSize: '0.77rem', mb: 0.5 }}>
								{i + 1}. {step}
							</Typography>
						))}
					</Box>
				</Collapse>

				{/* Error */}
				{err && (
					<Alert severity="error" onClose={() => setErr(null)} sx={{ mb: 2 }}>
						{err}
					</Alert>
				)}

				{/* Paste area */}
				<TextField
					fullWidth multiline rows={7}
					placeholder={
						'# Netscape HTTP Cookie File\n' +
						'.youtube.com\tTRUE\t/\tTRUE\t0\tCOOKIE_NAME\tcookie_value\n...'
					}
					value={content}
					onChange={e => setContent(e.target.value)}
					disabled={busy}
					sx={{
						'& .MuiOutlinedInput-root': {
							bgcolor: '#252530', fontFamily: 'monospace', fontSize: '0.72rem',
							color: '#fff',
							'& fieldset': { borderColor: 'rgba(255,255,255,0.1)' },
							'&:hover fieldset': { borderColor: 'rgba(255,255,255,0.2)' },
							'&.Mui-focused fieldset': { borderColor: '#8B5CF6', borderWidth: 1 },
						},
					}}
				/>
				<Typography sx={{ mt: 0.75, fontSize: '0.7rem', color: 'rgba(255,255,255,0.25)' }}>
					Cookies are stored inside the Docker container and never transmitted externally.
				</Typography>
			</DialogContent>

			<DialogActions sx={{ px: 3, pb: 2, gap: 1 }}>
				<Button onClick={onClose} disabled={busy}
					sx={{ color: 'rgba(255,255,255,0.4)', textTransform: 'none' }}>
					Cancel
				</Button>
				<Button
					onClick={handleSave}
					disabled={busy || !content.trim()}
					variant="contained"
					sx={{
						textTransform: 'none', fontWeight: 600,
						background: 'linear-gradient(135deg, #8B5CF6, #6D28D9)',
						boxShadow: 'none',
						'&:hover': { opacity: 0.9 },
						'&.Mui-disabled': { bgcolor: '#252530', color: 'rgba(255,255,255,0.3)' },
					}}
				>
					{busy ? 'Saving…' : 'Save Cookies'}
				</Button>
			</DialogActions>
		</Dialog>
	);
}