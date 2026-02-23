# backend/app/config.py
from pathlib import Path
from typing import Union
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Application ──────────────────────────────────────────────────────────
    app_name: str = "YouTube Music Downloader"
    debug: bool = False

    # ── Paths ─────────────────────────────────────────────────────────────────
    # DOWNLOAD_DIR must be an absolute path on the host (mounted volume)
    download_dir: Path = Path("/downloads")
    database_url: str = "sqlite+aiosqlite:////app/data/downloads.db"
    # Cookies live in a dedicated sub-directory of the data volume
    cookies_dir: Path = Path("/app/data/cookies")

    # ── Download ──────────────────────────────────────────────────────────────
    max_concurrent_downloads: int = 3
    # mp3 | flac | m4a | opus | wav
    audio_format: str = "mp3"
    # DEPRECATED: audio_quality is no longer used. Audio is always downloaded at
    # the highest quality possible (VBR best quality for lossy formats).
    audio_quality: str = "0"

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: Union[str, list[str]] = "*"

    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def cors_list(self) -> list[str]:
        """Always return a proper list regardless of env var format."""
        if isinstance(self.cors_origins, str):
            return ["*"] if self.cors_origins.strip() == "*" else [
                o.strip() for o in self.cors_origins.split(",") if o.strip()
            ]
        return self.cors_origins


# Single shared instance imported everywhere
settings = Settings()