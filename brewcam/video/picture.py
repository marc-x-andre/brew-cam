from brewcam.config import monitoring
from brewcam.video.background import STANDBY_SOURCE
from brewcam.video.probe import ensure_probe, probe_is_fresh, probe_path, stop_probe
from brewcam.video.starter import start_camera_attempt, start_filler
from brewcam.video.stopper import stop
from brewcam.video.supervisor import watch_running_process


def maintain_picture(
    state, camera_config, stream_config, overlay_text_path, snapshot_path,
    now, now_local, monitoring_dir, retention_days, youtube_target,
) -> None:
    if state.running_source == {"mode": "camera"} and state.process is not None:
        watch_running_process(
            state, snapshot_path, now, now_local, monitoring_dir, retention_days,
            youtube_target is not None,
        )
        stop_probe(state)
        if state.process is None:
            _ensure_standby(
                state, stream_config, overlay_text_path, snapshot_path,
                now_local, monitoring_dir, retention_days, youtube_target,
            )
        return

    if probe_is_fresh(state, probe_path(snapshot_path), now):
        stop_probe(state)
        if state.process is not None:
            stop(state)
        start_camera_attempt(
            state, camera_config, stream_config, overlay_text_path, snapshot_path,
            now, now_local, monitoring_dir, retention_days, youtube_target,
        )
        if state.process is None:
            _ensure_standby(
                state, stream_config, overlay_text_path, snapshot_path,
                now_local, monitoring_dir, retention_days, youtube_target,
            )
        return

    _ensure_standby(
        state, stream_config, overlay_text_path, snapshot_path,
        now_local, monitoring_dir, retention_days, youtube_target,
    )
    ensure_probe(
        state, camera_config, probe_path(snapshot_path), now, now_local,
        monitoring_dir, retention_days,
    )


def _ensure_standby(
    state, stream_config, overlay_text_path, snapshot_path,
    now_local, monitoring_dir, retention_days, youtube_target,
) -> None:
    desired = {"mode": "black"} if state.degraded_mode == STANDBY_SOURCE else STANDBY_SOURCE
    if _showing(state, desired):
        return
    crashed = _dead(state) and state.running_source == desired
    if state.process is not None:
        stop(state)
    if crashed and desired["mode"] != "black":
        state.degraded_mode = STANDBY_SOURCE
        monitoring.write_event(
            monitoring_dir, now_local, "background_degraded", {"mode": "image"}, retention_days,
        )
        desired = {"mode": "black"}
    start_filler(
        state, desired, stream_config, overlay_text_path, snapshot_path,
        now_local, monitoring_dir, retention_days, youtube_target,
    )


def _showing(state, desired) -> bool:
    return state.running_source == desired and state.process is not None and state.process.poll() is None


def _dead(state) -> bool:
    return state.process is not None and state.process.poll() is not None
