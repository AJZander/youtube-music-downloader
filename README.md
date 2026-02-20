# YouTube Music Downloader

<div align="center">

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-required-blue.svg)
![Python](https://img.shields.io/badge/python-3.11-blue.svg)
![React](https://img.shields.io/badge/react-18-blue.svg)

</div>

A production-grade web application for downloading music from YouTube Music. Built with FastAPI (Python) backend and React frontend, fully containerized with Docker.

![YouTube Music Downloader](https://img.shields.io/badge/status-production%20ready-success)

> **Note**: This tool is for personal use only. Only download content you own or have permission to download.

## 📸 Screenshots

<!-- Add screenshots of your application here -->
*Coming soon - screenshots of the web interface*

## Features

- 🎵 Download individual songs, albums, artists, and playlists from YouTube Music
- 📥 Queue multiple downloads simultaneously
- 📊 Real-time progress tracking via WebSocket
- 🎨 Modern, responsive UI with Material-UI
- 🐳 Fully containerized with Docker
- 🔄 Automatic retry and error handling
- 💾 High-quality audio downloads (up to 320kbps MP3)
- 🏷️ Automatic metadata tagging (artist, album, title)
- 🖼️ Embedded album artwork

## Architecture

### Backend (Python/FastAPI)
- **FastAPI**: Modern, high-performance web framework
- **yt-dlp**: Robust YouTube downloader
- **SQLAlchemy**: Database ORM for tracking downloads
- **WebSocket**: Real-time progress updates
- **Async/Await**: High-performance concurrent downloads

### Frontend (React)
- **React 18**: Modern UI framework
- **Material-UI**: Professional component library
- **WebSocket**: Real-time updates
- **Axios**: HTTP client

### Infrastructure
- **Docker**: Containerization
- **Docker Compose**: Multi-container orchestration
- **Nginx**: Frontend web server
- **SQLite**: Lightweight database

## Prerequisites

- Docker (20.10+)
- Docker Compose (2.0+)
- At least 2GB available disk space

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/AJZander/youtube-music-downloader.git
   cd youtube-music-downloader
   ```

2. **Create environment files (optional):**
   ```bash
   # Backend
   cp backend/.env.example backend/.env
   
   # Frontend
   cp frontend/.env.example frontend/.env
   ```

3. **Build and start the application:**
   ```bash
   docker-compose up -d --build
   ```

4. **Access the application:**
   - Frontend: http://localhost:80
   - Backend API: http://localhost:8000
   - API Documentation: http://localhost:8000/docs

5. **View logs:**
   ```bash
   # All services
   docker-compose logs -f
   
   # Backend only
   docker-compose logs -f backend
   
   # Frontend only
   docker-compose logs -f frontend
   ```

## Usage

1. **Open the web interface** at http://localhost:80

2. **Paste a YouTube Music URL:**
   - Individual song: `https://music.youtube.com/watch?v=...`
   - Album: `https://music.youtube.com/playlist?list=OLAK5uy_...`
   - Artist: `https://music.youtube.com/channel/...`
   - Playlist: `https://music.youtube.com/playlist?list=...`

3. **Click "Download"** - The download will be added to the queue

4. **Monitor progress** - Real-time updates show download status

5. **Access downloaded files** - Files are saved in `./downloads/` organized by Artist/Album/

## Configuration

### Backend Configuration (backend/.env)

```env
# Download settings
DOWNLOAD_DIR=/downloads
MAX_CONCURRENT_DOWNLOADS=3
AUDIO_FORMAT=mp3
AUDIO_QUALITY=320

# Application
DEBUG=false

# CORS
CORS_ORIGINS=http://localhost:3000,http://localhost:80
```

### Frontend Configuration (frontend/.env)

```env
REACT_APP_API_URL=http://localhost:8000
REACT_APP_WS_URL=ws://localhost:8000/ws
```

## API Endpoints

### REST API

- `GET /` - Root endpoint
- `GET /health` - Health check
- `POST /downloads` - Create new download
- `GET /downloads` - List all downloads
- `GET /downloads/{id}` - Get specific download
- `DELETE /downloads/{id}` - Cancel download
- `GET /downloads/status/active` - Get active downloads

### WebSocket

- `WS /ws` - Real-time updates

Full API documentation available at http://localhost:8000/docs

## File Structure

```
downloader/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI application
│   │   ├── config.py            # Configuration
│   │   ├── models.py            # Database models
│   │   ├── schemas.py           # Pydantic schemas
│   │   ├── database.py          # Database connection
│   │   ├── downloader.py        # Download manager
│   │   └── queue_service.py     # Queue management
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── public/
│   │   └── index.html
│   ├── src/
│   │   ├── components/
│   │   │   ├── Header.js
│   │   │   ├── DownloadForm.js
│   │   │   ├── DownloadList.js
│   │   │   └── DownloadItem.js
│   │   ├── App.js
│   │   ├── api.js
│   │   ├── index.js
│   │   └── index.css
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── package.json
│   └── .env.example
├── downloads/                    # Downloaded files (created automatically)
├── docker-compose.yml
└── README.md
```

## Advanced Usage

### Changing Download Quality

Edit `backend/.env`:
```env
AUDIO_QUALITY=192  # Options: 128, 192, 256, 320
```

### Changing Concurrent Downloads

Edit `backend/.env`:
```env
MAX_CONCURRENT_DOWNLOADS=5  # Increase for more simultaneous downloads
```

### Using Different Audio Format

Edit `backend/.env`:
```env
AUDIO_FORMAT=flac  # Options: mp3, flac, m4a, opus, wav
```

## Troubleshooting

### Container won't start
```bash
# Check logs
docker-compose logs backend
docker-compose logs frontend

# Restart services
docker-compose restart
```

### Downloads failing
- Check that ffmpeg is installed in the backend container
- Verify the YouTube URL is valid and accessible
- Check backend logs: `docker-compose logs backend`

### Can't access the web interface
```bash
# Check if containers are running
docker-compose ps

# Restart frontend
docker-compose restart frontend
```

### WebSocket not connecting
- Ensure backend is running: `curl http://localhost:8000/health`
- Check browser console for errors
- Verify WebSocket URL in frontend/.env

## Maintenance

### Stop the application
```bash
docker-compose down
```

### Update the application
```bash
docker-compose down
docker-compose up -d --build
```

### Clean up old downloads
```bash
# Remove old download records
docker-compose exec backend python -c "from app.database import async_session_maker; from app.models import Download; import asyncio; asyncio.run(cleanup())"

# Or manually clean the downloads directory
rm -rf downloads/*
```

### Backup database
```bash
cp backend/downloads.db backend/downloads.db.backup
```

## Performance

- **Concurrent Downloads**: 3 by default (configurable)
- **Audio Quality**: 320kbps MP3 (configurable)
- **WebSocket Updates**: Real-time with automatic reconnection
- **Database**: SQLite with async operations
- **Resource Usage**: ~500MB RAM, minimal CPU when idle

## Security Notes

⚠️ **Important**: This application is designed for personal use only.

- No authentication/authorization implemented
- Should only be run on trusted networks
- Consider adding nginx reverse proxy with SSL for internet exposure
- Respect copyright laws and YouTube's Terms of Service

## Legal Disclaimer

This tool is for personal use only to download content you have the right to access. Users are responsible for complying with YouTube's Terms of Service and applicable copyright laws. Only download content you own or have permission to download.

## License

MIT License - See LICENSE file for details

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to contribute to this project.

## Support

- 🐛 **Bug Reports**: [Open an issue](https://github.com/AJZander/youtube-music-downloader/issues)
- 💡 **Feature Requests**: [Open an issue](https://github.com/AJZander/youtube-music-downloader/issues)
- 📖 **Documentation**: Check this README and [QUICKSTART.md](QUICKSTART.md)
- 🔧 **API Docs**: http://localhost:8000/docs (when running)
- 💬 **Questions**: [Open a discussion](https://github.com/AJZander/youtube-music-downloader/discussions)

## Credits

Built with:
- [FastAPI](https://fastapi.tiangolo.com/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [React](https://react.dev/)
- [Material-UI](https://mui.com/)
- [Docker](https://www.docker.com/)
