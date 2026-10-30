import math
from datetime import datetime

from brewcam.sensors import SensorReading, classify_reading


def read_temperature(now: datetime, stale_after_seconds: int) -> SensorReading:
    value = 20.0 + 2.0 * math.sin(now.timestamp() / 300)
    return classify_reading(
        "temperature", value, "C", now, now, stale_after_seconds
    )
