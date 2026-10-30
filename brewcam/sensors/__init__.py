from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SensorReading:
    sensor_id: str
    value: float | None
    unit: str
    measured_at: datetime | None
    status: str  # "available" | "stale" | "unavailable"


def classify_reading(
    sensor_id: str,
    value: float | None,
    unit: str,
    measured_at: datetime | None,
    now: datetime,
    stale_after_seconds: int,
) -> SensorReading:
    if value is None or measured_at is None:
        status = "unavailable"
    elif (now - measured_at).total_seconds() > stale_after_seconds:
        status = "stale"
    else:
        status = "available"
    return SensorReading(sensor_id, value, unit, measured_at, status)
