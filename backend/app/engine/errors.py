"""Typed error classification. Never substring-match a whole log dump —
walk the exception cause chain and inspect real types/status codes."""
from ..models import ErrorClass


class EngineError(Exception):
    def __init__(self, error_class: ErrorClass, message: str, candidates: list | None = None):
        super().__init__(message)
        self.error_class = error_class
        self.candidates = candidates or []


def _walk_causes(exc: BaseException):
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        # yt-dlp's DownloadError carries the original in exc_info
        exc_info = getattr(current, "exc_info", None)
        if exc_info and isinstance(exc_info, tuple) and isinstance(exc_info[1], BaseException):
            yield exc_info[1]
        current = current.__cause__ or current.__context__


# Extractor-message fragments that indicate the *video* is gone, not our setup.
_PERMANENT_MARKERS = (
    "private video", "video unavailable", "has been removed", "account associated",
    "members-only", "drm", "this video is not available", "copyright",
    "geo restriction", "not available in your country", "age-restricted",
)


def classify(exc: BaseException) -> tuple[ErrorClass, str]:
    """Returns (class, human message)."""
    try:
        from yt_dlp.networking.exceptions import HTTPError as YDLHTTPError
    except ImportError:
        YDLHTTPError = ()
    try:
        from yt_dlp.utils import ExtractorError
    except ImportError:
        ExtractorError = ()

    message = str(exc)
    for e in _walk_causes(exc):
        if YDLHTTPError and isinstance(e, YDLHTTPError):
            status = getattr(e, "status", None)
            if status == 429:
                return ErrorClass.RATE_LIMITED, f"HTTP 429: {e}"
            if status == 403:
                return ErrorClass.FORBIDDEN, f"HTTP 403: {e}"
            if status and status >= 500:
                return ErrorClass.TRANSIENT, f"HTTP {status}: {e}"
        if ExtractorError and isinstance(e, ExtractorError):
            m = str(e).lower()
            if "sign in to confirm" in m or "not a bot" in m:
                return ErrorClass.BOT_FLAGGED, str(e)
            if "429" in m or "too many requests" in m:
                return ErrorClass.RATE_LIMITED, str(e)
            if any(marker in m for marker in _PERMANENT_MARKERS):
                return ErrorClass.PERMANENT, str(e)
        if isinstance(e, (TimeoutError, ConnectionError, OSError)) and not isinstance(e, PermissionError):
            return ErrorClass.TRANSIENT, message
    return ErrorClass.TRANSIENT, message
