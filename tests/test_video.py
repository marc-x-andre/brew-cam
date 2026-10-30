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
from brewcam.video.supervisor import watch_running_process
from brewcam.video.video import VideoState, tick

NOW = datetime(2026, 11, 7, 9, 0, 0, tzinfo=timezone.utc)
CAMERA_CONFIG = {"source_env": "CAMERA_SOURCE"}
STREAM = {"width": 1280, "height": 720, "fps": 25}
SESSION = SessionConfig(name="T", timezone="UTC", background={"mode": "camera"},
                         before={"mode": "black"}, after={"mode": "black"})
ACTIVE_NO_OVERRIDE = Step(name="s", description="d", start=None, end=None,
                           target_temperature_c=None, background=None)
SCHEDULE_ACTIVE = StageState(stage=ACTIVE_NO_OVERRIDE, status="active", stage_progress_pct=0, overall_progress_pct=0)
IMAGE_SESSION = SessionConfig(name="T", timezone="UTC", background={"mode": "image", "path": "assets/standby.png"},
                               before={"mode": "black"}, after={"mode": "black"})
STREAM_YOUTUBE = {**STREAM, "destination": "youtube", "ingest_url_env": "YOUTUBE_INGEST_URL",
                  "key_env": "YOUTUBE_STREAM_KEY"}


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
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
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
    assert status == {"background_mode": "black", "ffmpeg_running": False,
                       "youtube": {"configured": False, "leg_active": False}}


def test_tick_never_raises_when_camera_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("CAMERA_SOURCE", raising=False)
    state = VideoState()
    tick(state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt", tmp_path / "snap.jpg",
         NOW, NOW, str(tmp_path / "logs"), 30)  # must not raise


def _running_process():
    return MagicMock(poll=MagicMock(return_value=None), terminate=MagicMock(), kill=MagicMock(), wait=MagicMock(),
                      stderr=MagicMock(read=MagicMock(return_value=b"")))


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
    assert status == {"background_mode": "camera", "ffmpeg_running": True,
                       "youtube": {"configured": False, "leg_active": False}}


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
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
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
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
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


def test_camera_death_and_filler_start_failure_in_the_same_tick_does_not_crash_loop(tmp_path, monkeypatch):
    # Regression (review finding): if the camera dies this tick AND the same-tick
    # fallback start_filler call also fails to start (e.g. ffmpeg missing), both
    # process-teardown paths must clear running_source. Otherwise running_source
    # stays {"mode": "camera"} with process=None, and the NEXT tick's first
    # branch (running_source == camera and desired == camera) calls
    # watch_running_process on a None process, raising AttributeError on every
    # subsequent tick — an unrecoverable crash loop.
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    exited_process = MagicMock(poll=MagicMock(return_value=1), stderr=MagicMock(read=MagicMock(return_value=b"")))
    state = VideoState(process=exited_process, status="connected", process_started_at=NOW,
                        connected_since=NOW, running_source={"mode": "camera"})
    with patch("brewcam.video.starter.subprocess.Popen", side_effect=OSError("ffmpeg missing")):
        tick(state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
             tmp_path / "snap.jpg", NOW + timedelta(seconds=1), NOW + timedelta(seconds=1),
             str(tmp_path / "logs"), 30)
    assert state.process is None
    assert state.running_source is None  # must not be stuck at {"mode": "camera"} with no process
    # the next tick must not raise, even with the camera still unresolved
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
        tick(state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
             tmp_path / "snap.jpg", NOW + timedelta(seconds=2), NOW + timedelta(seconds=2),
             str(tmp_path / "logs"), 30)  # must not raise AttributeError


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
        poll=MagicMock(return_value=1), terminate=MagicMock(), kill=MagicMock(), wait=MagicMock(),
        stderr=MagicMock(read=MagicMock(return_value=b"")),
    )
    state = VideoState(process=exited_process, status="connecting", process_started_at=NOW,
                        running_source={"mode": "camera"})
    log_dir = tmp_path / "logs"
    # Task 4: the immediate black-filler fallback fires once this process is
    # detected as exited — its Popen call must be mocked.
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
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


