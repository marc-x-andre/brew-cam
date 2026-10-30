import subprocess
from datetime import datetime
from pathlib import Path

from brewcam.config import SessionConfig, monitoring
from brewcam.schedule import StageState
from brewcam.video import background
from brewcam.video.starter import start_camera_attempt, start_filler
from brewcam.video.state import VideoState
from brewcam.video.supervisor import watch_running_process

__all__ = ["VideoState", "tick", "stop"]


def tick(
    state: VideoState,
    camera_config: dict,
    stream_config: dict,
    session: SessionConfig,
    schedule_state: StageState,
    overlay_text_path: Path,
    snapshot_path: Path,
    now: datetime,
    now_local: datetime,
    monitoring_dir: str,
    retention_days: int,
) -> dict:
    configured_mode = background.resolve_configured_mode(session, schedule_state)
    camera_retry_due = state.next_restart_at is None or now >= state.next_restart_at

    if configured_mode["mode"] == "camera":
        desired = (
            {"mode": "camera"}
            if (state.running_source == {"mode": "camera"} or camera_retry_due)
            else {"mode": "black"}
        )
    elif state.degraded_mode == configured_mode:
        desired = {"mode": "black"}
    else:
        desired = configured_mode

    if state.running_source == {"mode": "camera"} and desired == {"mode": "camera"}:
        watch_running_process(state, snapshot_path, now, now_local, monitoring_dir, retention_days)
        if state.process is None:  # just failed/stalled this tick — keep the preview fresh immediately
            start_filler(
                state, {"mode": "black"}, stream_config, overlay_text_path, snapshot_path,
                now_local, monitoring_dir, retention_days,
            )
    elif state.running_source != desired or (state.process is not None and state.process.poll() is not None):
        filler_crashed_on_own = (
            state.process is not None
            and state.process.poll() is not None
            and state.running_source == desired
        )
        if state.process is not None:
            stop(state)
        if desired["mode"] == "camera":
            start_camera_attempt(
                state, camera_config, stream_config, overlay_text_path, snapshot_path,
                now, now_local, monitoring_dir, retention_days,
            )
        elif filler_crashed_on_own and desired["mode"] != "black":
            state.degraded_mode = desired
            monitoring.write_event(
                monitoring_dir, now_local, "background_degraded", {"mode": desired["mode"]}, retention_days,
            )
            desired = {"mode": "black"}
            start_filler(
                state, desired, stream_config, overlay_text_path, snapshot_path,
                now_local, monitoring_dir, retention_days,
            )
        else:
            start_filler(
                state, desired, stream_config, overlay_text_path, snapshot_path,
                now_local, monitoring_dir, retention_days,
            )

    display_source = background.resolve_display_source(desired, state.status)
    return {"background_mode": display_source["mode"], "ffmpeg_running": state.process is not None}


def stop(state: VideoState) -> None:
    if state.process is None:
        return
    state.process.terminate()
    try:
        state.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        state.process.kill()
    state.process = None
    state.running_source = None
