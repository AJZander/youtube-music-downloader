# backend/app/models.py
from datetime import datetime
from enum import Enum

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, JSON, Boolean
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class DownloadStatus(str, Enum):
    QUEUED      = "queued"
    DOWNLOADING = "downloading"
    PROCESSING  = "processing"
    COMPLETED   = "completed"
    FAILED      = "failed"
    CANCELLED   = "cancelled"


class MetadataProcessingStatus(str, Enum):
    PENDING     = "pending"
    PROCESSING  = "processing" 
    COMPLETED   = "completed"
    FAILED      = "failed"
    CANCELLED   = "cancelled"


class Download(Base):
    __tablename__ = "downloads"

    id            = Column(Integer, primary_key=True, index=True)
    url           = Column(String(2048), nullable=False)
    title         = Column(String(512),  nullable=True)
    artist        = Column(String(512),  nullable=True)
    album         = Column(String(512),  nullable=True)
    # song | album | playlist | artist
    download_type = Column(String(32),   nullable=False, default="song")
    status        = Column(String(32),   nullable=False, default=DownloadStatus.QUEUED)
    progress      = Column(Float,        nullable=False, default=0.0)
    error_message = Column(Text,         nullable=True)
    file_path     = Column(String(1024), nullable=True)
    # Total tracks for playlist/album display
    total_tracks  = Column(Integer,      nullable=True)
    done_tracks   = Column(Integer,      nullable=True, default=0)
    # User-selected format ID
    format_id     = Column(String(128),  nullable=True, default="bestaudio/best")
    created_at    = Column(DateTime,     default=datetime.utcnow)
    updated_at    = Column(DateTime,     default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id":            self.id,
            "url":           self.url,
            "title":         self.title,
            "artist":        self.artist,
            "album":         self.album,
            "download_type": self.download_type,
            "status":        self.status,
            "progress":      round(self.progress, 1),
            "error_message": self.error_message,
            "file_path":     self.file_path,
            "total_tracks":  self.total_tracks,
            "done_tracks":   self.done_tracks,
            "format_id":     self.format_id,
            "created_at":    self.created_at.isoformat() if self.created_at else None,
            "updated_at":    self.updated_at.isoformat() if self.updated_at else None,
        }


class MetadataProcessingJob(Base):
    __tablename__ = "metadata_processing_jobs"

    id               = Column(String(36), primary_key=True)  # UUID
    channel_url      = Column(String(2048), nullable=False)
    channel_name     = Column(String(512), nullable=True)
    status           = Column(String(32), nullable=False, default=MetadataProcessingStatus.PENDING)
    progress         = Column(Float, nullable=False, default=0.0)
    total_items      = Column(Integer, nullable=True)
    processed_items  = Column(Integer, nullable=False, default=0)
    error_message    = Column(Text, nullable=True)
    metadata_results = Column(JSON, nullable=True)  # Store the discovered playlists
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel_url": self.channel_url,
            "channel_name": self.channel_name,
            "status": self.status,
            "progress": round(self.progress, 1),
            "total_items": self.total_items,
            "processed_items": self.processed_items,
            "error_message": self.error_message,
            "metadata_results": self.metadata_results,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class MetadataPlaylistItem(Base):
    __tablename__ = "metadata_playlist_items"

    id                    = Column(Integer, primary_key=True, index=True)
    processing_job_id     = Column(String(36), nullable=False, index=True)
    playlist_id           = Column(String(128), nullable=True)
    title                 = Column(String(512), nullable=False)
    url                   = Column(String(2048), nullable=False)
    thumbnail             = Column(String(2048), nullable=True)
    track_count           = Column(Integer, nullable=True)
    channel               = Column(String(512), nullable=True)
    channel_url           = Column(String(2048), nullable=True)
    source_tab            = Column(String(64), nullable=True)
    release_type          = Column(String(64), nullable=True)
    selected_for_download = Column(Boolean, nullable=False, default=False)
    # Additional metadata fields for better classification
    release_date          = Column(String(32), nullable=True)
    release_year          = Column(Integer, nullable=True)
    description           = Column(Text, nullable=True)
    total_duration        = Column(Integer, nullable=True)  # Total duration in seconds
    view_count            = Column(Integer, nullable=True)
    like_count            = Column(Integer, nullable=True)
    created_at            = Column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "processing_job_id": self.processing_job_id,
            "playlist_id": self.playlist_id,
            "title": self.title,
            "url": self.url,
            "thumbnail": self.thumbnail,
            "track_count": self.track_count,
            "channel": self.channel,
            "channel_url": self.channel_url,
            "source_tab": self.source_tab,
            "release_type": self.release_type,
            "selected_for_download": self.selected_for_download,
            "release_date": self.release_date,
            "release_year": self.release_year,
            "description": self.description,
            "total_duration": self.total_duration,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }