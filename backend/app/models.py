from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
from enum import Enum

Base = declarative_base()


class DownloadStatus(str, Enum):
    """Status enum for downloads."""
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Download(Base):
    """Download task model."""
    
    __tablename__ = "downloads"
    
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, nullable=False)
    title = Column(String, nullable=True)
    artist = Column(String, nullable=True)
    album = Column(String, nullable=True)
    type = Column(String, nullable=False)  # song, album, artist, playlist
    status = Column(String, nullable=False, default=DownloadStatus.QUEUED)
    progress = Column(Float, default=0.0)
    error_message = Column(Text, nullable=True)
    file_path = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        """Convert model to dictionary."""
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "type": self.type,
            "status": self.status,
            "progress": self.progress,
            "error_message": self.error_message,
            "file_path": self.file_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
