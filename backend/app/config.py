from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Union


class Settings(BaseSettings):
    """Application settings and configuration."""
    
    # Application
    app_name: str = "YouTube Music Downloader"
    debug: bool = False
    
    # Paths
    download_dir: Path = Path("/downloads")
    database_url: str = "sqlite+aiosqlite:///./data/downloads.db"
    
    # Download settings
    max_concurrent_downloads: int = 3
    audio_format: str = "mp3"
    audio_quality: str = "320"  # kbps
    
    # CORS - allow all origins for local development
    cors_origins: Union[str, list[str]] = "*"
    
    class Config:
        env_file = ".env"
        case_sensitive = False
    
    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins - handle both string and list."""
        if isinstance(self.cors_origins, str):
            if self.cors_origins == "*":
                return ["*"]
            return [origin.strip() for origin in self.cors_origins.split(",")]
        return self.cors_origins


settings = Settings()
