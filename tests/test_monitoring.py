import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from brewcam.utils.jsonl import daily_log_path, append_jsonl, enforce_retention
from brewcam.config.monitoring import (
    build_snapshot_record,
    build_event_record,
    write_snapshot,
    write_event,
    SCHEMA_VERSION,
)
from brewcam.overlay import OverlayData

NOW = datetime(2026, 11, 7, 9, 20, tzinfo=timezone.utc)
OVERLAY = OverlayData("Test Brew", "Mash", "desc", "active", 10.0, 5.0, "20.0C")


def test_daily_log_path_names_file_by_date(tmp_path):
    path = daily_log_path(str(tmp_path), date(2026, 11, 7))
    assert path == tmp_path / "2026-11-07.jsonl"


def test_append_jsonl_writes_one_line_per_call(tmp_path):
    append_jsonl(str(tmp_path), date(2026, 11, 7), {"a": 1})
    append_jsonl(str(tmp_path), date(2026, 11, 7), {"a": 2})
    lines = (tmp_path / "2026-11-07.jsonl").read_text().splitlines()
    assert [json.loads(l) for l in lines] == [{"a": 1}, {"a": 2}]


def test_rollover_uses_local_date_not_utc_date(tmp_path):
    # 2026-11-07 23:30 America/Toronto (-05:00) is 2026-11-08 04:30 UTC.
    # The log file must be named by the LOCAL date, 2026-11-07.
    local_moment = datetime(2026, 11, 7, 23, 30, tzinfo=ZoneInfo("America/Toronto"))
    append_jsonl(str(tmp_path), local_moment.date(), {"a": 1})
    assert (tmp_path / "2026-11-07.jsonl").exists()
    assert not (tmp_path / "2026-11-08.jsonl").exists()


def test_retention_deletes_files_older_than_window(tmp_path):
    (tmp_path / "2026-10-01.jsonl").write_text("")
    (tmp_path / "2026-11-05.jsonl").write_text("")
    (tmp_path / "2026-11-07.jsonl").write_text("")
    enforce_retention(str(tmp_path), today=date(2026, 11, 7), retention_days=30)
    remaining = {p.name for p in tmp_path.iterdir()}
    assert remaining == {"2026-11-05.jsonl", "2026-11-07.jsonl"}


def test_build_snapshot_record_shape():
    record = build_snapshot_record(NOW, OVERLAY, {"background_mode": "black", "ffmpeg_running": False})
    assert record["event_type"] == "snapshot"
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["session"]["stage"] == "Mash"
    assert record["session"]["progress_pct"] == 10.0
    assert record["video"] == {"background": "black", "ffmpeg_running": False}


def test_snapshot_record_ignores_unexpected_fields_that_could_carry_secrets():
    leaky_status = {"background_mode": "black", "ffmpeg_running": False, "stream_key": "super-secret"}
    record = build_snapshot_record(NOW, OVERLAY, leaky_status)
    assert "super-secret" not in str(record)
    assert "stream_key" not in record["video"]


def test_build_event_record_shape():
    record = build_event_record(NOW, "shutdown", {"reason": "sigint"})
    assert record["event_type"] == "shutdown"
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["details"] == {"reason": "sigint"}


def test_write_snapshot_appends_to_todays_file(tmp_path):
    write_snapshot(str(tmp_path), NOW, OVERLAY, {"background_mode": "black", "ffmpeg_running": False}, retention_days=30)
    lines = (tmp_path / "2026-11-07.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["event_type"] == "snapshot"


def test_write_event_appends_immediately(tmp_path):
    write_event(str(tmp_path), NOW, "error", {"message": "sensor timeout"}, retention_days=30)
    lines = (tmp_path / "2026-11-07.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["event_type"] == "error"
