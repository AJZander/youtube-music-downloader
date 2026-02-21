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
from app.cookies_manager import cookies_manager

logger = logging.getLogger(__name__)

# Set SSL certificate file for Python
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

# Disable SSL warnings (necessary when using nocheckcertificate)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def clean_artist_name(artist: str) -> str:
    """
    Extract the main artist name by removing featuring artists.
    
    Examples:
        "Artist feat. Other" -> "Artist"
        "Artist ft. Other" -> "Artist"
        "Artist featuring Other" -> "Artist"
        "Artist & Other feat. Third" -> "Artist & Other"
    
    Args:
        artist: The full artist string
        
    Returns:
        The main artist name without featuring artists
    """
    if not artist:
        return "Unknown"
    
    # Remove featuring artists and everything after
    # Match variations: feat., feat, ft., ft, featuring
    patterns = [
        r'\s+feat\.\s+.*$',
        r'\s+feat\s+.*$', 
        r'\s+ft\.\s+.*$',
        r'\s+ft\s+.*$',
        r'\s+featuring\s+.*$',
        r'\s+\(feat\.\s+.*\).*$',
        r'\s+\(ft\.\s+.*\).*$',
    ]
    
    cleaned = artist.strip()
    for pattern in patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    return cleaned.strip() or "Unknown"


