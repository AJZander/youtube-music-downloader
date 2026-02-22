# Quick Start

## 1 · Clone & configure
```bash
git clone https://github.com/AJZander/youtube-music-downloader.git
cd youtube-music-downloader
cp backend/.env.example backend/.env
```

## 2 · (Optional) Set custom API URLs for your domain
Create a `.env` file in the project root:
```bash
REACT_APP_API_URL=https://api.your-domain.com
REACT_APP_WS_URL=wss://api.your-domain.com/ws
```

## 3 · Build & run
```bash
docker compose up -d --build
```

- **Frontend** → http://localhost:8080  
- **Backend API** → http://localhost:8000  
- **Swagger docs** → http://localhost:8000/docs  

## 4 · (Optional) YouTube authentication
For age-restricted content:
1. Click the 🔑 key icon in the app header
2. Install *Get cookies.txt LOCALLY* (Chrome) or *cookies.txt* (Firefox)
3. Sign in to YouTube, export cookies, paste them in the dialog

## File layout after downloads
```
./downloads/
  ArtistName/
    AlbumName/
      01 - Track Title.mp3
      02 - Track Title.mp3
```

## Update yt-dlp (YouTube changes formats often)
```bash
docker compose exec backend pip install -U yt-dlp
# or rebuild fully
docker compose up -d --build
```

## Environment variables (backend/.env)
| Variable | Default | Description |
|---|---|---|
| `DOWNLOAD_DIR` | `/downloads` | Where files are saved |
| `MAX_CONCURRENT_DOWNLOADS` | `3` | Parallel downloads |
| `AUDIO_FORMAT` | `mp3` | `mp3 \| flac \| m4a \| opus` |
| `AUDIO_QUALITY` | `320` | kbps (128/192/256/320) |
| `CORS_ORIGINS` | `*` | Restrict in production |
| `DEBUG` | `false` | Verbose logging |