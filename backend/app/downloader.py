import asyncio
import yt_dlp
import certifi
import ssl
import urllib3
from pathlib import Path
from typing import Dict, Callable, Optional
from datetime import datetime
import logging
import re
import os

from app.config import settings
from app.models import DownloadStatus

logger = logging.getLogger(__name__)

# Set SSL certificate file for Python
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

# Disable SSL warnings (necessary when using nocheckcertificate)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class DownloadManager:
    """Manages YouTube Music downloads using yt-dlp."""
    
    def __init__(self):
        self.download_dir = settings.download_dir
        self.download_dir.mkdir(parents=True, exist_ok=True)
        
    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to remove invalid characters."""
        # Remove or replace invalid characters
        filename = re.sub(r'[<>:"/\\|?*]', '', filename)
        filename = re.sub(r'\s+', ' ', filename)
        return filename.strip()
    
    def _get_ydl_opts(self, progress_callback: Optional[Callable] = None) -> Dict:
        """Get yt-dlp options."""
        
        def progress_hook(d):
            """Hook for download progress."""
            if progress_callback and d['status'] in ['downloading', 'finished']:
                if d['status'] == 'downloading':
                    total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    downloaded = d.get('downloaded_bytes', 0)
                    
                    if total > 0:
                        progress = (downloaded / total) * 100
                        progress_callback(progress, d.get('_percent_str', '0%'))
                elif d['status'] == 'finished':
                    progress_callback(100, '100%')
        
        opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': settings.audio_format,
                'preferredquality': settings.audio_quality,
            }],
            'outtmpl': str(self.download_dir / '%(artist)s/%(album)s/%(title)s.%(ext)s'),
            'writethumbnail': True,
            'embedthumbnail': True,
            'addmetadata': True,
            'quiet': not settings.debug,
            'no_warnings': not settings.debug,
            'extract_flat': False,
            'ignoreerrors': False,
            'nocheckcertificate': True,
            'geo_bypass': True,
            'age_limit': None,
            'source_address': '0.0.0.0',
            'prefer_insecure': False,
        }
        
        if progress_callback:
            opts['progress_hooks'] = [progress_hook]
            
        return opts
    
    async def get_info(self, url: str) -> Dict:
        """Extract information about the URL without downloading."""
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': 'in_playlist',
                'nocheckcertificate': True,
                'age_limit': None,
                'geo_bypass': True,
                'source_address': '0.0.0.0',
            }
            
            loop = asyncio.get_event_loop()
            
            def _extract_info():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)
            
            info = await loop.run_in_executor(None, _extract_info)
            
            # Determine type
            download_type = 'song'
            if info.get('_type') == 'playlist':
                download_type = 'playlist'
                # Check if it's an album or artist page
                if 'album' in url.lower() or info.get('album'):
                    download_type = 'album'
                elif 'artist' in url.lower() or 'channel' in url.lower():
                    download_type = 'artist'
            
            return {
                'title': info.get('title', 'Unknown'),
                'artist': info.get('artist') or info.get('uploader', 'Unknown Artist'),
                'album': info.get('album', 'Unknown Album'),
                'type': download_type,
                'entry_count': len(info.get('entries', [])) if download_type != 'song' else 1,
            }
            
        except Exception as e:
            logger.error(f"Failed to extract info from {url}: {e}")
            raise
    
    async def download(
        self, 
        url: str, 
        progress_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None
    ) -> Dict:
        """Download audio from YouTube Music URL."""
        try:
            if status_callback:
                await status_callback(DownloadStatus.DOWNLOADING, 0)
            
            loop = asyncio.get_event_loop()
            
            def _progress_wrapper(progress: float, percent_str: str):
                """Wrapper for progress callback to handle async."""
                if progress_callback:
                    asyncio.run_coroutine_threadsafe(
                        progress_callback(progress), 
                        loop
                    )
            
            ydl_opts = self._get_ydl_opts(_progress_wrapper)
            
            def _download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    return info
            
            info = await loop.run_in_executor(None, _download)
            
            if status_callback:
                await status_callback(DownloadStatus.PROCESSING, 95)
            
            # Get downloaded files info
            result = {
                'title': info.get('title', 'Unknown'),
                'artist': info.get('artist') or info.get('uploader', 'Unknown Artist'),
                'album': info.get('album', 'Unknown Album'),
                'file_count': len(info.get('entries', [])) if info.get('_type') == 'playlist' else 1,
            }
            
            if status_callback:
                await status_callback(DownloadStatus.COMPLETED, 100)
            
            return result
            
        except Exception as e:
            logger.error(f"Download failed for {url}: {e}")
            if status_callback:
                await status_callback(DownloadStatus.FAILED, 0)
            raise


download_manager = DownloadManager()
