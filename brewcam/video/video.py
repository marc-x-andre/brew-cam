import subprocess
from datetime import datetime
from pathlib import Path

from brewcam.video.camera import build_snapshot_command
from brewcam.video.starter import start_if_due
from brewcam.video.state import VideoState
from brewcam.video.supervisor import watch_running_process

__all__ = ["VideoState", "build_snapshot_command", "tick", "stop"]


def tick(
    state: VideoState,
    camera_config: dict,
    snapshot_path: Path,
    now: datetime,
    now_local: datetime,
    monitoring_dir: str,
    retention_days: int,
) -> dict:
    if state.process is None:
        start_if_due(state, camera_config, snapshot_path, now, now_local, monitoring_dir, retention_days)
    else:
        watch_running_process(state, snapshot_path, now, now_local, monitoring_dir, retention_days)

    return {
        "background_mode": "camera" if state.status == "connected" else "black",
        "ffmpeg_running": state.process is not None,
    }


def stop(state: VideoState) -> None:
    if state.process is None:
        return
    state.process.terminate()
    try:
        state.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        state.process.kill()
    state.process = None
