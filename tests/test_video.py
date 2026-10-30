import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from brewcam.config import SessionConfig, Step
from brewcam.schedule import StageState
from brewcam.video.camera import CameraSourceError, build_snapshot_command, redact_url, resolve_rtsp_url
from brewcam.video.starter import start_camera_attempt, start_filler
from brewcam.video.state import UNAVAILABLE_RETRY_SECONDS
from brewcam.video.video import VideoState, tick

NOW = datetime(2026, 11, 7, 9, 0, 0, tzinfo=timezone.utc)
CAMERA_CONFIG = {"source_env": "CAMERA_SOURCE"}
STREAM = {"width": 1280, "height": 720, "fps": 25}
SESSION = SessionConfig(name="T", timezone="UTC", background={"mode": "camera"},
                         before={"mode": "black"}, after={"mode": "black"})
ACTIVE_NO_OVERRIDE = Step(name="s", description="d", start=None, end=None,
                           target_temperature_c=None, background=None)
SCHEDULE_ACTIVE = StageState(stage=ACTIVE_NO_OVERRIDE, status="active", stage_progress_pct=0, overall_progress_pct=0)


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


def test_redact_url_does_not_raise_on_a_malformed_url():
    # Found in review: urlsplit raises ValueError on a malformed URL (e.g. a bad
    # IPv6 host). redact_url must degrade instead of propagating — a malformed
    # CAMERA_SOURCE must never crash the caller that's trying to log it safely.
    result = redact_url("rtsp://wb:super-secret@[bad:8554/cam1")
    assert "super-secret" not in result


def test_build_snapshot_command_reads_rtsp_over_tcp_without_audio():
    cmd = build_snapshot_command("rtsp://wb:secret@wyze-bridge:8554/cam1", Path("/tmp/snap.jpg"))
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-rtsp_transport") + 1] == "tcp"
    assert "-an" in cmd
    assert cmd[cmd.index("-i") + 1] == "rtsp://wb:secret@wyze-bridge:8554/cam1"
    assert cmd[-1] == "/tmp/snap.jpg"


def test_build_snapshot_command_sets_a_read_timeout():
    # -rw_timeout does not apply to the RTSP demuxer (verified empirically against
    # ffmpeg 9.x against a socket that accepts and never replies: it never fires).
    # -timeout is the flag the RTSP demuxer actually honors.
    cmd = build_snapshot_command("rtsp://wyze-bridge:8554/cam1", Path("/tmp/snap.jpg"))
    assert "-timeout" in cmd
    assert "-rw_timeout" not in cmd


