# backend/app/models.py
from datetime import datetime
from enum import Enum

from sqlalchemy import Column, DateTime, Float, Integer, String, Text
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
            "created_at":    self.created_at.isoformat() if self.created_at else None,
            "updated_at":    self.updated_at.isoformat() if self.updated_at else None,
        }