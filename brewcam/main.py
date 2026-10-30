import signal
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from brewcam.config import load_config
from brewcam.config import monitoring
from brewcam.overlay import build_overlay
from brewcam.schedule import compute_schedule_state
from brewcam.sensors import simulated
from brewcam.utils.clock import utc_now
from brewcam.video import video


def run(config_path: str) -> None:
    config = load_config(config_path)

    stop = False

    def _handle_sigint(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_sigint)

    tz = ZoneInfo(config.session.timezone)
    interval_seconds = config.monitoring["interval_seconds"]
    last_snapshot_at = None
    now_local = utc_now().astimezone(tz)
    video_state = video.VideoState()
    snapshot_path = Path(config.monitoring["directory"]).parent / "runtime" / "camera_preview.jpg"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        while not stop:
            now = utc_now()
            now_local = now.astimezone(tz)
            state = compute_schedule_state(config.steps, now)
            reading = simulated.read_temperature(
                now, config.sensors["temperature"]["stale_after_seconds"]
            )
            overlay = build_overlay(config, state, reading)
            video_status = video.tick(
                video_state,
                config.camera,
                snapshot_path,
                now,
                now_local,
                config.monitoring["directory"],
                config.monitoring["retention_days"],
            )

            if (
                last_snapshot_at is None
                or (now - last_snapshot_at).total_seconds() >= interval_seconds
            ):
                monitoring.write_snapshot(
                    config.monitoring["directory"],
                    now_local,
                    overlay,
                    video_status,
                    config.monitoring["retention_days"],
                    reading,
                )
                last_snapshot_at = now

            time.sleep(1)
    except Exception as exc:
        video.stop(video_state)
        monitoring.write_event(
            config.monitoring["directory"],
            now_local,
            "error",
            {"message": str(exc)},
            config.monitoring["retention_days"],
        )
        raise

    video.stop(video_state)
    monitoring.write_event(
        config.monitoring["directory"],
        now_local,
        "shutdown",
        {},
        config.monitoring["retention_days"],
    )
