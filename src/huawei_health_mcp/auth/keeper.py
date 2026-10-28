"""File-based coordination between the session keeper and MCP processes.

The keeper holds the dedicated browser profile open (which locks it), so an
MCP process must not launch its own browser while the keeper is alive.
Instead it drops a refresh request file and waits for the keeper to save
fresh credentials. Files contain only timestamps, never credentials.
"""

from __future__ import annotations

import time
from pathlib import Path

HEARTBEAT_FILE = "keeper.heartbeat"
REQUEST_FILE = "keeper.refresh-request"
DEFAULT_MAX_AGE_SECONDS = 30.0


class KeeperChannel:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    @property
    def heartbeat_path(self) -> Path:
        return self.directory / HEARTBEAT_FILE

    @property
    def request_path(self) -> Path:
        return self.directory / REQUEST_FILE

    # keeper side -------------------------------------------------------
    def beat(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path.write_text(f"{time.time():.3f}", encoding="ascii")

    def stop(self) -> None:
        for path in (self.heartbeat_path, self.request_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def take_request(self) -> bool:
        """Consume a pending refresh request, if any."""
        try:
            self.request_path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    # MCP side ----------------------------------------------------------
    def is_alive(self, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> bool:
        try:
            stamp = float(self.heartbeat_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return False
        return 0 <= time.time() - stamp <= max_age_seconds

    def request_refresh(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.request_path.write_text(f"{time.time():.3f}", encoding="ascii")
        except OSError:
            pass
