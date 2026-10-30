import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from brewcam.video.camera import CameraSourceError, redact_url, resolve_rtsp_url
from brewcam.video.video import VideoState, build_snapshot_command, tick

NOW = datetime(2026, 11, 7, 9, 0, 0, tzinfo=timezone.utc)
CAMERA_CONFIG = {"source_env": "CAMERA_SOURCE"}


def test_resolve_rtsp_url_reads_named_env_var(monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wb:secret@wyze-bridge:8554/cam1")
    url = resolve_rtsp_url({"source_env": "CAMERA_SOURCE"})
    assert url == "rtsp://wb:secret@wyze-bridge:8554/cam1"


def test_resolve_rtsp_url_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("CAMERA_SOURCE", raising=False)
    with pytest.raises(CameraSourceError):
        resolve_rtsp_url({"source_env": "CAMERA_SOURCE"})


def test_resolve_rtsp_url_empty_env_var_raises(monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "")
    with pytest.raises(CameraSourceError):
        resolve_rtsp_url({"source_env": "CAMERA_SOURCE"})


def test_redact_url_masks_userinfo():
    redacted = redact_url("rtsp://wb:super-secret@wyze-bridge:8554/cam1")
    assert "super-secret" not in redacted
    assert redacted == "rtsp://***:***@wyze-bridge:8554/cam1"


def test_redact_url_masks_secret_query_params():
    redacted = redact_url("http://host/path?api_key=super-secret&other=1")
    assert "super-secret" not in redacted
    assert "other=1" in redacted


def test_redact_url_leaves_plain_url_unchanged():
    assert redact_url("rtsp://wyze-bridge:8554/cam1") == "rtsp://wyze-bridge:8554/cam1"


def test_build_snapshot_command_reads_rtsp_over_tcp_without_audio():
    cmd = build_snapshot_command("rtsp://wb:secret@wyze-bridge:8554/cam1", Path("/tmp/snap.jpg"))
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-rtsp_transport") + 1] == "tcp"
    assert "-an" in cmd
    assert cmd[cmd.index("-i") + 1] == "rtsp://wb:secret@wyze-bridge:8554/cam1"
    assert cmd[-1] == "/tmp/snap.jpg"


def test_build_snapshot_command_sets_a_read_timeout():
    cmd = build_snapshot_command("rtsp://wyze-bridge:8554/cam1", Path("/tmp/snap.jpg"))
    assert "-rw_timeout" in cmd


def test_tick_starts_ffmpeg_when_camera_source_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    state = VideoState()
    snapshot_path = tmp_path / "snap.jpg"
    with patch("brewcam.video.video.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None))
        status = tick(state, CAMERA_CONFIG, snapshot_path, NOW, NOW, str(tmp_path / "logs"), 30)
    mock_popen.assert_called_once()
    assert state.status == "connecting"
    assert status["ffmpeg_running"] is True
    assert status["background_mode"] == "black"  # not connected yet — no frame seen


def test_tick_reports_unavailable_without_starting_process_when_env_var_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("CAMERA_SOURCE", raising=False)
    state = VideoState()
    snapshot_path = tmp_path / "snap.jpg"
    with patch("brewcam.video.video.subprocess.Popen") as mock_popen:
        status = tick(state, CAMERA_CONFIG, snapshot_path, NOW, NOW, str(tmp_path / "logs"), 30)
    mock_popen.assert_not_called()
    assert state.status == "unavailable"
    assert status == {"background_mode": "black", "ffmpeg_running": False}


def test_tick_never_raises_when_camera_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("CAMERA_SOURCE", raising=False)
    state = VideoState()
    tick(state, CAMERA_CONFIG, tmp_path / "snap.jpg", NOW, NOW, str(tmp_path / "logs"), 30)  # must not raise


def _running_process():
    return MagicMock(poll=MagicMock(return_value=None), terminate=MagicMock(), kill=MagicMock(), wait=MagicMock())


def test_tick_transitions_to_connected_once_snapshot_file_updated(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    snapshot_path = tmp_path / "snap.jpg"
    state = VideoState(process=_running_process(), status="connecting", process_started_at=NOW)
    snapshot_path.write_bytes(b"jpg")
    os.utime(snapshot_path, (NOW.timestamp() + 1, NOW.timestamp() + 1))
    status = tick(
        state, CAMERA_CONFIG, snapshot_path, NOW + timedelta(seconds=2), NOW + timedelta(seconds=2),
        str(tmp_path / "logs"), 30,
    )
    assert state.status == "connected"
    assert status == {"background_mode": "camera", "ffmpeg_running": True}


def test_tick_ignores_a_snapshot_left_over_from_a_previous_run(tmp_path, monkeypatch):
    # snapshot predates this attempt's process start — must not count as a fresh frame
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    snapshot_path = tmp_path / "snap.jpg"
    snapshot_path.write_bytes(b"old")
    os.utime(snapshot_path, (NOW.timestamp() - 100, NOW.timestamp() - 100))
    state = VideoState(process=_running_process(), status="connecting", process_started_at=NOW)
    status = tick(
        state, CAMERA_CONFIG, snapshot_path, NOW + timedelta(seconds=2), NOW + timedelta(seconds=2),
        str(tmp_path / "logs"), 30,
    )
    assert state.status == "connecting"
    assert status["background_mode"] == "black"


def test_tick_detects_stall_and_schedules_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    snapshot_path = tmp_path / "snap.jpg"
    snapshot_path.write_bytes(b"jpg")
    os.utime(snapshot_path, (NOW.timestamp(), NOW.timestamp()))
    process = _running_process()
    state = VideoState(process=process, status="connected", process_started_at=NOW, connected_since=NOW)
    later = NOW + timedelta(seconds=20)  # past the 15s stall timeout
    tick(state, CAMERA_CONFIG, snapshot_path, later, later, str(tmp_path / "logs"), 30)
    process.terminate.assert_called_once()
    assert state.status == "stalled"
    assert state.process is None
    assert state.next_restart_at is not None and state.next_restart_at > later


def test_backoff_grows_and_caps_at_sixty_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    state = VideoState(failure_count=0)
    gaps = []
    with patch("brewcam.video.video.subprocess.Popen", side_effect=OSError("boom")):
        current_now = NOW
        for _ in range(6):
            tick(state, CAMERA_CONFIG, tmp_path / "snap.jpg", current_now, current_now, str(tmp_path / "logs"), 30)
            gaps.append((state.next_restart_at - current_now).total_seconds())
            current_now = state.next_restart_at + timedelta(seconds=1)  # jump past this backoff window
    assert gaps == sorted(gaps)  # non-decreasing
    assert max(gaps) <= 60


def test_healthy_connection_resets_failure_count(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    snapshot_path = tmp_path / "snap.jpg"
    snapshot_path.write_bytes(b"jpg")
    os.utime(snapshot_path, (NOW.timestamp(), NOW.timestamp()))
    state = VideoState(
        process=_running_process(), status="connected", process_started_at=NOW, connected_since=NOW,
        failure_count=3,
    )
    later = NOW + timedelta(seconds=61)
    os.utime(snapshot_path, (later.timestamp(), later.timestamp()))
    tick(state, CAMERA_CONFIG, snapshot_path, later, later, str(tmp_path / "logs"), 30)
    assert state.failure_count == 0


def test_popen_failure_never_leaks_the_raw_url_into_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wb:super-secret@wyze-bridge:8554/cam1")
    state = VideoState()
    log_dir = tmp_path / "logs"
    with patch(
        "brewcam.video.video.subprocess.Popen",
        side_effect=OSError("rtsp://wb:super-secret@wyze-bridge:8554/cam1 unreachable"),
    ):
        tick(state, CAMERA_CONFIG, tmp_path / "snap.jpg", NOW, NOW, str(log_dir), 30)
    logged = "\n".join(p.read_text() for p in log_dir.glob("*.jsonl"))
    assert "super-secret" not in logged


def test_stop_is_a_safe_no_op_with_no_process():
    from brewcam.video.video import stop

    stop(VideoState())  # must not raise


def test_stop_terminates_a_running_process():
    from brewcam.video.video import stop

    process = _running_process()
    state = VideoState(process=process)
    stop(state)
    process.terminate.assert_called_once()
    assert state.process is None
