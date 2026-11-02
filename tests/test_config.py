import textwrap
from datetime import datetime, timedelta, timezone
import pytest
from brewcam.config.validation import (
    ConfigError,
    validate_step_sequence,
    validate_stream_dimensions,
    validate_timezone_offset,
    validate_youtube_stream,
)
from brewcam.config import load_config, ConfigError

def _dt(h):
    return datetime(2026, 11, 7, h, 0, 0, tzinfo=timezone.utc)

def test_contiguous_sorted_spans_pass():
    validate_step_sequence([(_dt(9), _dt(10)), (_dt(10), _dt(11))])

def test_gap_between_spans_rejected():
    with pytest.raises(ConfigError):
        validate_step_sequence([(_dt(9), _dt(10)), (_dt(11), _dt(12))])

def test_overlap_between_spans_rejected():
    with pytest.raises(ConfigError):
        validate_step_sequence([(_dt(9), _dt(11)), (_dt(10), _dt(12))])

def test_zero_length_span_rejected():
    with pytest.raises(ConfigError):
        validate_step_sequence([(_dt(9), _dt(9))])

def test_correct_dst_offset_passes():
    # 2026-07-01 is daylight time in America/Toronto (UTC-4)
    moment = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    validate_timezone_offset(moment, "America/Toronto")

def test_wrong_offset_during_dst_rejected():
    # same wall-clock date, but tagged with standard-time offset (-5) instead of the
    # correct daylight offset (-4) for that instant in America/Toronto
    moment = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    with pytest.raises(ConfigError):
        validate_timezone_offset(moment, "America/Toronto")

VALID_YAML = textwrap.dedent("""
timezone: "America/Toronto"
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
  enabled: [temperature, stage, progress]
monitoring:
  interval_seconds: 60
  directory: logs
  retention_days: 30
""")

def test_load_config_happy_path(tmp_path):
    cfg_path = tmp_path / "session.yaml"
    cfg_path.write_text(VALID_YAML)
    config = load_config(str(cfg_path))
    assert config.timezone == "America/Toronto"
    assert len(config.steps) == 1
    assert config.steps[0].name == "Mash"
    assert config.camera["source_env"] == "CAMERA_SOURCE"


def _load(tmp_path, yaml_text):
    cfg_path = tmp_path / "session.yaml"
    cfg_path.write_text(yaml_text)
    return load_config(str(cfg_path))

def test_missing_start_key_rejected(tmp_path):
    bad = VALID_YAML.replace('start: "2026-11-07T09:00:00-05:00"\n', "")
    with pytest.raises(ConfigError):
        _load(tmp_path, bad)

def test_naive_timestamp_rejected(tmp_path):
    bad = VALID_YAML.replace('"2026-11-07T09:00:00-05:00"', '"2026-11-07T09:00:00"')
    with pytest.raises(ConfigError):
        _load(tmp_path, bad)

def test_zero_length_step_rejected(tmp_path):
    bad = VALID_YAML.replace(
        'end: "2026-11-07T10:00:00-05:00"', 'end: "2026-11-07T09:00:00-05:00"'
    )
    with pytest.raises(ConfigError):
        _load(tmp_path, bad)

def test_missing_timezone_rejected(tmp_path):
    bad = VALID_YAML.replace('timezone: "America/Toronto"\n', "")
    with pytest.raises(ConfigError):
        _load(tmp_path, bad)

def test_empty_steps_list_rejected(tmp_path):
    bad = VALID_YAML.replace(
        textwrap.dedent("""\
        steps:
          - name: "Mash"
            description: "desc"
            start: "2026-11-07T09:00:00-05:00"
            end: "2026-11-07T10:00:00-05:00"
            target_temperature_c: 67
        """),
        "steps: []\n",
    )
    with pytest.raises(ConfigError):
        _load(tmp_path, bad)


def test_validate_stream_dimensions_accepts_positive_integers():
    validate_stream_dimensions({"width": 1280, "height": 720, "fps": 25})  # must not raise

def test_validate_stream_dimensions_rejects_non_positive_width():
    with pytest.raises(ConfigError):
        validate_stream_dimensions({"width": 0, "height": 720, "fps": 25})

def test_validate_stream_dimensions_rejects_non_integer_fps():
    with pytest.raises(ConfigError):
        validate_stream_dimensions({"width": 1280, "height": 720, "fps": "fast"})

def test_stream_dimensions_checked_by_load_config(tmp_path):
    bad = VALID_YAML.replace("height: 720", "height: -720")
    with pytest.raises(ConfigError):
        _load(tmp_path, bad)


def test_validate_youtube_stream_is_a_no_op_when_destination_is_not_youtube():
    validate_youtube_stream({"destination": "none"})  # must not raise

def test_validate_youtube_stream_requires_ingest_url_env():
    with pytest.raises(ConfigError):
        validate_youtube_stream({"destination": "youtube", "key_env": "YOUTUBE_STREAM_KEY"})

def test_validate_youtube_stream_requires_key_env():
    with pytest.raises(ConfigError):
        validate_youtube_stream({"destination": "youtube", "ingest_url_env": "YOUTUBE_INGEST_URL"})

def test_validate_youtube_stream_rejects_non_positive_bitrate():
    spec = {"destination": "youtube", "ingest_url_env": "YOUTUBE_INGEST_URL",
            "key_env": "YOUTUBE_STREAM_KEY", "bitrate_kbps": 0}
    with pytest.raises(ConfigError):
        validate_youtube_stream(spec)

def test_validate_youtube_stream_accepts_a_complete_spec():
    spec = {"destination": "youtube", "ingest_url_env": "YOUTUBE_INGEST_URL",
            "key_env": "YOUTUBE_STREAM_KEY", "bitrate_kbps": 3000}
    validate_youtube_stream(spec)  # must not raise

def test_youtube_stream_missing_ingest_url_env_rejected_by_load_config(tmp_path):
    bad = VALID_YAML.replace('  ingest_url_env: "YOUTUBE_INGEST_URL"\n', "")
    with pytest.raises(ConfigError):
        _load(tmp_path, bad)
