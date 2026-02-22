# backend/app/cookies_manager.py
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_COOKIES_FILENAME = "youtube_cookies.txt"


class CookiesManager:
    """
    Manages a single Netscape-format cookies file used by yt-dlp.

    The file lives on the named Docker volume so it persists across
    container recreations.
    """

    def __init__(self) -> None:
        # Ensure the directory exists with correct permissions on first access
        self._dir: Path = settings.cookies_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file: Path = self._dir / _COOKIES_FILENAME

    # ── Public helpers ────────────────────────────────────────────────────────

    def has_cookies(self) -> bool:
        """Return True when a non-empty cookies file is present."""
        return self._file.exists() and self._file.stat().st_size > 0

    def get_cookies_path(self) -> Optional[str]:
        """
        Return absolute path string when cookies exist, else None.
        yt-dlp accepts a str path via the ``cookiefile`` option.
        """
        return str(self._file) if self.has_cookies() else None

    def save_cookies(self, content: str) -> None:
        """
        Persist validated Netscape cookie content.
        Raises ValueError on empty input (schema-level validation should
        catch format issues before we get here).
        """
        content = content.strip()
        if not content:
            raise ValueError("Cookie content is empty")

        # Write atomically: write to .tmp then rename
        tmp = self._file.with_suffix(".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            # os.replace is atomic on POSIX; on Windows it may raise if dst exists
            os.replace(tmp, self._file)
            logger.info("Cookies saved to %s (%d bytes)", self._file, self._file.stat().st_size)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def delete_cookies(self) -> bool:
        """Remove cookies file. Returns True if a file was actually deleted."""
        if self._file.exists():
            self._file.unlink()
            logger.info("Cookies deleted")
            return True
        return False

    def info(self) -> dict:
        """Return a serialisable status dict for the API response."""
        if not self.has_cookies():
            return {"exists": False, "size_bytes": 0, "last_modified": None}
        stat = self._file.stat()
        return {
            "exists":        True,
            "size_bytes":    stat.st_size,
            "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }


# Module-level singleton — import this everywhere
cookies_manager = CookiesManager()