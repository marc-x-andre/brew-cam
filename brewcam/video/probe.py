import subprocess
from datetime import timedelta
from pathlib import Path

from brewcam.config import monitoring
from brewcam.video.camera import (
    CameraSourceError,
    build_snapshot_command,
    redact_url,
    resolve_rtsp_url,
)
from brewcam.video.state import (
    STALL_TIMEOUT_SECONDS,
    UNAVAILABLE_RETRY_SECONDS,
    VideoState,
    backoff_seconds,
)


def probe_path(snapshot_path: Path) -> Path:
    return snapshot_path.with_name("camera_frame.jpg")


def stop_probe(state: VideoState) -> None:
    process = state.probe_process
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    state.probe_process = None
    state.probe_started_at = None


def probe_is_fresh(state: VideoState, path: Path, now) -> bool:
    if state.probe_started_at is None:
        return False
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    age = now.timestamp() - mtime
    return mtime > state.probe_started_at.timestamp() and age < STALL_TIMEOUT_SECONDS


def ensure_probe(state, camera_config, path, now, now_local, monitoring_dir, retention_days) -> None:
    if state.probe_process is not None and state.probe_process.poll() is None:
        return
    if state.probe_process is not None:
        state.probe_process = None
        state.probe_started_at = None
        state.failure_count += 1
        state.next_restart_at = now + timedelta(seconds=backoff_seconds(state.failure_count))
        return
    if state.next_restart_at is not None and now < state.next_restart_at:
        return
    _start_probe(state, camera_config, path, now, now_local, monitoring_dir, retention_days)


def _start_probe(state, camera_config, path, now, now_local, monitoring_dir, retention_days) -> None:
    try:
        rtsp_url = resolve_rtsp_url(camera_config)
    except CameraSourceError:
        state.next_restart_at = now + timedelta(seconds=UNAVAILABLE_RETRY_SECONDS)
        monitoring.write_event(monitoring_dir, now_local, "camera_unavailable", {}, retention_days)
        return
    command = build_snapshot_command(rtsp_url, path)
    try:
        state.probe_process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        state.probe_started_at = now
    except OSError:
        state.failure_count += 1
        state.next_restart_at = now + timedelta(seconds=backoff_seconds(state.failure_count))
        monitoring.write_event(
            monitoring_dir, now_local, "camera_unavailable",
            {"source": redact_url(rtsp_url)}, retention_days,
        )