def test_stderr_excerpt_never_leaks_a_raw_secret_into_the_log(tmp_path, monkeypatch):
    # The task's own named risk: stderr capture was turned on (DEVNULL -> PIPE) so a
    # failure's excerpt could be logged for diagnosis, but ffmpeg's stderr can itself
    # contain the raw RTSP credentials or RTMPS stream key. redact_stderr must run
    # before the excerpt reaches write_event — this proves it end-to-end through tick(),
    # mirroring test_popen_failure_never_leaks_the_raw_url_into_logs but for the new
    # stderr-capture path rather than the Popen-failure path.
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    secret_stderr = b'Input #0, rtsp, from "rtsp://wb:wyze-secret@wyze-bridge:8554/cam1":'
    exited_process = MagicMock(
        poll=MagicMock(return_value=1), terminate=MagicMock(), kill=MagicMock(), wait=MagicMock(),
        stderr=MagicMock(read=MagicMock(return_value=secret_stderr)),
    )
    state = VideoState(process=exited_process, status="connecting", process_started_at=NOW,
                        running_source={"mode": "camera"})
    log_dir = tmp_path / "logs"
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None),
                                             stderr=MagicMock(read=MagicMock(return_value=b"")))
        tick(
            state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt", tmp_path / "snap.jpg",
            NOW + timedelta(seconds=1), NOW + timedelta(seconds=1), str(log_dir), 30,
        )
    logged = "\n".join(p.read_text() for p in log_dir.glob("*.jsonl"))
    assert "wyze-secret" not in logged
    assert '"excerpt"' in logged


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


def test_stop_logs_streaming_stopped_when_a_youtube_leg_was_active(tmp_path):
    from brewcam.video.video import stop

    process = MagicMock(poll=MagicMock(return_value=None), terminate=MagicMock(), wait=MagicMock())
    state = VideoState(process=process, running_source={"mode": "camera"})
    stop(state, youtube_leg_active=True, monitoring_dir=str(tmp_path / "logs"), now_local=NOW, retention_days=30)
    logged = "\n".join(p.read_text() for p in (tmp_path / "logs").glob("*.jsonl"))
    assert '"streaming_stopped"' in logged


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


# --- YouTube target resolution (Task 3) ---


def test_tick_reports_youtube_not_configured_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("CAMERA_SOURCE", raising=False)
    state = VideoState()
    status = tick(state, CAMERA_CONFIG, STREAM, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
                   tmp_path / "snap.jpg", NOW, NOW, str(tmp_path / "logs"), 30)
    assert status["youtube"] == {"configured": False, "leg_active": False}


def test_tick_degrades_to_jpeg_only_when_youtube_env_vars_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    monkeypatch.delenv("YOUTUBE_INGEST_URL", raising=False)
    monkeypatch.delenv("YOUTUBE_STREAM_KEY", raising=False)
    state = VideoState()
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock())
        status = tick(state, CAMERA_CONFIG, STREAM_YOUTUBE, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
                       tmp_path / "snap.jpg", NOW, NOW, str(tmp_path / "logs"), 30)
    assert status["youtube"] == {"configured": False, "leg_active": False}
    assert status["ffmpeg_running"] is True  # camera/filler still runs — YouTube failure never blocks local output
    logged = "\n".join(p.read_text() for p in (tmp_path / "logs").glob("*.jsonl"))
    assert '"youtube_unavailable"' in logged


def test_tick_reports_youtube_leg_active_once_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    monkeypatch.setenv("YOUTUBE_INGEST_URL", "rtmps://a.rtmps.youtube.com/live2")
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "k")
    state = VideoState()
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock())
        status = tick(state, CAMERA_CONFIG, STREAM_YOUTUBE, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
                       tmp_path / "snap.jpg", NOW, NOW, str(tmp_path / "logs"), 30)
    assert status["youtube"] == {"configured": True, "leg_active": True}


def test_process_exit_with_youtube_leg_active_logs_streaming_failed_not_camera_disconnected(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    monkeypatch.setenv("YOUTUBE_INGEST_URL", "rtmps://a.rtmps.youtube.com/live2")
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "k")
    state = VideoState(process=MagicMock(poll=MagicMock(return_value=1), stderr=MagicMock(read=MagicMock(return_value=b""))),
                        status="connected", running_source={"mode": "camera"}, process_started_at=NOW, connected_since=NOW)
    tick(state, CAMERA_CONFIG, STREAM_YOUTUBE, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
         tmp_path / "snap.jpg", NOW + timedelta(seconds=1), NOW + timedelta(seconds=1), str(tmp_path / "logs"), 30)
    logged = "\n".join(p.read_text() for p in (tmp_path / "logs").glob("*.jsonl"))
    assert '"streaming_failed"' in logged
    assert '"camera_disconnected"' not in logged


def test_first_ever_connection_with_youtube_leg_logs_streaming_started_not_recovered(tmp_path, monkeypatch):
    # A brand-new run's very first connection (failure_count == 0, nothing has ever
    # failed) must be reported as streaming_started, not streaming_recovered —
    # "recovered" implies a prior tracked failure that never happened here.
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    monkeypatch.setenv("YOUTUBE_INGEST_URL", "rtmps://a.rtmps.youtube.com/live2")
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "k")
    snapshot_path = tmp_path / "snap.jpg"
    process = _running_process()
    state = VideoState(process=process, status="connecting", process_started_at=NOW,
                        running_source={"mode": "camera"})
    assert state.failure_count == 0
    snapshot_path.write_bytes(b"jpg")
    os.utime(snapshot_path, (NOW.timestamp() + 1, NOW.timestamp() + 1))
    tick(state, CAMERA_CONFIG, STREAM_YOUTUBE, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt", snapshot_path,
         NOW + timedelta(seconds=2), NOW + timedelta(seconds=2), str(tmp_path / "logs"), 30)
    assert state.status == "connected"
    logged = "\n".join(p.read_text() for p in (tmp_path / "logs").glob("*.jsonl"))
    assert '"streaming_started"' in logged
    assert '"streaming_recovered"' not in logged


def test_streaming_failure_retry_and_recovery_sequence_with_youtube_leg(tmp_path, monkeypatch):
    # Regression for a review finding: streaming_recovered must only fire after a
    # genuinely tracked failure (failure_count > 0 at the moment of reconnection).
    # This drives a real failure -> retry -> recovery sequence through tick() and
    # pins streaming_retry's payload shape along the way.
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    monkeypatch.setenv("YOUTUBE_INGEST_URL", "rtmps://a.rtmps.youtube.com/live2")
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "k")
    snapshot_path = tmp_path / "snap.jpg"
    log_dir = tmp_path / "logs"
    exited_process = MagicMock(poll=MagicMock(return_value=1), stderr=MagicMock(read=MagicMock(return_value=b"")))
    state = VideoState(process=exited_process, status="connected", process_started_at=NOW, connected_since=NOW,
                        failure_count=0, running_source={"mode": "camera"})

    # Tick 1: the camera process has died -> streaming_failed + streaming_retry;
    # the same-tick black-filler fallback fires too, so its Popen must be mocked.
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None),
                                             stderr=MagicMock(read=MagicMock(return_value=b"")))
        tick(state, CAMERA_CONFIG, STREAM_YOUTUBE, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
             snapshot_path, NOW + timedelta(seconds=1), NOW + timedelta(seconds=1), str(log_dir), 30)
    logged_records = [
        json.loads(line) for p in log_dir.glob("*.jsonl") for line in p.read_text().splitlines()
    ]
    event_types = [r["event_type"] for r in logged_records]
    assert "streaming_failed" in event_types
    assert "streaming_retry" in event_types
    retry_details = next(r["details"] for r in logged_records if r["event_type"] == "streaming_retry")
    assert "delay_seconds" in retry_details
    assert state.failure_count == 1
    restart_at = state.next_restart_at

    # Tick 2 (backoff elapsed): camera is retried.
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None),
                                             stderr=MagicMock(read=MagicMock(return_value=b"")))
        tick(state, CAMERA_CONFIG, STREAM_YOUTUBE, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
             snapshot_path, restart_at, restart_at, str(log_dir), 30)
    assert state.running_source == {"mode": "camera"}

    # Tick 3: a fresh frame arrives -> the reconnection is a genuine recovery
    # (failure_count is still 1 at this point) -> streaming_recovered.
    snapshot_path.write_bytes(b"jpg")
    fresh_mtime = state.process_started_at.timestamp() + 1
    os.utime(snapshot_path, (fresh_mtime, fresh_mtime))
    even_later = restart_at + timedelta(seconds=2)
    tick(state, CAMERA_CONFIG, STREAM_YOUTUBE, SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
         snapshot_path, even_later, even_later, str(log_dir), 30)
    assert state.status == "connected"
    logged = "\n".join(p.read_text() for p in log_dir.glob("*.jsonl"))
    assert '"streaming_recovered"' in logged


