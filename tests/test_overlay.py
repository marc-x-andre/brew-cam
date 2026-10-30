import ast
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from brewcam.config import load_config
from brewcam.overlay import build_overlay
from brewcam.schedule import compute_schedule_state
from brewcam.sensors import SensorReading, classify_reading, thermopro

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


VALID_YAML = textwrap.dedent("""
session:
  name: "Test Brew"
  timezone: "America/Toronto"
  background: {mode: black}
  before: {mode: black}
  after: {mode: black}
steps:
  - name: "Mash"
    description: "desc"
    start: "2026-11-07T09:00:00-05:00"
    end: "2026-11-07T10:00:00-05:00"
    target_temperature_c: 67
camera:
  source_env: "CAMERA_SOURCE"
sensors:
  temperature: {enabled: true, driver: simulated, stale_after_seconds: 120}
stream:
  destination: youtube
  ingest_url_env: "YOUTUBE_INGEST_URL"
  key_env: "YOUTUBE_STREAM_KEY"
  width: 1280
  height: 720
  fps: 25
overlays:
  enabled: [session, temperature, stage, progress]
monitoring:
  interval_seconds: 60
  directory: logs
  retention_days: 30
""")


def _load(tmp_path):
    cfg_path = tmp_path / "session.yaml"
    cfg_path.write_text(VALID_YAML)
    return load_config(str(cfg_path))


def test_build_overlay_active_stage_with_available_temperature(tmp_path):
    config = _load(tmp_path)
    state = compute_schedule_state(config.steps, config.steps[0].start)
    reading = SensorReading("temp", 20.0, "C", config.steps[0].start, "available")
    overlay = build_overlay(config, state, reading)
    assert overlay.session_name == config.session.name
    assert overlay.stage_name == "Mash"
    assert overlay.temperature_display == "20.0C"
    assert overlay.status_label == "active"


def test_build_overlay_shows_unavailable_temperature(tmp_path):
    config = _load(tmp_path)
    state = compute_schedule_state(config.steps, config.steps[0].start)
    reading = SensorReading("temp", None, "C", None, "unavailable")
    overlay = build_overlay(config, state, reading)
    assert overlay.temperature_display == "Unavailable"


def test_build_overlay_shows_unavailable_temperature_for_stale_reading(tmp_path):
    # A stale reading must never display as current (charter requirement), and the
    # brief's design decision says stale collapses to the same "Unavailable" string.
    config = _load(tmp_path)
    state = compute_schedule_state(config.steps, config.steps[0].start)
    reading = SensorReading("temp", 20.0, "C", config.steps[0].start - timedelta(minutes=10), "stale")
    overlay = build_overlay(config, state, reading)
    assert overlay.temperature_display == "Unavailable"


def test_build_overlay_before_first_step_has_no_stage_name(tmp_path):
    config = _load(tmp_path)
    state = compute_schedule_state(config.steps, config.steps[0].start - timedelta(hours=1))
    reading = SensorReading("temp", None, "C", None, "unavailable")
    overlay = build_overlay(config, state, reading)
    assert overlay.status_label == "upcoming"
    assert overlay.stage_name == ""


def test_overlay_module_has_no_camera_or_youtube_dependency():
    source = Path("brewcam/overlay.py").read_text()
    tree = ast.parse(source)
    imported = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert not any(name and ("camera" in name or "youtube" in name or "video" in name) for name in imported)
