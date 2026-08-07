from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Paths (container-side)
    data_dir: Path = Path("/app/data")
    staging_dir: Path = Path("/staging")
    library_dir: Path = Path("/library")

    # Plex
    plex_url: str = "http://host.docker.internal:32400"
    plex_token: str = ""
    plex_section_id: int = 5
    # Plex's own path for library_dir (host CIFS mount seen by the Plex container)
    plex_library_root: str = "/data/Music"

    # Spotify API
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = ""

    # Rate governor
    tracks_per_hour: int = 60
    tracks_per_day: int = 300
    inter_track_delay_min: float = 5.0
    inter_track_delay_max: float = 20.0
    long_pause_every: int = 25
    long_pause_min: float = 120.0
    long_pause_max: float = 300.0

    # Staging guardrails
    staging_max_gb: float = 30.0
    staging_min_free_gb: float = 5.0

    # Engine
    ytdlp_min_version: str = "2026.07.04"
    download_timeout_sec: int = 900
    pot_provider_url: str = "http://mdl-pot:4416"
    ytdlp_sleep_min: float = 5.0        # pre-download sleep, uniform(min, max)
    ytdlp_sleep_max: float = 15.0
    ytdlp_sleep_requests: float = 1.0   # between innertube API requests
    ratelimit_bytes: int = 3_000_000    # transfer cap (bytes/sec)

    # Matching
    duration_tolerance_sec: float = 3.0

    log_level: str = "INFO"

    model_config = {"env_file": ".env", "extra": "ignore"}

    # Derived paths
    @property
    def db_path(self) -> Path:
        return self.data_dir / "mdl.db"

    @property
    def cookies_master(self) -> Path:
        return self.data_dir / "cookies" / "youtube-cookies.txt"

    @property
    def cookies_working(self) -> Path:
        return self.data_dir / "cookies" / ".working-copy.txt"

    @property
    def staging_jobs_dir(self) -> Path:
        return self.staging_dir / "jobs"

    @property
    def quarantine_dir(self) -> Path:
        return self.staging_dir / "quarantine"

    @property
    def library_sentinel(self) -> Path:
        return self.library_dir / ".mdl-library-online"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


settings = Settings()
