import subprocess
from datetime import datetime

from brewcam.config import monitoring
from brewcam.video.state import VideoState


def stop(
    state: VideoState,
    youtube_leg_active: bool = False,
    monitoring_dir: str | None = None,
    now_local: datetime | None = None,
    retention_days: int = 30,
) -> None:
    if state.process is None:
        return
    state.process.terminate()
    try:
        state.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        state.process.kill()
    state.process = None
    state.running_source = None
    if youtube_leg_active:
        monitoring.write_event(monitoring_dir, now_local, "streaming_stopped", {}, retention_days)
