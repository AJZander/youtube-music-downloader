import os
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class CookiesManager:
    """Manages YouTube cookies for authentication."""
    
    def __init__(self, cookies_dir: Path = None):
        """Initialize cookies manager.
        
        Args:
            cookies_dir: Directory to store cookies file
        """
        self.cookies_dir = cookies_dir or Path("/app/data/cookies")
        self.cookies_dir.mkdir(parents=True, exist_ok=True)
        self.cookies_file = self.cookies_dir / "youtube_cookies.txt"
        
    def has_cookies(self) -> bool:
        """Check if cookies file exists."""
        return self.cookies_file.exists() and self.cookies_file.stat().st_size > 0
    
    def get_cookies_path(self) -> Optional[str]:
        """Get path to cookies file if it exists."""
        if self.has_cookies():
            return str(self.cookies_file)
        return None
    
    def save_cookies(self, cookies_content: str) -> bool:
        """Save cookies content to file.
        
        Args:
            cookies_content: Cookies in Netscape format
            
        Returns:
            True if saved successfully
        """
        try:
            # Validate it's not empty
            if not cookies_content or not cookies_content.strip():
                raise ValueError("Cookies content is empty")
            
            # Write to file
            self.cookies_file.write_text(cookies_content)
            logger.info(f"Saved cookies to {self.cookies_file}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save cookies: {e}")
            raise
    
    def delete_cookies(self) -> bool:
        """Delete cookies file.
        
        Returns:
            True if deleted successfully
        """
        try:
            if self.cookies_file.exists():
                self.cookies_file.unlink()
                logger.info("Deleted cookies")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete cookies: {e}")
            raise
    
    def get_cookies_info(self) -> dict:
        """Get information about stored cookies.
        
        Returns:
            Dictionary with cookies info
        """
        if not self.has_cookies():
            return {
                "exists": False,
                "size": 0,
                "modified": None
            }
        
        stat = self.cookies_file.stat()
        return {
            "exists": True,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
        }


# Global instance
cookies_manager = CookiesManager()
