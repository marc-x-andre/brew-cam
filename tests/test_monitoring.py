import json
from datetime import date, datetime
from zoneinfo import ZoneInfo
from brewcam.utils.jsonl import daily_log_path, append_jsonl, enforce_retention


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