# --- start_camera_attempt / start_filler (Task 4, Step 2) ---


def test_start_camera_attempt_starts_process_and_sets_running_source(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    state = VideoState()
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
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
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
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
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
        status = _tick(state, NOW + timedelta(seconds=1), tmp_path)  # before backoff elapses: filler only
    assert status["background_mode"] == "black"
    assert state.running_source == {"mode": "black"}
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
        status = _tick(state, state.next_restart_at, tmp_path)  # backoff elapsed: camera retried
    assert state.running_source == {"mode": "camera"}


def test_connecting_camera_is_not_torn_down_before_the_stall_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    state = VideoState()
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
        _tick(state, NOW, tmp_path)
    assert state.status == "connecting"
    process_from_first_tick = state.process
    _tick(state, NOW + timedelta(seconds=2), tmp_path)  # well within the 15s stall timeout
    assert state.process is process_from_first_tick  # not restarted mid-attempt


def test_step_boundary_away_from_camera_switches_immediately_even_while_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    state = VideoState(process=MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b""))), status="connected",
                        running_source={"mode": "camera"}, process_started_at=NOW, connected_since=NOW)
    image_schedule = StageState(stage=Step(name="s2", description="d", start=None, end=None,
                                            target_temperature_c=None, background={"mode": "image", "path": "assets/standby.png"}),
                                 status="active", stage_progress_pct=0, overall_progress_pct=0)
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
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
    exited_filler = MagicMock(poll=MagicMock(return_value=1), stderr=MagicMock(read=MagicMock(return_value=b"")))
    state = VideoState(process=exited_filler, running_source={"mode": "black"})
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
        status = tick(state, CAMERA_CONFIG, STREAM, black_session, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
                       tmp_path / "snap.jpg", NOW, NOW, str(tmp_path / "logs"), 30)
    mock_popen.assert_called_once()
    assert state.process is not None
    assert state.process is not exited_filler
    assert state.running_source == {"mode": "black"}
    assert status["ffmpeg_running"] is True


# --- Finding 1 (final review): broken non-camera background degrades to black ---


