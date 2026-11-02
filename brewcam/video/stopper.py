import subprocess
from datetime import datetime

from brewcam.config import monitoring
from brewcam.video.state import VideoState


def _terminate(process) -> None:
    if process is None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def stop(
    state: VideoState,
    youtube_leg_active: bool = False,
    monitoring_dir: str | None = None,
    now_local: datetime | None = None,
    retention_days: int = 30,
) -> None:
    _terminate(state.probe_process)
    state.probe_process = None
    state.probe_started_at = None
    if state.process is None:
        return
    _terminate(state.process)
    state.process = None
    state.running_source = None
    if youtube_leg_active:
        monitoring.write_event(monitoring_dir, now_local, "streaming_stopped", {}, retention_days)
