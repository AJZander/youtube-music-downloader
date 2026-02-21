import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Box,
  Typography,
  Alert,
  Chip,
  IconButton,
  Link,
  Collapse,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ErrorIcon from '@mui/icons-material/Error';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';

function CookieManager({ open, onClose, onSave }) {
  const [cookies, setCookies] = useState('');
  const [cookiesInfo, setCookiesInfo] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [showHelp, setShowHelp] = useState(false);

  useEffect(() => {
    if (open) {
      fetchCookiesInfo();
    }
  }, [open]);

  const fetchCookiesInfo = async () => {
    try {
      const apiUrl = process.env.REACT_APP_API_URL || 'http://localhost:8000';
      const response = await fetch(`${apiUrl}/cookies`);
      const data = await response.json();
      setCookiesInfo(data);
    } catch (err) {
      console.error('Failed to fetch cookies info:', err);
    }
  };

  const handleSave = async () => {
    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const apiUrl = process.env.REACT_APP_API_URL || 'http://localhost:8000';
      const response = await fetch(`${apiUrl}/cookies`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ cookies_content: cookies }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to save cookies');
      }

      const data = await response.json();
      setSuccess(data.message);
      setCookies('');
      fetchCookiesInfo();
      
      if (onSave) {
        onSave();
      }

      setTimeout(() => {
        onClose();
      }, 2000);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async () => {
    setLoading(true);
    setError(null);

    try {
      const apiUrl = process.env.REACT_APP_API_URL || 'http://localhost:8000';
      const response = await fetch(`${apiUrl}/cookies`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        throw new Error('Failed to delete cookies');
      }

      const data = await response.json();
      setSuccess(data.message);
      fetchCookiesInfo();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog 
      open={open} 
      onClose={onClose} 
      maxWidth="md" 
      fullWidth
      PaperProps={{
        sx: {
          background: 'var(--bg-elevated)',
          borderRadius: 2,
          border: '1px solid var(--border-subtle)',
        }
      }}
    >
      <DialogTitle sx={{ 
        display: 'flex', 
        justifyContent: 'space-between', 
        alignItems: 'center',
        color: 'var(--text-primary)',
        pb: 1
      }}>
        <Box display="flex" alignItems="center" gap={1}>
          <span>YouTube Authentication</span>
          <IconButton 
            size="small" 
            onClick={() => setShowHelp(!showHelp)}
            sx={{ color: 'var(--text-tertiary)' }}
          >
            <HelpOutlineIcon fontSize="small" />
          </IconButton>
        </Box>
        <IconButton onClick={onClose} size="small" sx={{ color: 'var(--text-tertiary)' }}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ pt: 2 }}>
        {/* Current Status */}
        {cookiesInfo && (
          <Box mb={2}>
            {cookiesInfo.exists ? (
              <Alert 
                severity="success" 
                icon={<CheckCircleIcon />}
                sx={{ 
                  background: 'rgba(76, 175, 80, 0.1)',
                  color: 'var(--text-primary)',
                  border: '1px solid rgba(76, 175, 80, 0.3)',
                }}
              >
                <Box display="flex" justifyContent="space-between" alignItems="center">
                  <span>✓ YouTube cookies are active. Age-restricted content can be downloaded.</span>
                  <Button 
                    size="small" 
                    variant="outlined" 
                    color="error"
                    onClick={handleDelete}
                    disabled={loading}
                    sx={{ ml: 2 }}
                  >
                    Remove
                  </Button>
                </Box>
              </Alert>
            ) : (
              <Alert 
                severity="warning"
                icon={<ErrorIcon />}
                sx={{ 
                  background: 'rgba(255, 152, 0, 0.1)',
                  color: 'var(--text-primary)',
                  border: '1px solid rgba(255, 152, 0, 0.3)',
                }}
              >
                No YouTube cookies configured. Age-restricted videos cannot be downloaded.
              </Alert>
            )}
          </Box>
        )}

        {/* Help Section */}
        <Collapse in={showHelp}>
          <Box 
            mb={2} 
            p={2} 
            sx={{ 
              background: 'var(--bg-tertiary)', 
              borderRadius: 1,
              border: '1px solid var(--border-subtle)',
            }}
          >
            <Typography variant="subtitle2" sx={{ color: 'var(--text-primary)', mb: 1, fontWeight: 600 }}>
              How to export YouTube cookies:
            </Typography>
            <Typography variant="body2" sx={{ color: 'var(--text-secondary)', mb: 1 }}>
              1. Install a cookie export extension in your browser:
            </Typography>
            <Box pl={2} mb={1}>
              <Typography variant="body2" sx={{ color: 'var(--text-secondary)' }}>
                • <Link href="https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc" target="_blank" rel="noopener" sx={{ color: 'var(--accent-primary)' }}>Get cookies.txt LOCALLY</Link> (Chrome/Edge)
              </Typography>
              <Typography variant="body2" sx={{ color: 'var(--text-secondary)' }}>
                • <Link href="https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/" target="_blank" rel="noopener" sx={{ color: 'var(--accent-primary)' }}>cookies.txt</Link> (Firefox)
              </Typography>
            </Box>
            <Typography variant="body2" sx={{ color: 'var(--text-secondary)', mb: 1 }}>
              2. Sign in to <Link href="https://youtube.com" target="_blank" rel="noopener" sx={{ color: 'var(--accent-primary)' }}>YouTube</Link>
            </Typography>
            <Typography variant="body2" sx={{ color: 'var(--text-secondary)', mb: 1 }}>
              3. Use the extension to export cookies for youtube.com
            </Typography>
            <Typography variant="body2" sx={{ color: 'var(--text-secondary)' }}>
              4. Paste the exported cookies content below
            </Typography>
          </Box>
        </Collapse>

        {/* Error/Success Messages */}
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        {success && (
          <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>
            {success}
          </Alert>
        )}

        {/* Cookie Input */}
        <TextField
          fullWidth
          multiline
          rows={8}
          placeholder="Paste your YouTube cookies here (Netscape format)&#10;&#10;Example:&#10;# Netscape HTTP Cookie File&#10;.youtube.com	TRUE	/	TRUE	0	COOKIE_NAME	cookie_value&#10;..."
          value={cookies}
          onChange={(e) => setCookies(e.target.value)}
          disabled={loading}
          sx={{
            '& .MuiOutlinedInput-root': {
              background: 'var(--bg-tertiary)',
              fontFamily: 'monospace',
              fontSize: '0.75rem',
              color: 'var(--text-primary)',
              '& fieldset': {
                borderColor: 'var(--border-subtle)',
              },
              '&:hover fieldset': {
                borderColor: 'var(--border-medium)',
              },
              '&.Mui-focused fieldset': {
                borderColor: 'var(--accent-primary)',
              },
            },
          }}
        />

        <Typography variant="caption" sx={{ color: 'var(--text-tertiary)', mt: 1, display: 'block' }}>
          ⚠️ Your cookies are stored locally in the container and never transmitted elsewhere.
        </Typography>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button 
          onClick={onClose} 
          disabled={loading}
          sx={{ 
            color: 'var(--text-secondary)',
            textTransform: 'none',
          }}
        >
          Cancel
        </Button>
        <Button
          onClick={handleSave}
          disabled={loading || !cookies.trim()}
          variant="contained"
          sx={{
            background: 'var(--accent-gradient)',
            textTransform: 'none',
            '&:hover': {
              background: 'var(--accent-gradient)',
              opacity: 0.9,
            },
          }}
        >
          {loading ? 'Saving...' : 'Save Cookies'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default CookieManager;