def test_tick_starts_ffmpeg_when_camera_source_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    state = VideoState()
    snapshot_path = tmp_path / "snap.jpg"
    with patch("brewcam.video.video.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None))
        status = tick(state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
                       snapshot_path, NOW, NOW, str(tmp_path / "logs"), 30)
    mock_popen.assert_called_once()
    assert state.status == "connecting"
    assert status["ffmpeg_running"] is True
    assert status["background_mode"] == "black"  # not connected yet — no frame seen


def test_tick_reports_unavailable_without_starting_process_when_env_var_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("CAMERA_SOURCE", raising=False)
    state = VideoState()
    snapshot_path = tmp_path / "snap.jpg"
    with patch("brewcam.video.video.subprocess.Popen") as mock_popen:
        status = tick(state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
                       snapshot_path, NOW, NOW, str(tmp_path / "logs"), 30)
    mock_popen.assert_not_called()
    assert state.status == "unavailable"
    assert status == {"background_mode": "black", "ffmpeg_running": False}


def test_tick_never_raises_when_camera_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("CAMERA_SOURCE", raising=False)
    state = VideoState()
    tick(state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt", tmp_path / "snap.jpg",
         NOW, NOW, str(tmp_path / "logs"), 30)  # must not raise


def _running_process():
    return MagicMock(poll=MagicMock(return_value=None), terminate=MagicMock(), kill=MagicMock(), wait=MagicMock())


def test_tick_transitions_to_connected_once_snapshot_file_updated(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    snapshot_path = tmp_path / "snap.jpg"
    state = VideoState(process=_running_process(), status="connecting", process_started_at=NOW,
                        running_source={"mode": "camera"})
    snapshot_path.write_bytes(b"jpg")
    os.utime(snapshot_path, (NOW.timestamp() + 1, NOW.timestamp() + 1))
    status = tick(
        state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt", snapshot_path,
        NOW + timedelta(seconds=2), NOW + timedelta(seconds=2), str(tmp_path / "logs"), 30,
    )
    assert state.status == "connected"
    assert status == {"background_mode": "camera", "ffmpeg_running": True}


def test_tick_ignores_a_snapshot_left_over_from_a_previous_run(tmp_path, monkeypatch):
    # snapshot predates this attempt's process start — must not count as a fresh frame
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    snapshot_path = tmp_path / "snap.jpg"
    snapshot_path.write_bytes(b"old")
    os.utime(snapshot_path, (NOW.timestamp() - 100, NOW.timestamp() - 100))
    state = VideoState(process=_running_process(), status="connecting", process_started_at=NOW,
                        running_source={"mode": "camera"})
    status = tick(
        state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt", snapshot_path,
        NOW + timedelta(seconds=2), NOW + timedelta(seconds=2), str(tmp_path / "logs"), 30,
    )
    assert state.status == "connecting"
    assert status["background_mode"] == "black"


def test_tick_detects_stall_and_schedules_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    snapshot_path = tmp_path / "snap.jpg"
    snapshot_path.write_bytes(b"jpg")
    os.utime(snapshot_path, (NOW.timestamp(), NOW.timestamp()))
    process = _running_process()
    state = VideoState(process=process, status="connected", process_started_at=NOW, connected_since=NOW,
                        running_source={"mode": "camera"})
    later = NOW + timedelta(seconds=20)  # past the 15s stall timeout
    # Task 4: a stalled camera is torn down AND a black filler is started the same
    # tick, so the preview never goes stale for even one tick (see tick()'s
    # "state.process is None" fallback branch) — the filler's Popen call must be
    # mocked here too, since it now fires as part of this tick.
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None))
        tick(state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt", snapshot_path,
             later, later, str(tmp_path / "logs"), 30)
    process.terminate.assert_called_once()
    assert state.status == "stalled"
    assert state.next_restart_at is not None and state.next_restart_at > later
    # process is no longer None: the immediate black-filler fallback replaced it
    assert state.process is not None
    assert state.running_source == {"mode": "black"}


def test_tick_times_out_a_connecting_process_that_never_produces_a_frame(tmp_path, monkeypatch):
    # Reproduces a real failure found in review: a dead RTSP handshake (camera
    # reachable but silent) left the process "connecting" forever with no restart,
    # because the old stall check only ran once status == "connected".
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    process = _running_process()
    state = VideoState(process=process, status="connecting", process_started_at=NOW,
                        running_source={"mode": "camera"})
    later = NOW + timedelta(seconds=20)  # past the 15s stall timeout, still no snapshot file
    # Task 4: the immediate black-filler fallback fires here too (see above) —
    # its Popen call must be mocked.
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None))
        tick(state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
             tmp_path / "snap.jpg", later, later, str(tmp_path / "logs"), 30)
    process.terminate.assert_called_once()
    assert state.status != "connecting"
    assert state.next_restart_at is not None and state.next_restart_at > later
    assert state.process is not None
    assert state.running_source == {"mode": "black"}


def test_backoff_grows_and_caps_at_sixty_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    state = VideoState(failure_count=0)
    gaps = []
    with patch("brewcam.video.video.subprocess.Popen", side_effect=OSError("boom")):
        current_now = NOW
        for _ in range(6):
            tick(state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
                 tmp_path / "snap.jpg", current_now, current_now, str(tmp_path / "logs"), 30)
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
        failure_count=3, running_source={"mode": "camera"},
    )
    later = NOW + timedelta(seconds=61)
    os.utime(snapshot_path, (later.timestamp(), later.timestamp()))
    tick(state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt", snapshot_path,
         later, later, str(tmp_path / "logs"), 30)
    assert state.failure_count == 0


