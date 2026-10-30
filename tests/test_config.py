from datetime import datetime, timedelta, timezone
import pytest
from pathlib import Path
from brewcam.config.validation import (
    ConfigError,
    validate_background_mode,
    validate_step_sequence,
    validate_timezone_offset,
)

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

def test_known_mode_without_path_passes():
    validate_background_mode({"mode": "black"})

def test_unknown_mode_rejected():
    with pytest.raises(ConfigError):
        validate_background_mode({"mode": "bogus"})

def test_image_mode_missing_file_rejected(tmp_path):
    with pytest.raises(ConfigError):
        validate_background_mode({"mode": "image", "path": "nope.png"}, assets_dir=tmp_path)

def test_image_mode_existing_file_passes(tmp_path):
    (tmp_path / "pic.png").write_bytes(b"\x89PNG")
    validate_background_mode({"mode": "image", "path": "pic.png"}, assets_dir=tmp_path)
