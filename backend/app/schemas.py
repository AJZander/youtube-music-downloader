# backend/app/schemas.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

from app.utils import is_valid_youtube_url


class DownloadCreate(BaseModel):
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
    created_at:    Optional[datetime]
    updated_at:    Optional[datetime]

    class Config:
        from_attributes = True


class CookieUpload(BaseModel):
    cookies_content: str

    @field_validator("cookies_content")
    @classmethod
    def validate_netscape_format(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Cookie content must not be empty")
        # Netscape cookie files must start with this magic comment
        if not v.startswith("# Netscape HTTP Cookie File") and not v.startswith("# HTTP Cookie File"):
            raise ValueError(
                "Cookies must be in Netscape format. "
                "The file should start with '# Netscape HTTP Cookie File'."
            )
        return v


class ErrorDetail(BaseModel):
    detail: str