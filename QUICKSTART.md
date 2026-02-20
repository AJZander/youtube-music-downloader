# YouTube Music Downloader - Quick Start Guide

## 🚀 Quick Start (3 steps)

1. **Clone and navigate to the project:**
   ```bash
   git clone https://github.com/AJZander/youtube-music-downloader.git
   cd youtube-music-downloader
   ```

2. **Start the application:**
   ```bash
   ./start.sh
   ```
   Or manually:
   ```bash
   docker-compose up -d --build
   ```

3. **Open your browser:**
   - Go to: http://localhost:80
   - Paste a YouTube Music URL
   - Click "Download"
   - Watch the progress in real-time!

## 📥 Downloaded Files

Files are saved to: `./downloads/`

Organized as: `Artist/Album/Song.mp3`

## 🛠️ Useful Commands

```bash
# View logs
docker-compose logs -f

# Stop application
docker-compose down
# or
./stop.sh

# Restart
docker-compose restart

# Check status
docker-compose ps

# Access backend API docs
# Open: http://localhost:8000/docs
```

## 🎯 What URLs Work?

✅ Individual songs: `https://music.youtube.com/watch?v=...`
✅ Albums: `https://music.youtube.com/playlist?list=OLAK5uy_...`
✅ Artists: `https://music.youtube.com/channel/...`
✅ Playlists: `https://music.youtube.com/playlist?list=...`

## ⚙️ Configuration

Edit `.env` files to customize:
- `backend/.env` - Download settings, quality, concurrent downloads
- `frontend/.env` - API URLs

## 🐛 Troubleshooting

**Services won't start?**
```bash
docker-compose logs
```

**Can't access the web interface?**
```bash
docker-compose ps  # Check if running
docker-compose restart
```

**Downloads failing?**
- Check the URL is valid
- View backend logs: `docker-compose logs backend`

## 📊 Features

- ✅ Real-time progress tracking
- ✅ Queue multiple downloads
- ✅ Auto metadata tagging
- ✅ Album artwork embedding
- ✅ High quality audio (320kbps MP3)
- ✅ WebSocket live updates

## 🔒 Important Notes

- This is for **personal use only**
- Only download content you own or have rights to
- Runs on your local machine only
- No internet exposure by default

---

For full documentation, see [README.md](README.md)
