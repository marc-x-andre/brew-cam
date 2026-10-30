from datetime import datetime

from brewcam.sensors import SensorReading


def read_temperature(now: datetime, stale_after_seconds: int) -> SensorReading:
    raise NotImplementedError(
        "ThermoPro Bluetooth driver pending exact model confirmation"
    )
