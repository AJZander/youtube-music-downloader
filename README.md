# YouTube Music Downloader - Interactive Format Selection

High-quality audio downloader for YouTube Music with **interactive format selection** and a modern web interface.

> **Legal Notice**: This software is for downloading content you own or have permission to download. Respect copyright laws and YouTube's Terms of Service. The authors are not responsible for misuse.

## ✨ Features

- 🎵 **Interactive Format Selection** - See all available audio formats and choose the best one
- 📀 **Batch downloads** - Songs, albums, playlists, artist channels  
- 🎨 **Clean metadata** - Auto-tagged with artist, album, artwork
- 🔒 **Age-restricted content** - Cookie authentication support
- ⚡ **Real-time progress** - WebSocket updates
- 🎯 **Smart rate limiting** - Avoids YouTube throttling
- 📊 **Download history** - Track all your downloads
- 🔍 **Transparency** - See exactly what formats are available before downloading

## 🆕 What's New in v2.1

### Interactive Format Selection

Instead of guessing which format will work, you now **see all available options**:

1. **Paste a URL** → System analyzes available formats
2. **Choose your format** → Pick from audio-only options with quality info
3. **Download starts** → System uses your selected format

This solves authentication issues where different formats are available with/without cookies!

---

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose
- 2GB free disk space minimum

### Installation

```bash
# 1. Clone repository
git clone https://github.com/AJZander/youtube-music-downloader.git
cd youtube-music-downloader

# 2. Quick start script
chmod +x quick-start.sh
./quick-start.sh

# Or manual setup:
cp backend/.env.example backend/.env
docker compose up -d --build

# 3. Access the app
# Frontend: http://localhost:3000
# Backend API: http://localhost:8000
# API Docs: http://localhost:8000/docs
```

---

## 📖 How to Use

### Basic Download

1. Open http://localhost:3000
2. Paste a YouTube Music URL
3. **Select your preferred format** from the dialog
4. Click "Download Selected Format"
5. Watch real-time progress

### Understanding Format Options

The format selector shows:

- **🎵 Codec type** (opus, m4a, webm)
- **Bitrate** (~130kbps, ~160kbps, ~250kbps)
- **Estimated file size**
- **Recommended option** (⭐ badge)

**Recommended formats** are automatically selected but you can choose any option.

### Format Selection Tips

- **Auto-select Best** - Let yt-dlp automatically pick (safest choice)
- **Highest bitrate** - Best quality, larger files
- **Opus codec** - Best quality-to-size ratio for most music
- **M4A/AAC** - Good compatibility with Apple devices

---

## 🔐 Authentication (Age-Restricted Content)

For age-restricted or premium content:

1. **Install browser extension**:
   - Chrome/Edge: [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   - Firefox: [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)

2. **Sign in to YouTube** in your browser

3. **Export cookies**:
   - Use the extension to export from `youtube.com`
   - Format: Netscape (should start with `# Netscape HTTP Cookie File`)

4. **Upload in app**:
   - Click the 🔑 key icon in the app header
   - Paste the exported cookie text
   - Click "Save Cookies"

5. **Match User-Agent** (CRITICAL):
   - Find your browser's User-Agent:
     - Chrome: `chrome://version`
     - Firefox: `about:support`
     - Edge: `edge://version`
   - Update `USER_AGENT` in `backend/.env` to match EXACTLY
   - Restart: `docker compose restart backend`

With authentication, the format selector will show formats available to premium/authenticated users!

---

## 📁 File Organization

Downloads are organized automatically:

```
./downloads/
├── Artist Name/
│   ├── Album Name/
│   │   ├── 01 - Song Title.opus
│   │   ├── 02 - Song Title.opus
│   │   └── ...
│   └── Another Album/
│       └── ...
└── Another Artist/
    └── ...
```

---

## ⚙️ Configuration

### Audio Post-Processing

After downloading, you can convert to your preferred format:

Edit `backend/.env`:

```bash
# Keep original format (RECOMMENDED - what you selected)
AUDIO_FORMAT=best

# Or convert after download:
# AUDIO_FORMAT=mp3    # Universal compatibility
# AUDIO_FORMAT=flac   # Lossless (large files)
# AUDIO_FORMAT=m4a    # Good quality, smaller files

# MP3 quality (only when AUDIO_FORMAT=mp3):
MP3_QUALITY=0  # VBR highest (0-9, lower=better)
# MP3_QUALITY=320  # Or constant 320kbps
```

### Rate Limiting

```bash
# Prevent YouTube throttling
DOWNLOAD_INTERVAL_SECONDS=5  # Wait between downloads
MAX_CONCURRENT_DOWNLOADS=1   # Parallel downloads (1 recommended)

# Retry behavior
RATE_LIMIT_BACKOFF_SECONDS=60  # Wait time when rate limited
MAX_RATE_LIMIT_RETRIES=5       # Max retry attempts
```

---

## 🔄 Updating

### Update yt-dlp (Important!)

YouTube changes formats frequently. Update regularly:

```bash
# Quick update
docker compose exec backend pip install -U yt-dlp
docker compose restart backend

# Full rebuild
docker compose down
docker compose up -d --build
```

### Update Application

```bash
git pull
docker compose down
docker compose up -d --build

# Migrate database (if needed)
docker compose exec backend python migrate_db.py
```

---

## 🐛 Troubleshooting

### "Format not available" errors FIXED! ✅

The new interactive format selection **eliminates** this error by:
- Showing you exactly what's available
- Letting you choose a known-working format
- No more guessing games

### Still having issues?

1. **Try different format** - Use the format selector to pick another option
2. **Update yt-dlp** - `docker compose exec backend pip install -U yt-dlp`
3. **Check authentication** - Some formats require cookies
4. **Enable debug logs**:
   ```bash
   # In backend/.env
   DEBUG=true
   ```
   ```bash
   docker compose restart backend
   docker compose logs -f backend
   ```

### Age-Restricted Content

**Requires cookies AND matching User-Agent!**
- See Authentication section above
- Format selector will show different options with authentication

**See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for complete guide**

---

## 📊 Monitoring

```bash
# View logs
docker compose logs -f backend

# Check container status
docker compose ps

# API health check
curl http://localhost:8000/health
```

---

## 🛠️ Development

```bash
# Backend (Python/FastAPI)
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python migrate_db.py  # Add format_id column
uvicorn app.main:app --reload

# Frontend (React)
cd frontend
npm install
npm start
```

---

## 🎯 How It Works

### Format Selection Flow

```
User pastes URL
    ↓
System calls /formats endpoint
    ↓
yt-dlp extracts available formats
    ↓
Backend analyzes and categorizes formats
    ↓
User sees dialog with format options
    ↓
User selects preferred format
    ↓
Download starts with selected format
    ↓
Optional: Convert to different format (AUDIO_FORMAT setting)
```

### Why This Approach?

- **Transparency**: You see exactly what's available
- **Reliability**: No format guessing = fewer errors
- **Flexibility**: Choose quality vs file size tradeoff
- **Authentication-aware**: Different options with/without cookies

---

## 📚 Tech Stack

**Backend:**
- FastAPI - Modern Python web framework
- yt-dlp - YouTube extraction & download
- SQLAlchemy - Database ORM
- WebSockets - Real-time updates

**Frontend:**
- React 18 - UI framework
- Material-UI (MUI) - Component library
- Axios - HTTP client
- WebSocket - Live updates

**Infrastructure:**
- Docker & Docker Compose
- Nginx - Frontend serving
- SQLite - Database

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md)

---

## 📝 License

MIT License - see [LICENSE](LICENSE)

**Legal Disclaimer:** This software is for personal/educational use only. Users must comply with YouTube's Terms of Service and copyright laws. Only download content you own or have permission to download.

---

## 🔗 Useful Links

- [yt-dlp Documentation](https://github.com/yt-dlp/yt-dlp)
- [FFmpeg Documentation](https://ffmpeg.org/documentation.html)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Issue Tracker](https://github.com/AJZander/youtube-music-downloader/issues)