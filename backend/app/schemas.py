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