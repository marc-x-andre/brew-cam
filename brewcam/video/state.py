import subprocess
from dataclasses import dataclass
from datetime import datetime

UNAVAILABLE_RETRY_SECONDS = 60
STALL_TIMEOUT_SECONDS = 15
HEALTHY_RESET_SECONDS = 60


@dataclass
class VideoState:
    process: subprocess.Popen | None = None
    status: str = "unavailable"
    failure_count: int = 0
    next_restart_at: datetime | None = None
    process_started_at: datetime | None = None
    connected_since: datetime | None = None
    running_source: dict | None = None
    degraded_mode: dict | None = None


def backoff_seconds(failure_count: int) -> float:
    return min(2 * (2 ** (failure_count - 1)), 60)
