from datetime import datetime
from pathlib import Path

from brewcam.video import youtube
from brewcam.video.background import resolve_display_source
from brewcam.video.picture import maintain_picture
from brewcam.video.state import VideoState
from brewcam.video.stopper import stop

__all__ = ["VideoState", "tick", "stop"]


def tick(
    state: VideoState,
    camera_config: dict,
    stream_config: dict,
    overlay_text_path: Path,
    snapshot_path: Path,
    now: datetime,
    now_local: datetime,
    monitoring_dir: str,
    retention_days: int,
) -> dict:
    youtube_target = youtube.resolve_or_degrade(
        state, stream_config, monitoring_dir, now_local, retention_days
    )
    maintain_picture(
        state, camera_config, stream_config, overlay_text_path, snapshot_path,
        now, now_local, monitoring_dir, retention_days, youtube_target,
    )
    source = state.running_source if state.running_source is not None else {"mode": "black"}
    display_source = resolve_display_source(source, state.status)
    return {
        "background_mode": display_source["mode"],
        "ffmpeg_running": state.process is not None,
        "youtube": {
            "configured": youtube_target is not None,
            "leg_active": youtube_target is not None and state.process is not None,
        },
    }
