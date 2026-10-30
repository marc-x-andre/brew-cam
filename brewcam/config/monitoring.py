from datetime import datetime

from brewcam.overlay import OverlayData
from brewcam.sensors import SensorReading
from brewcam.utils import jsonl

SCHEMA_VERSION = 1


def build_snapshot_record(
    now: datetime, overlay: OverlayData, video_status: dict, temperature: SensorReading
) -> dict:
    return {
        "timestamp": now.isoformat(),
        "event_type": "snapshot",
        "schema_version": SCHEMA_VERSION,
        "session": {
            "stage": overlay.stage_name,
            "progress_pct": overlay.stage_progress_pct,
        },
        "video": {
            "background": video_status["background_mode"],
            "ffmpeg_running": video_status["ffmpeg_running"],
        },
        "sensors": {
            "temperature": {
                "value": temperature.value,
                "unit": temperature.unit,
                "status": temperature.status,
                "measured_at": (
                    temperature.measured_at.isoformat()
                    if temperature.measured_at is not None
                    else None
                ),
            }
        },
    }


def build_event_record(now: datetime, event_type: str, details: dict) -> dict:
    return {
        "timestamp": now.isoformat(),
        "event_type": event_type,
        "schema_version": SCHEMA_VERSION,
        "details": details,
    }


def write_snapshot(
    directory: str,
    now_local: datetime,
    overlay: OverlayData,
    video_status: dict,
    retention_days: int,
    temperature: SensorReading,
) -> None:
    record = build_snapshot_record(now_local, overlay, video_status, temperature)
    jsonl.append_jsonl(directory, now_local.date(), record)
    jsonl.enforce_retention(directory, now_local.date(), retention_days)


def write_event(
    directory: str,
    now_local: datetime,
    event_type: str,
    details: dict,
    retention_days: int,
) -> None:
    record = build_event_record(now_local, event_type, details)
    jsonl.append_jsonl(directory, now_local.date(), record)
    jsonl.enforce_retention(directory, now_local.date(), retention_days)