def test_popen_failure_never_leaks_the_raw_url_into_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wb:super-secret@wyze-bridge:8554/cam1")
    state = VideoState()
    log_dir = tmp_path / "logs"
    with patch(
        "brewcam.video.video.subprocess.Popen",
        side_effect=OSError("rtsp://wb:super-secret@wyze-bridge:8554/cam1 unreachable"),
    ):
        tick(state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt", tmp_path / "snap.jpg",
             NOW, NOW, str(log_dir), 30)
    logged = "\n".join(p.read_text() for p in log_dir.glob("*.jsonl"))
    assert "super-secret" not in logged


def test_tick_logs_an_event_when_a_just_started_process_exits_immediately(tmp_path, monkeypatch):
    # Reproduces a real failure found in review: a crash-looping camera (e.g. a
    # typo'd stream path) started ffmpeg fine, but it exited almost immediately.
    # That transition previously logged nothing at all, making a persistent
    # misconfiguration indistinguishable from "still connecting".
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    exited_process = MagicMock(
        poll=MagicMock(return_value=1), terminate=MagicMock(), kill=MagicMock(), wait=MagicMock()
    )
    state = VideoState(process=exited_process, status="connecting", process_started_at=NOW,
                        running_source={"mode": "camera"})
    log_dir = tmp_path / "logs"
    # Task 4: the immediate black-filler fallback fires once this process is
    # detected as exited — its Popen call must be mocked.
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None))
        tick(
            state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt", tmp_path / "snap.jpg",
            NOW + timedelta(seconds=1), NOW + timedelta(seconds=1), str(log_dir), 30,
        )
    event_types = [
        json.loads(line)["event_type"]
        for p in log_dir.glob("*.jsonl")
        for line in p.read_text().splitlines()
    ]
    assert "camera_reconnecting" in event_types


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


def test_tick_survives_the_snapshot_file_disappearing_mid_check(tmp_path, monkeypatch):
    # Found in review: watch_running_process calls snapshot_path.stat() without
    # catching OSError. If the file vanishes between .exists() and .stat() (e.g.
    # deleted during manual UAT), the real pathlib.Path raises FileNotFoundError,
    # which is an OSError subclass but was not being caught anywhere in this
    # path — violating "a camera outage must never stop the main loop".
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    snapshot_path = tmp_path / "snap.jpg"
    snapshot_path.write_bytes(b"jpg")
    real_stat = Path.stat

    def _flaky_stat(self, *args, **kwargs):
        if self == snapshot_path:
            raise FileNotFoundError("deleted mid-check")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _flaky_stat)
    process = _running_process()
    state = VideoState(process=process, status="connecting", process_started_at=NOW,
                        running_source={"mode": "camera"})
    # must not raise
    tick(state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt", snapshot_path,
         NOW + timedelta(seconds=1), NOW + timedelta(seconds=1), str(tmp_path / "logs"), 30)


# --- start_camera_attempt / start_filler (Task 4, Step 2) ---


def test_start_camera_attempt_starts_process_and_sets_running_source(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    state = VideoState()
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None))
        start_camera_attempt(state, CAMERA_CONFIG, STREAM, tmp_path / "overlay.txt", tmp_path / "snap.jpg",
                              NOW, NOW, str(tmp_path / "logs"), 30)
    mock_popen.assert_called_once()
    assert state.running_source == {"mode": "camera"}
    assert state.status == "connecting"


def test_start_camera_attempt_degrades_when_env_var_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("CAMERA_SOURCE", raising=False)
    state = VideoState()
    start_camera_attempt(state, CAMERA_CONFIG, STREAM, tmp_path / "overlay.txt", tmp_path / "snap.jpg",
                          NOW, NOW, str(tmp_path / "logs"), 30)
    assert state.process is None
    assert state.running_source is None
    assert state.status == "unavailable"


