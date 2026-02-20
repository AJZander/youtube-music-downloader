from pydantic import BaseModel, HttpUrl, Field
from datetime import datetime
from typing import Optional


class DownloadRequest(BaseModel):
    """Schema for download request."""
    url: str = Field(..., description="YouTube Music URL")
    

class DownloadResponse(BaseModel):
    """Schema for download response."""
    id: int
    url: str
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    type: str
    status: str
    progress: float
    error_message: Optional[str] = None
    file_path: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class DownloadStatusUpdate(BaseModel):
    """Schema for WebSocket status updates."""
    id: int
    status: str
    progress: float
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    error_message: Optional[str] = None


class ErrorResponse(BaseModel):
    """Schema for error responses."""
    detail: str