def clean_info_dict_artists(info: Dict) -> Dict:
    """
    Recursively clean artist names in an info dictionary.
    
    This handles both single videos and playlists with entries.
    Modifies the dictionary in-place and returns it.
    """
    if not info:
        return info
    
    # Clean artist field - handle comma-separated artists by taking first one
    if 'artist' in info and info['artist']:
        original_artist = info['artist']
        # First, split by comma and take the first artist (main artist)
        main_artist = original_artist.split(',')[0].strip()
        # Then clean any feat/ft from that main artist
        info['artist'] = clean_artist_name(main_artist)
        logger.debug(f'Cleaned artist: "{original_artist}" -> "{info["artist"]}"')
    
    # For playlists, clean each entry
    if 'entries' in info and info['entries']:
        for entry in info['entries']:
            if entry:
                clean_info_dict_artists(entry)
    
    return info


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
    
    def _get_ydl_opts(
        self, 
        progress_callback: Optional[Callable] = None,
        save_thumbnail: bool = True,
        attempt: int = 1
    ) -> Dict:
        """Get yt-dlp options.
        
        Args:
            progress_callback: Optional callback for progress updates
            save_thumbnail: Whether to save and embed thumbnails (disable for albums to save space)
            attempt: Attempt number for progressive fallback (1, 2, or 3)
        """
        
        def progress_hook(d):
            """Hook for download progress with playlist support."""
            if progress_callback and d['status'] in ['downloading', 'finished']:
                # Get info_dict which contains playlist information
                info_dict = d.get('info_dict', {})
                playlist_index = info_dict.get('playlist_index')
                playlist_count = info_dict.get('playlist_count') or info_dict.get('n_entries')
                
                # Calculate progress
                if d['status'] == 'downloading':
                    total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    downloaded = d.get('downloaded_bytes', 0)
                    
                    if total > 0:
                        # Calculate current file progress
                        current_file_progress = downloaded / total
                        
                        # If this is part of a playlist, calculate overall progress
                        if playlist_index and playlist_count and playlist_count > 1:
                            # Overall progress = (completed files + current file progress) / total files
                            completed_files = playlist_index - 1  # playlist_index is 1-based
                            overall_progress = ((completed_files + current_file_progress) / playlist_count) * 100
                            progress_callback(overall_progress, f'{playlist_index}/{playlist_count}')
                        else:
                            # Single file download
                            progress = current_file_progress * 100
                            progress_callback(progress, d.get('_percent_str', '0%'))
                            
                elif d['status'] == 'finished':
                    # If this is the last file in a playlist, report 100%
                    if playlist_index and playlist_count:
                        if playlist_index >= playlist_count:
                            progress_callback(100, 'Complete')
                        else:
                            # Not the last file, calculate progress
                            overall_progress = (playlist_index / playlist_count) * 100
                            progress_callback(overall_progress, f'{playlist_index}/{playlist_count}')
                    else:
                        progress_callback(100, '100%')
        
        # Format selection with progressive fallback based on attempt
        if attempt == 1:
            # First attempt: Best quality audio formats - be more flexible
            format_str = 'bestaudio/best'
        elif attempt == 2:
            # Second attempt: Any audio, more permissive
            format_str = 'bestaudio*'
        else:
            # Third attempt: Absolutely anything that works
            format_str = 'best'
        
        logger.info(f"Using format selector (attempt {attempt}): {format_str}")
        
        opts = {
            # Format selection: try best audio, fallback to best available
            'format': format_str,
            'postprocessors': [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': settings.audio_format,
                    'preferredquality': settings.audio_quality,
                    'nopostoverwrites': False,
                },
                {
                    'key': 'FFmpegMetadata',
                    'add_metadata': True,
                },
            ],
            # Use field replacements to extract main artist only
            'outtmpl': str(self.download_dir / '%(artist)s/%(album)s/%(title)s.%(ext)s'),
            'writethumbnail': save_thumbnail,
            'embedthumbnail': save_thumbnail,
            'addmetadata': True,
            'quiet': not settings.debug,
            'no_warnings': not settings.debug,
            'extract_flat': False,
            'ignoreerrors': True,  # Skip unavailable videos in playlists
            'nocheckcertificate': True,
            'geo_bypass': True,
            'geo_bypass_country': 'US',
            'age_limit': None,
            'source_address': '0.0.0.0',
            'prefer_insecure': False,
            'logger': logger,  # Add logger to see yt-dlp output
            'verbose': True,  # Enable verbose mode for debugging
            'extractor_args': {
                'youtube': {
                    'player_client': ['android_music', 'android', 'ios', 'web'],
                    'player_skip': ['webpage', 'configs'],
                    'skip': ['hls', 'dash'],
                }
            },
            'http_chunk_size': 10485760,  # 10MB chunks
            'retries': 10,
            'fragment_retries': 10,
            'skip_unavailable_fragments': True,
            'keepvideo': False,
            'prefer_ffmpeg': True,
            'postprocessor_args': [
                '-ar', '48000',  # Set sample rate
            ],
        }
        
        # Add cookies if available for age-restricted content
        cookies_path = cookies_manager.get_cookies_path()
        if cookies_path:
            opts['cookiefile'] = str(cookies_path)
            logger.info(f"Using YouTube cookies from: {cookies_path}")
        
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
                'ignoreerrors': True,  # Skip problematic videos during info extraction
                'nocheckcertificate': True,
                'age_limit': None,
                'geo_bypass': True,
                'geo_bypass_country': 'US',
                'source_address': '0.0.0.0',
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android_music', 'android', 'ios', 'web'],
                        'player_skip': ['webpage', 'configs'],
                    }
                },
            }
            
            # Add cookies if available
            cookies_path = cookies_manager.get_cookies_path()
            if cookies_path:
                ydl_opts['cookiefile'] = cookies_path
            
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
        status_callback: Optional[Callable] = None,
        max_attempts: int = 3,
    ) -> Dict:
        """Download audio from YouTube Music URL with automatic retry and fallback.
        
        Args:
            url: YouTube Music URL to download
            progress_callback: Optional callback for progress updates
            status_callback: Optional callback for status updates
            max_attempts: Maximum number of attempts with different format options
        """
        last_error = None
        
        for attempt in range(1, max_attempts + 1):
            try:
                if attempt > 1:
                    logger.warning(f"Retrying download (attempt {attempt}/{max_attempts}) with fallback options")
                
                return await self._download_with_options(
                    url, progress_callback, status_callback, attempt
                )
                
            except Exception as e:
                last_error = e
                logger.error(f"Download attempt {attempt} failed: {e}")
                if attempt < max_attempts:
                    continue
                else:
                    # All attempts failed
                    logger.error(f"All {max_attempts} download attempts failed for {url}")
                    if status_callback:
                        await status_callback(DownloadStatus.FAILED, 0)
                    raise last_error
    
    async def _download_with_options(
        self, 
        url: str, 
        progress_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        attempt: int = 1,
    ) -> Dict:
        """Internal download method with specific attempt options.
        
        Args:
            url: YouTube Music URL to download
            progress_callback: Optional callback for progress updates
            status_callback: Optional callback for status updates
            attempt: Attempt number for progressive fallback
        """
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
            
            # Simplified: Always save thumbnails for all downloads
            ydl_opts = self._get_ydl_opts(_progress_wrapper, save_thumbnail=True, attempt=attempt)
            
            def _download():
                # First pass: get info to determine if playlist and clean artist names
                logger.info(f"Extracting info for {url}")
                info_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': 'in_playlist',
                    'ignoreerrors': True,
                    'nocheckcertificate': True,
                    'geo_bypass': True,
                    'geo_bypass_country': 'US',
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['android_music', 'android', 'ios', 'web'],
                            'player_skip': ['webpage', 'configs'],
                        }
                    },
                }
                
                # Add cookies if available
                cookies_path = cookies_manager.get_cookies_path()
                if cookies_path:
                    info_opts['cookiefile'] = str(cookies_path)
                
                with yt_dlp.YoutubeDL(info_opts) as ydl_info:
                    basic_info = ydl_info.extract_info(url, download=False)
                
                # Clean artist names in basic info for reference
                clean_info_dict_artists(basic_info)
                
                # Custom YoutubeDL subclass to clean artist names before filename generation
                class CustomYDL(yt_dlp.YoutubeDL):
                    def process_ie_result(self, ie_result, download=True, extra_info=None):
                        # Clean artist names before processing
                        if 'artist' in ie_result and ie_result['artist']:
                            original = ie_result['artist']
                            # Split by comma and take first artist
                            main_artist = original.split(',')[0].strip()
                            # Remove featuring markers
                            for marker in [' feat.', ' feat ', ' ft.', ' ft ', ' featuring ']:
                                if marker in main_artist.lower():
                                    main_artist = main_artist[:main_artist.lower().index(marker)].strip()
                                    break
                            ie_result['artist'] = main_artist
                        
                        # Process entries in playlists
                        if 'entries' in ie_result:
                            for entry in ie_result.get('entries', []):
                                if entry and 'artist' in entry and entry['artist']:
                                    original = entry['artist']
                                    main_artist = original.split(',')[0].strip()
                                    for marker in [' feat.', ' feat ', ' ft.', ' ft ', ' featuring ']:
                                        if marker in main_artist.lower():
                                            main_artist = main_artist[:main_artist.lower().index(marker)].strip()
                                            break
                                    entry['artist'] = main_artist
                        
                        return super().process_ie_result(ie_result, download, extra_info)
                
                # Second pass: actual download with clean settings
                logger.info("Starting download")
                with CustomYDL(ydl_opts) as ydl:
                    try:
                        # Direct download - let yt-dlp handle everything
                        result = ydl.extract_info(url, download=True)
                        if not result:
                            raise Exception("No data returned from yt-dlp")
                        return result
                    except Exception as e:
                        # Log the error and raise it
                        logger.error(f"yt-dlp download error: {e}")
                        raise
            
            info = await loop.run_in_executor(None, _download)
            
            if status_callback:
                await status_callback(DownloadStatus.PROCESSING, 95)
            
            # Get downloaded files info
            # Count successful vs total entries for playlists
            total_entries = len(info.get('entries', [])) if info.get('_type') == 'playlist' else 1
            # Filter out None entries (failed downloads with ignoreerrors=True)
            successful_entries = [e for e in info.get('entries', [info]) if e is not None] if info.get('_type') == 'playlist' else [info]
            successful_count = len(successful_entries)
            
            result = {
                'title': info.get('title', 'Unknown'),
                'artist': info.get('artist') or info.get('uploader', 'Unknown Artist'),
                'album': info.get('album', 'Unknown Album'),
                'file_count': successful_count,
            }
            
            # Log if some tracks were skipped
            if total_entries > successful_count:
                skipped = total_entries - successful_count
                logger.warning(f"Downloaded {successful_count}/{total_entries} tracks ({skipped} skipped due to age restrictions or errors)")
            else:
                logger.info(f"Successfully downloaded {successful_count} track(s)")
            
            # Check if nothing was downloaded
            if successful_count == 0:
                error_msg = "No tracks were downloaded. This could be due to format availability, geo-restrictions, or authentication issues."
                logger.error(error_msg)
                if status_callback:
                    await status_callback(DownloadStatus.FAILED, 0)
                raise Exception(error_msg)
            
            if status_callback:
                await status_callback(DownloadStatus.COMPLETED, 100)
            
            return result
            
        except Exception as e:
            logger.error(f"Download failed for {url}: {e}")
            if status_callback:
                await status_callback(DownloadStatus.FAILED, 0)
            raise


download_manager = DownloadManager()
