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

    # Library management
    # Scan concurrency. Tag reads are worker *processes*, not threads: mutagen
    # parsing is pure Python and threads plateau at one core's worth of GIL.
    # Past that the ceiling is the NAS, not us — cold scattered reads off this
    # CIFS mount land at roughly 13 file-opens/sec in total however wide the
    # pool goes, so raising this much above 32 buys latency, not throughput.
    library_scan_workers: int = 32
    trash_retention_days: int = 30

    # Artist catalogue browsing — YouTube Music metadata, not downloads, so
    # it has its own budget rather than spending the download caps. It still
    # shares the governor's backoff: YouTube throttles the address.
    catalog_requests_per_hour: int = 600
    # Spacing for background tracklist prefetch. Someone waiting on an answer
    # (a search, an album they just expanded) jumps ahead with a shorter gap.
    catalog_prefetch_delay_min: float = 2.0
    catalog_prefetch_delay_max: float = 4.0

    # Discover: background scan of the library's artists for new releases.
    # One YouTube Music request per interval — 12s is ~300 an hour, half the
    # browsing budget, so the artist browser always has room.
    discover_enabled: bool = True
    discover_request_interval: float = 12.0
    # How many recommended artists ("fans also like", counted across the
    # library) are scanned for new releases as well.
    discover_suggested_artists: int = 40
    # Default chart country (YouTube Music codes: AU, US, GB… or ZZ = global).
    discover_country: str = "ZZ"

    # Metadata manager: keeps file tags and Plex in step with MusicBrainz.
    # Report-only until auto-apply is switched on in the UI; these switches
    # are a hard ceiling on what auto-apply may touch.
    metadata_enabled: bool = True
    metadata_interval_min: int = 30
    metadata_write_files: bool = True
    metadata_move_files: bool = True
    metadata_write_plex: bool = True
    # A release is applied automatically at or above this match score, offered
    # for review between the two, and reported as unidentified below.
    metadata_auto_confidence: float = 0.85
    metadata_review_confidence: float = 0.55
    metadata_max_genres: int = 5
    metadata_max_moods: int = 5
    # Albums already checked are looked at again this often (sources improve).
    metadata_recheck_days: int = 90
    # MusicBrainz requires contact details in the User-Agent; lookups stay
    # off until this is set (an email address or a URL).
    musicbrainz_contact: str = ""
    musicbrainz_url: str = "https://musicbrainz.org/ws/2"
    musicbrainz_interval: float = 1.0     # seconds between request starts (limit: 1/s)
    lastfm_api_key: str = ""
    # Plex is local; this only keeps a full sync from hogging the server.
    plex_requests_per_sec: float = 20.0

    # Matching
    duration_tolerance_sec: float = 3.0

    # Validation — the absolute length floor applies ONLY when the source gave
    # us no expected duration. Anything the source vouches for (intros,
    # interludes, skits) is checked against that duration, not against a floor.
    min_duration_sec: float = 5.0

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
    def trash_dir(self) -> Path:
        # Inside the library so removal is a rename, not a copy; dot-prefixed
        # so Plex (and our own scanner) walk straight past it.
        return self.library_dir / ".mdl-trash"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


settings = Settings()
