import os
from pathlib import Path
from typing import Any, Dict

class WorkspaceLock:
    """Simple file-based lock to prevent concurrent modifications to the same repo."""
    
    def __init__(self, root_path: Path):
        self.lock_file = root_path / ".factory.lock"

    def acquire(self):
        """Blocks until lock is acquired."""
        while self.lock_file.exists():
            # Check for stale lock (older than 1 hour)
            if (time.time() - self.lock_file.stat().st_mtime) > 3600:
                self.lock_file.unlink()
                break
            time.sleep(5)
        self.lock_file.touch()

    def release(self):
        """Releases the lock."""
        if self.lock_file.exists():
            self.lock_file.unlink()

# To be used in NodeHandlers
# lock = WorkspaceLock(Path("/Users/beauroberts/github-analysis/marketing-as-code"))
# lock.acquire()
# ... do work ...
# lock.release()
