# backend/app/schemas.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

from app.utils import is_valid_youtube_url


class DownloadCreate(BaseModel):
    url: str
    format_id: Optional[str] = None  # If not provided, uses "bestaudio/best"

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("URL must not be empty")
        if not is_valid_youtube_url(v):
            raise ValueError("Only YouTube / YouTube Music URLs are accepted")
        return v


class DownloadResponse(BaseModel):
    id:            int
    url:           str
    title:         Optional[str]
    artist:        Optional[str]
    album:         Optional[str]
    download_type: str
    status:        str
    progress:      float
    error_message: Optional[str]
    file_path:     Optional[str]
    total_tracks:  Optional[int]
    done_tracks:   Optional[int]
    format_id:     Optional[str]
    created_at:    Optional[datetime]
    updated_at:    Optional[datetime]

    class Config:
        from_attributes = True


class PaginatedDownloadsResponse(BaseModel):
    """Paginated downloads response with total count."""
    downloads: list[DownloadResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class FormatOption(BaseModel):
    """Single format option for user selection."""
    format_id: str
    label: str
    description: str
    ext: Optional[str] = None
    codec: Optional[str] = None
    bitrate: Optional[int] = None
    filesize_mb: Optional[float] = None
    recommended: bool = False
    has_video: bool = False
    note: Optional[str] = None  # e.g., "authenticated"


class FormatMetadata(BaseModel):
    """Video/playlist metadata."""
    title: str
    artist: str
    folder_artist: str
    album: str
    download_type: str
    total_tracks: int


class FormatListResponse(BaseModel):
    """Response containing available formats and metadata."""
    formats: list[FormatOption]
    metadata: FormatMetadata
    has_video: bool
    is_playlist: bool


class ErrorDetail(BaseModel):
    detail: str


# ── Channel / playlist-import schemas ─────────────────────────────────────────

class ChannelRequest(BaseModel):
    """Request body for channel playlist discovery."""
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("URL must not be empty")
        if not is_valid_youtube_url(v):
            raise ValueError("Only YouTube / YouTube Music URLs are accepted")
        return v


class PlaylistInfo(BaseModel):
    """Metadata for a single playlist discovered on a channel."""
    id: Optional[str] = None
    title: str
    url: str
    thumbnail: Optional[str] = None
    track_count: Optional[int] = None
    channel: Optional[str] = None
    channel_url: Optional[str] = None
    source_tab: Optional[str] = None
    # Classified release type: album | ep | single | playlist | music_video
    release_type: Optional[str] = None
    # Additional metadata for better classification
    release_date: Optional[str] = None
    release_year: Optional[int] = None
    description: Optional[str] = None
    total_duration: Optional[int] = None  # Total duration in seconds
    view_count: Optional[int] = None
    like_count: Optional[int] = None


class ChannelPlaylistsResponse(BaseModel):
    """All playlists found on a channel."""
    playlists: list[PlaylistInfo]
    channel: Optional[str] = None
    total: int


class ChannelQueueRequest(BaseModel):
    """Request body to queue a set of playlists for download."""
    playlists: list[PlaylistInfo]


class ChannelQueueResponse(BaseModel):
    """Result of bulk-queuing channel playlists."""
    batch_id: str
    total: int
    message: str


class BatchStatusResponse(BaseModel):
    """Status of a background batch operation."""
    id: str
    status: str  # processing | completed
    total: int
    queued: int
    skipped: int
    failed: int
    download_ids: list[int]
    created_at: datetime
    updated_at: datetime


class StatsResponse(BaseModel):
    """Per-status counts for the dashboard."""
    queued: int = 0
    downloading: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    total: int = 0


# ── Metadata Processing Schemas ──────────────────────────────────────────────

class MetadataProcessingRequest(BaseModel):
    """Request to start background metadata processing for a channel."""
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("URL must not be empty")
        if not is_valid_youtube_url(v):
            raise ValueError("Only YouTube / YouTube Music URLs are accepted")
        return v


class MetadataProcessingResponse(BaseModel):
    """Response when starting metadata processing."""
    job_id: str
    message: str


class MetadataProcessingJob(BaseModel):
    """Metadata processing job status."""
    id: str
    channel_url: str
    channel_name: Optional[str]
    status: str
    progress: float
    total_items: Optional[int]
    processed_items: int
    error_message: Optional[str]
    metadata_results: Optional[list]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class MetadataPlaylistItem(BaseModel):
    """Individual playlist item from metadata processing."""
    id: int
    processing_job_id: str
    playlist_id: Optional[str]
    title: str
    url: str
    thumbnail: Optional[str]
    track_count: Optional[int]
    channel: Optional[str]
    channel_url: Optional[str]
    source_tab: Optional[str]
    release_type: Optional[str]
    selected_for_download: bool
    # Additional metadata fields
    release_date: Optional[str]
    release_year: Optional[int]
    description: Optional[str]
    total_duration: Optional[int]
    view_count: Optional[int]
    like_count: Optional[int]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class MetadataItemSelectionRequest(BaseModel):
    """Request to update selection status of metadata items."""
    item_ids: list[int]
    selected: bool


class MetadataQueueSelectedRequest(BaseModel):
    """Request to queue selected metadata items for download."""
    format_id: str = "bestaudio/best"