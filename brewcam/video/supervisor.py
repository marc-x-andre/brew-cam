from datetime import datetime, timedelta
from pathlib import Path

from brewcam.config import monitoring
from brewcam.video import youtube
from brewcam.video.state import (
    HEALTHY_RESET_SECONDS,
    STALL_TIMEOUT_SECONDS,
    VideoState,
    backoff_seconds,
)


def _stderr_excerpt(process) -> str:
    stderr_bytes = process.stderr.read() if process.stderr else b""
    stderr_text = youtube.redact_stderr(stderr_bytes.decode(errors="replace"))
    return "\n".join(stderr_text.splitlines()[-20:])


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
        excerpt = _stderr_excerpt(state.process)
        state.process = None
        state.status = "unavailable"
        state.failure_count += 1
        state.next_restart_at = now + timedelta(seconds=backoff_seconds(state.failure_count))
        if previous_status in ("connected", "stalled"):
            monitoring.write_event(
                monitoring_dir, now_local, "camera_disconnected", {"excerpt": excerpt}, retention_days
            )
        elif previous_status == "connecting":
            monitoring.write_event(
                monitoring_dir, now_local, "camera_reconnecting",
                {"failure_count": state.failure_count, "excerpt": excerpt}, retention_days,
            )
        return

    is_fresh = False
    try:
        mtime = snapshot_path.stat().st_mtime
        is_fresh = (
            mtime > state.process_started_at.timestamp()
            and (now.timestamp() - mtime) <= STALL_TIMEOUT_SECONDS
        )
    except OSError:
        pass  # file missing or briefly unreadable mid-write — treat as not-yet-fresh

    if is_fresh:
        if state.status != "connected":
            state.status = "connected"
            state.connected_since = now
            monitoring.write_event(monitoring_dir, now_local, "camera_connected", {}, retention_days)
        elif (now - state.connected_since).total_seconds() >= HEALTHY_RESET_SECONDS:
            state.failure_count = 0
        return

    connecting_elapsed = (now - state.process_started_at).total_seconds()
    if state.status == "connected" or connecting_elapsed > STALL_TIMEOUT_SECONDS:
        state.process.terminate()
        state.process = None
        state.status = "stalled"
        state.failure_count += 1
        state.next_restart_at = now + timedelta(seconds=backoff_seconds(state.failure_count))
        monitoring.write_event(monitoring_dir, now_local, "camera_stalled", {}, retention_days)
