import signal
import time
from zoneinfo import ZoneInfo

from brewcam.config import load_config
from brewcam.config import monitoring
from brewcam.overlay import build_overlay
from brewcam.schedule import compute_schedule_state
from brewcam.sensors import simulated
from brewcam.utils.clock import utc_now

VIDEO_STATUS = {"background_mode": "black", "ffmpeg_running": False}


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

    try:
        while not stop:
            now = utc_now()
            state = compute_schedule_state(config.steps, now)
            reading = simulated.read_temperature(
                now, config.sensors["temperature"]["stale_after_seconds"]
            )
            overlay = build_overlay(config, state, reading)
            now_local = now.astimezone(tz)

            if (
                last_snapshot_at is None
                or (now - last_snapshot_at).total_seconds() >= interval_seconds
            ):
                monitoring.write_snapshot(
                    config.monitoring["directory"],
                    now_local,
                    overlay,
                    VIDEO_STATUS,
                    config.monitoring["retention_days"],
                    reading,
                )
                last_snapshot_at = now

            time.sleep(1)
    except Exception as exc:
        monitoring.write_event(
            config.monitoring["directory"],
            now_local,
            "error",
            {"message": str(exc)},
            config.monitoring["retention_days"],
        )
        raise

    monitoring.write_event(
        config.monitoring["directory"],
        now_local,
        "shutdown",
        {},
        config.monitoring["retention_days"],
    )
