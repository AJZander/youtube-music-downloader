from enum import StrEnum

from pydantic import BaseModel


class JobStatus(StrEnum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    IMPORTING = "importing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TrackStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    VALIDATING = "validating"
    IMPORTING = "importing"
    COMPLETED = "completed"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    IMPORT_FAILED = "import_failed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TRACK_STATUSES = {
    TrackStatus.COMPLETED,
    TrackStatus.SKIPPED_DUPLICATE,
    TrackStatus.FAILED,
    TrackStatus.CANCELLED,
    TrackStatus.NEEDS_REVIEW,
    TrackStatus.IMPORT_FAILED,
}


class ErrorClass(StrEnum):
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    BOT_FLAGGED = "bot_flagged"
    FORBIDDEN = "forbidden"
    PERMANENT = "permanent"
    NO_CONFIDENT_MATCH = "no_confident_match"
    VALIDATION_FAILED = "validation_failed"
    IMPORT_FAILED = "import_failed"


class TrackOut(BaseModel):
    id: str
    job_id: str
    position: int | None = None
    provider: str | None = None
    provider_track_id: str | None = None
    source_url: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    title: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    isrc: str | None = None
    status: TrackStatus
    stage: str | None = None
    progress_pct: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    match_candidates: str | None = None
    library_path: str | None = None
    format: str | None = None
    bitrate_kbps: int | None = None
    below_target: bool = False
    duration_sec: float | None = None
    plex_verified: bool = False
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class JobOut(BaseModel):
    id: str
    source_url: str
    provider: str
    source_type: str
    title: str | None = None
    artist: str | None = None
    force: bool = False
    status: JobStatus
    error_code: str | None = None
    error_message: str | None = None
    total_tracks: int = 0
    completed_tracks: int = 0
    failed_tracks: int = 0
    skipped_tracks: int = 0
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    tracks: list[TrackOut] | None = None


def row_to_track(row) -> TrackOut:
    d = dict(row)
    d["below_target"] = bool(d.get("below_target"))
    d["plex_verified"] = bool(d.get("plex_verified"))
    return TrackOut(**{k: v for k, v in d.items() if k in TrackOut.model_fields})


def row_to_job(row, tracks=None) -> JobOut:
    d = dict(row)
    d["force"] = bool(d.get("force"))
    job = JobOut(**{k: v for k, v in d.items() if k in JobOut.model_fields})
    if tracks is not None:
        job.tracks = [row_to_track(t) for t in tracks]
    return job