def test_start_filler_starts_process_and_sets_running_source(tmp_path):
    state = VideoState()
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None))
        start_filler(state, {"mode": "black"}, STREAM, tmp_path / "overlay.txt", tmp_path / "snap.jpg",
                      NOW, str(tmp_path / "logs"), 30)
    mock_popen.assert_called_once()
    assert state.running_source == {"mode": "black"}


# --- video.tick orchestration (Task 4, Step 6 — Review Focus scenarios) ---


def _tick(state, now, tmp_path, monkeypatch=None, camera_source="rtsp://wyze-bridge:8554/cam1"):
    if monkeypatch is not None:
        monkeypatch.setenv("CAMERA_SOURCE", camera_source)
    return tick(state, {"source_env": "CAMERA_SOURCE"}, STREAM, SESSION, SCHEDULE_ACTIVE,
                tmp_path / "overlay.txt", tmp_path / "snap.jpg", now, now, str(tmp_path / "logs"), 30)


def test_camera_falling_back_to_black_is_retried_on_the_backoff_schedule_not_abandoned(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    state = VideoState()
    with patch("brewcam.video.starter.subprocess.Popen", side_effect=OSError("boom")):
        status = _tick(state, NOW, tmp_path)
    assert state.running_source is None  # camera attempt failed to even start
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None))
        status = _tick(state, NOW + timedelta(seconds=1), tmp_path)  # before backoff elapses: filler only
    assert status["background_mode"] == "black"
    assert state.running_source == {"mode": "black"}
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None))
        status = _tick(state, state.next_restart_at, tmp_path)  # backoff elapsed: camera retried
    assert state.running_source == {"mode": "camera"}


def test_connecting_camera_is_not_torn_down_before_the_stall_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    state = VideoState()
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None))
        _tick(state, NOW, tmp_path)
    assert state.status == "connecting"
    process_from_first_tick = state.process
    _tick(state, NOW + timedelta(seconds=2), tmp_path)  # well within the 15s stall timeout
    assert state.process is process_from_first_tick  # not restarted mid-attempt


def test_step_boundary_away_from_camera_switches_immediately_even_while_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    state = VideoState(process=MagicMock(poll=MagicMock(return_value=None)), status="connected",
                        running_source={"mode": "camera"}, process_started_at=NOW, connected_since=NOW)
    image_schedule = StageState(stage=Step(name="s2", description="d", start=None, end=None,
                                            target_temperature_c=None, background={"mode": "image", "path": "assets/standby.png"}),
                                 status="active", stage_progress_pct=0, overall_progress_pct=0)
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None))
        status = tick(state, {"source_env": "CAMERA_SOURCE"}, STREAM, SESSION, image_schedule,
                       tmp_path / "overlay.txt", tmp_path / "snap.jpg", NOW + timedelta(seconds=5),
                       NOW + timedelta(seconds=5), str(tmp_path / "logs"), 30)
    assert state.running_source == {"mode": "image", "path": "assets/standby.png"}
    assert status["background_mode"] == "image"


def test_filler_process_dying_is_noticed_and_restarted_the_same_tick(tmp_path):
    # Review Focus: a filler (black/image) process can die on its own between
    # ticks (unlike the camera path, it has no watch_running_process health
    # check) — tick()'s elif clause `state.process.poll() is not None` must
    # catch this and restart it, same as a step-boundary mode change would.
    black_session = SessionConfig(name="T", timezone="UTC", background={"mode": "black"},
                                   before={"mode": "black"}, after={"mode": "black"})
    exited_filler = MagicMock(poll=MagicMock(return_value=1))
    state = VideoState(process=exited_filler, running_source={"mode": "black"})
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None))
        status = tick(state, CAMERA_CONFIG, STREAM, black_session, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
                       tmp_path / "snap.jpg", NOW, NOW, str(tmp_path / "logs"), 30)
    mock_popen.assert_called_once()
    assert state.process is not None
    assert state.process is not exited_filler
    assert state.running_source == {"mode": "black"}
    assert status["ffmpeg_running"] is True