def test_broken_image_filler_degrades_to_black_instead_of_looping(tmp_path):
    # Review finding: a permanently broken image path (e.g. missing file) must
    # not respawn the identical broken ffmpeg command forever. Once the filler
    # process is observed to have died on its own while still configured as the
    # same broken image, tick() must give up on it and fall back to black.
    broken_image = {"mode": "image", "path": "assets/standby.png"}
    exited_filler = MagicMock(poll=MagicMock(return_value=1), stderr=MagicMock(read=MagicMock(return_value=b"")))
    state = VideoState(process=exited_filler, running_source=broken_image)
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
        status = tick(state, CAMERA_CONFIG, STREAM, IMAGE_SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
                       tmp_path / "snap.jpg", NOW, NOW, str(tmp_path / "logs"), 30)
    mock_popen.assert_called_once()
    assert state.degraded_mode == broken_image
    assert state.running_source == {"mode": "black"}
    assert status["background_mode"] == "black"

    # Next tick: the black filler is healthy now. Must not try to restart the
    # broken image — no oscillation, no respawn of the broken config.
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen_again:
        status2 = tick(state, CAMERA_CONFIG, STREAM, IMAGE_SESSION, SCHEDULE_ACTIVE, tmp_path / "overlay.txt",
                        tmp_path / "snap.jpg", NOW + timedelta(seconds=5), NOW + timedelta(seconds=5),
                        str(tmp_path / "logs"), 30)
    mock_popen_again.assert_not_called()
    assert state.running_source == {"mode": "black"}
    assert status2["background_mode"] == "black"


def test_configured_mode_change_escapes_the_degraded_black_state(tmp_path):
    # Once configured_mode changes to something other than the abandoned broken
    # config (a step boundary to a different image, or to black directly),
    # tick() must try the newly-configured thing fresh rather than staying
    # permanently stuck on black.
    broken_image = {"mode": "image", "path": "assets/standby.png"}
    state = VideoState(process=MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b""))),
                        running_source={"mode": "black"}, degraded_mode=broken_image)
    new_image_schedule = StageState(
        stage=Step(name="s2", description="d", start=None, end=None, target_temperature_c=None,
                   background={"mode": "image", "path": "assets/other.png"}),
        status="active", stage_progress_pct=0, overall_progress_pct=0,
    )
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
        status = tick(state, CAMERA_CONFIG, STREAM, IMAGE_SESSION, new_image_schedule, tmp_path / "overlay.txt",
                       tmp_path / "snap.jpg", NOW, NOW, str(tmp_path / "logs"), 30)
    mock_popen.assert_called_once()
    assert state.running_source == {"mode": "image", "path": "assets/other.png"}
    assert status["background_mode"] == "image"


# --- Finding 2 (final review): a camera reconnect can't false-read a leftover filler frame ---


def test_camera_attempt_does_not_treat_a_leftover_filler_frame_as_connected(tmp_path, monkeypatch):
    # Review finding: start_camera_attempt previously set process_started_at to
    # the tick's own `now`, captured before stop()'s up-to-5s wait on the prior
    # (filler) process. If that prior process flushed one more frame during its
    # grace period, its mtime could land >= process_started_at purely from that
    # timing gap, falsely reading as a fresh camera frame.
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    snapshot_path = tmp_path / "snap.jpg"
    snapshot_path.write_bytes(b"leftover-frame")
    leftover_mtime = NOW.timestamp() + 1  # leftover filler frame flushed around attempt start
    os.utime(snapshot_path, (leftover_mtime, leftover_mtime))
    state = VideoState()
    with patch("brewcam.video.starter.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None), stderr=MagicMock(read=MagicMock(return_value=b"")))
        start_camera_attempt(state, CAMERA_CONFIG, STREAM, tmp_path / "overlay.txt", snapshot_path,
                              NOW, NOW, str(tmp_path / "logs"), 30)
    assert state.process_started_at.timestamp() >= leftover_mtime

    # No new frame yet (mtime unchanged) — must not read as connected.
    later = NOW + timedelta(seconds=2)
    watch_running_process(state, snapshot_path, later, later, str(tmp_path / "logs"), 30)
    assert state.status != "connected"

    # A genuinely new frame arrives strictly after process_started_at — must be detected.
    fresh_mtime = state.process_started_at.timestamp() + 1
    os.utime(snapshot_path, (fresh_mtime, fresh_mtime))
    even_later = later + timedelta(seconds=1)
    watch_running_process(state, snapshot_path, even_later, even_later, str(tmp_path / "logs"), 30)
    assert state.status == "connected"
