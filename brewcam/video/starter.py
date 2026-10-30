import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from brewcam.config import monitoring
from brewcam.video import filters
from brewcam.video.camera import CameraSourceError, redact_url, resolve_rtsp_url
from brewcam.video.state import UNAVAILABLE_RETRY_SECONDS, VideoState, backoff_seconds


def start_camera_attempt(
    state: VideoState,
    camera_config: dict,
    stream_config: dict,
    overlay_text_path: Path,
    snapshot_path: Path,
    now: datetime,
    now_local: datetime,
    monitoring_dir: str,
    retention_days: int,
) -> None:
    try:
        rtsp_url = resolve_rtsp_url(camera_config)
    except CameraSourceError:
        state.status = "unavailable"
        state.process = None
        state.running_source = None
        state.next_restart_at = now + timedelta(seconds=UNAVAILABLE_RETRY_SECONDS)
        monitoring.write_event(monitoring_dir, now_local, "camera_unavailable", {}, retention_days)
        return

    command = filters.build_compose_command(
        {"mode": "camera"}, rtsp_url, overlay_text_path, stream_config, snapshot_path
    )
    try:
        state.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state.process_started_at = now
        state.status = "connecting"
        state.running_source = {"mode": "camera"}
    except OSError:
        state.failure_count += 1
        state.next_restart_at = now + timedelta(seconds=backoff_seconds(state.failure_count))
        monitoring.write_event(
            monitoring_dir, now_local, "camera_unavailable", {"source": redact_url(rtsp_url)}, retention_days
        )


def start_filler(
    state: VideoState,
    target_mode: dict,
    stream_config: dict,
    overlay_text_path: Path,
    snapshot_path: Path,
    now_local: datetime,
    monitoring_dir: str,
    retention_days: int,
) -> None:
    command = filters.build_compose_command(target_mode, None, overlay_text_path, stream_config, snapshot_path)
    try:
        state.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state.running_source = target_mode
    except OSError:
        monitoring.write_event(
            monitoring_dir, now_local, "filler_start_failed", {"mode": target_mode.get("mode")}, retention_days
        )
