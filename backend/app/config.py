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
    
    # Quality for MP3 (only used when audio_format=mp3):
    # VBR: 0 (best) to 10 (worst)
    # CBR: 128, 192, 256, 320 (320 = highest quality)
    mp3_quality: str = "0"
    
    # Rate limiting settings
    download_interval_seconds: int = 5
    rate_limit_backoff_seconds: int = 60
    max_rate_limit_retries: int = 5
    
    # User-Agent - CRITICAL: Must match browser cookies came from!
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    cors_origins: Union[str, list[str]] = "*"

    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def cors_list(self) -> list[str]:
        """Parse CORS origins into a list."""
        if isinstance(self.cors_origins, str):
            if self.cors_origins.strip() == "*":
                return ["*"]
            return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return self.cors_origins


settings = Settings()