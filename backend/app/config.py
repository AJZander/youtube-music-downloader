# backend/app/config.py
from pathlib import Path
from typing import Union
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "YouTube Music Downloader"
    debug: bool = False

    download_dir: Path = Path("/downloads")
    database_url: str = "sqlite+aiosqlite:////app/data/downloads.db"
    cookies_dir: Path = Path("/app/data/cookies")

    max_concurrent_downloads: int = 1
    
    # Audio format: "best" preserves original, or specify: mp3, m4a, opus, flac, wav
    audio_format: str = "best"
    # Quality for lossy formats (mp3, m4a, opus): 0 (best) to 10 (worst), or specific bitrate like "320"
    mp3_quality: str = "0"  # Changed from "320" to "0" for best quality
    
    download_interval_seconds: int = 5
    rate_limit_backoff_seconds: int = 60  # Reduced from 300 to 60 seconds
    max_rate_limit_retries: int = 5  # Increased from 3 to 5
    
    # User-Agent to use with authenticated requests (must match browser cookies came from)
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

    cors_origins: Union[str, list[str]] = "*"

    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def cors_list(self) -> list[str]:
        if isinstance(self.cors_origins, str):
            return ["*"] if self.cors_origins.strip() == "*" else [
                o.strip() for o in self.cors_origins.split(",") if o.strip()
            ]
        return self.cors_origins


settings = Settings()