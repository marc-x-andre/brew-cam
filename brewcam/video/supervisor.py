from datetime import datetime, timedelta
from pathlib import Path

from brewcam.config import monitoring
from brewcam.video.state import (
    HEALTHY_RESET_SECONDS,
    STALL_TIMEOUT_SECONDS,
    VideoState,
    backoff_seconds,
)


def watch_running_process(
    state: VideoState,
    snapshot_path: Path,
    now: datetime,
    now_local: datetime,
    monitoring_dir: str,
    retention_days: int,
) -> None:
    if state.process.poll() is not None:
        previous_status = state.status
        state.process = None
        state.status = "unavailable"
        state.failure_count += 1
        state.next_restart_at = now + timedelta(seconds=backoff_seconds(state.failure_count))
        if previous_status in ("connected", "stalled"):
            monitoring.write_event(monitoring_dir, now_local, "camera_disconnected", {}, retention_days)
        return

    is_fresh = (
        snapshot_path.exists()
        and snapshot_path.stat().st_mtime >= state.process_started_at.timestamp()
        and (now.timestamp() - snapshot_path.stat().st_mtime) <= STALL_TIMEOUT_SECONDS
    )

    if is_fresh:
        if state.status != "connected":
            state.status = "connected"
            state.connected_since = now
            monitoring.write_event(monitoring_dir, now_local, "camera_connected", {}, retention_days)
        elif (now - state.connected_since).total_seconds() >= HEALTHY_RESET_SECONDS:
            state.failure_count = 0
        return

    if state.status == "connected":
        state.process.terminate()
        state.process = None
        state.status = "stalled"
        state.failure_count += 1
        state.next_restart_at = now + timedelta(seconds=backoff_seconds(state.failure_count))
        monitoring.write_event(monitoring_dir, now_local, "camera_stalled", {}, retention_days)
