from datetime import datetime, timedelta, timezone

import pytest

from brewcam.sensors import classify_reading, thermopro

NOW = datetime(2026, 11, 7, 9, 30, tzinfo=timezone.utc)


def test_fresh_reading_is_available():
    r = classify_reading("temp", 20.0, "C", NOW, NOW, stale_after_seconds=120)
    assert r.status == "available"


def test_old_reading_is_stale_not_unavailable():
    measured = NOW - timedelta(seconds=300)
    r = classify_reading("temp", 20.0, "C", measured, NOW, stale_after_seconds=120)
    assert r.status == "stale"


def test_missing_value_is_unavailable():
    r = classify_reading("temp", None, "C", None, NOW, stale_after_seconds=120)
    assert r.status == "unavailable"


def test_thermopro_driver_not_yet_implemented():
    with pytest.raises(NotImplementedError):
        thermopro.read_temperature(NOW, stale_after_seconds=120)
