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


def _log_failure_and_retry(
    monitoring_dir, now_local, retention_days, youtube_leg_was_active, delay, event_name, details
) -> None:
    monitoring.write_event(monitoring_dir, now_local, event_name, details, retention_days)
    if youtube_leg_was_active:
        monitoring.write_event(
            monitoring_dir, now_local, "streaming_retry", {"delay_seconds": delay}, retention_days
        )


def watch_running_process(
    state: VideoState,
    snapshot_path: Path,
    now: datetime,
    now_local: datetime,
    monitoring_dir: str,
    retention_days: int,
    youtube_leg_was_active: bool = False,
) -> None:
    if state.process.poll() is not None:
        previous_status = state.status
        excerpt = _stderr_excerpt(state.process)
        state.process = None
        state.status = "unavailable"
        state.failure_count += 1
        delay = backoff_seconds(state.failure_count)
        state.next_restart_at = now + timedelta(seconds=delay)
        if previous_status in ("connected", "stalled"):
            event_name = "streaming_failed" if youtube_leg_was_active else "camera_disconnected"
            _log_failure_and_retry(
                monitoring_dir, now_local, retention_days, youtube_leg_was_active, delay,
                event_name, {"excerpt": excerpt},
            )
        elif previous_status == "connecting":
            event_name = "streaming_failed" if youtube_leg_was_active else "camera_reconnecting"
            details = (
                {"excerpt": excerpt} if youtube_leg_was_active
                else {"failure_count": state.failure_count, "excerpt": excerpt}
            )
            _log_failure_and_retry(
                monitoring_dir, now_local, retention_days, youtube_leg_was_active, delay, event_name, details
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
            if youtube_leg_was_active:
                event_name = "streaming_recovered" if state.failure_count > 0 else "streaming_started"
                monitoring.write_event(monitoring_dir, now_local, event_name, {}, retention_days)
        elif (now - state.connected_since).total_seconds() >= HEALTHY_RESET_SECONDS:
            state.failure_count = 0
        return

    connecting_elapsed = (now - state.process_started_at).total_seconds()
    if state.status == "connected" or connecting_elapsed > STALL_TIMEOUT_SECONDS:
        state.process.terminate()
        state.process = None
        state.status = "stalled"
        state.failure_count += 1
        delay = backoff_seconds(state.failure_count)
        state.next_restart_at = now + timedelta(seconds=delay)
        event_name = "streaming_failed" if youtube_leg_was_active else "camera_stalled"
        _log_failure_and_retry(
            monitoring_dir, now_local, retention_days, youtube_leg_was_active, delay, event_name, {}
        )
