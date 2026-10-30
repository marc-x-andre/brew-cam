import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from brewcam.config import monitoring
from brewcam.video.camera import (
    CameraSourceError,
    build_snapshot_command,
    redact_url,
    resolve_rtsp_url,
)
from brewcam.video.state import UNAVAILABLE_RETRY_SECONDS, VideoState, backoff_seconds


def start_if_due(
    state: VideoState,
    camera_config: dict,
    snapshot_path: Path,
    now: datetime,
    now_local: datetime,
    monitoring_dir: str,
    retention_days: int,
) -> None:
    if state.next_restart_at is not None and now < state.next_restart_at:
        return

    try:
        rtsp_url = resolve_rtsp_url(camera_config)
    except CameraSourceError:
        state.status = "unavailable"
        state.next_restart_at = now + timedelta(seconds=UNAVAILABLE_RETRY_SECONDS)
        monitoring.write_event(monitoring_dir, now_local, "camera_unavailable", {}, retention_days)
        return

    command = build_snapshot_command(rtsp_url, snapshot_path)
    try:
        state.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state.process_started_at = now
        state.status = "connecting"
    except OSError:
        state.failure_count += 1
        state.next_restart_at = now + timedelta(seconds=backoff_seconds(state.failure_count))
        monitoring.write_event(
            monitoring_dir, now_local, "camera_unavailable", {"source": redact_url(rtsp_url)}, retention_days
        )
