import React, { useState, useEffect, useCallback } from 'react';
import {
  Container,
  Box,
  ThemeProvider,
  createTheme,
  CssBaseline,
  Alert,
  Snackbar,
} from '@mui/material';
import { downloadAPI, WebSocketService } from './api';
import DownloadForm from './components/DownloadForm';
import DownloadList from './components/DownloadList';
import Header from './components/Header';
import ColorPicker from './components/ColorPicker';
import { ThemeProvider as CustomThemeProvider } from './ThemeContext';

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#8B5CF6',
    },
    background: {
      default: '#0f0f1a',
      paper: '#1a1a24',
    },
    text: {
      primary: '#ffffff',
      secondary: '#a0a0b0',
    },
  },
  typography: {
    fontFamily: '-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", "Roboto", sans-serif',
    fontSize: 13,
    h4: { fontWeight: 600, fontSize: '1.75rem' },
    h5: { fontWeight: 600, fontSize: '1.25rem' },
    h6: { fontWeight: 500, fontSize: '1rem' },
    body1: { fontSize: '0.875rem' },
    body2: { fontSize: '0.8125rem' },
  },
  components: {
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
        },
      },
    },
  },
});

function App() {
  const [downloads, setDownloads] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [successMessage, setSuccessMessage] = useState(null);
  const [wsService] = useState(() => {
    const wsUrl = process.env.REACT_APP_WS_URL || 'ws://localhost:8000/ws';
    return new WebSocketService(wsUrl);
  });

  // Fetch downloads
  const fetchDownloads = useCallback(async () => {
    try {
      const data = await downloadAPI.getDownloads();
      setDownloads(data);
      setLoading(false);
    } catch (err) {
      console.error('Failed to fetch downloads:', err);
      setError('Failed to fetch downloads');
      setLoading(false);
    }
  }, []);

  // Initialize WebSocket and fetch data
  useEffect(() => {
    // Initial fetch
    fetchDownloads();

    // Setup WebSocket
    wsService
      .connect()
      .then(() => {
        console.log('WebSocket connected successfully');
        wsService.startPing();
      })
      .catch((err) => {
        console.error('WebSocket connection failed:', err);
        setError('Real-time updates unavailable');
      });

    // Listen for initial data
    wsService.on('initial_data', (data) => {
      console.log('Received initial data:', data);
      setDownloads(data);
    });

    // Listen for download updates
    wsService.on('download_update', (data) => {
      console.log('Download update:', data);
      setDownloads((prevDownloads) => {
        const index = prevDownloads.findIndex((d) => d.id === data.id);
        if (index >= 0) {
          const newDownloads = [...prevDownloads];
          newDownloads[index] = data;
          return newDownloads;
        } else {
          return [data, ...prevDownloads];
        }
      });
    });

    // Cleanup
    return () => {
      wsService.stopPing();
      wsService.disconnect();
    };
  }, [wsService, fetchDownloads]);

  // Handle new download
  const handleDownload = async (url) => {
    try {
      const download = await downloadAPI.createDownload(url);
      setSuccessMessage('Download added to queue!');
      // The download will be added via WebSocket update
      console.log('Created download:', download);
    } catch (err) {
      console.error('Failed to create download:', err);
      setError(err.response?.data?.detail || 'Failed to add download');
    }
  };

  // Handle cancel download
  const handleCancelDownload = async (id) => {
    try {
      await downloadAPI.cancelDownload(id);
      setSuccessMessage('Download cancelled');
    } catch (err) {
      console.error('Failed to cancel download:', err);
      setError('Failed to cancel download');
    }
  };

  // Close error/success messages
  const handleCloseError = () => setError(null);
  const handleCloseSuccess = () => setSuccessMessage(null);

  return (
    <CustomThemeProvider>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        <Box
          sx={{
            minHeight: '100vh',
            background: 'var(--bg-primary)',
            py: 2,
            px: 2,
          }}
        >
          {/* Color Picker - Fixed Position */}
          <Box
            sx={{
              position: 'fixed',
              top: 16,
              right: 16,
              zIndex: 1000,
            }}
          >
            <ColorPicker />
          </Box>

          <Container maxWidth="lg" sx={{ px: { xs: 1, sm: 2 } }}>
            <Header />
            
            {/* Download Form Section */}
            <Box
              sx={{
                p: { xs: 2, sm: 2.5 },
                mt: 3,
                borderRadius: 1.5,
                background: 'var(--bg-secondary)',
                border: '1px solid var(--border-subtle)',
                boxShadow: 'var(--shadow-sm)',
              }}
            >
              <DownloadForm onSubmit={handleDownload} />
            </Box>

            {/* Downloads List Section */}
            <Box
              sx={{
                p: { xs: 2, sm: 2.5 },
                mt: 2,
                borderRadius: 1.5,
                background: 'var(--bg-secondary)',
                border: '1px solid var(--border-subtle)',
                boxShadow: 'var(--shadow-sm)',
              }}
            >
              <DownloadList
                downloads={downloads}
                loading={loading}
                onCancel={handleCancelDownload}
              />
            </Box>
          </Container>

          {/* Error Snackbar */}
          <Snackbar
            open={!!error}
            autoHideDuration={6000}
            onClose={handleCloseError}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
          >
            <Alert 
              onClose={handleCloseError} 
              severity="error" 
              sx={{ 
                width: '100%',
                background: '#2d1b1b',
                border: '1px solid #ff5252',
                fontSize: '0.8125rem',
              }}
            >
              {error}
            </Alert>
          </Snackbar>

          {/* Success Snackbar */}
          <Snackbar
            open={!!successMessage}
            autoHideDuration={3000}
            onClose={handleCloseSuccess}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
          >
            <Alert 
              onClose={handleCloseSuccess} 
              severity="success" 
              sx={{ 
                width: '100%',
                background: '#1b2d1b',
                border: '1px solid #4caf50',
                fontSize: '0.8125rem',
              }}
            >
              {successMessage}
            </Alert>
          </Snackbar>
        </Box>
      </ThemeProvider>
    </CustomThemeProvider>
  );
}

export default App;
