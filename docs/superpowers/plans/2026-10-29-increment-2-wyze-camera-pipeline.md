# Brewcam Increment 2 (partial) — Wyze Camera Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get a real Wyze Cam V2 feed, via Docker Wyze Bridge, flowing through an FFmpeg subprocess that Brewcam supervises — visible locally as a refreshing JPEG snapshot for UAT — without stopping the existing sensor/schedule/monitoring loop on camera trouble, and without yet touching video compositing or YouTube publishing.

**Architecture:** A new `brewcam/video/` package: `camera.py` resolves the RTSP source URL from the existing `camera.source_env` config convention and redacts it for safe logging; `video.py` builds the FFmpeg command and runs a small state-machine supervisor (`VideoState` + `tick()`) polled once per existing 1-second main-loop iteration — no threads, no async. `main.py` swaps its hardcoded `VIDEO_STATUS` for this supervisor's live output and calls `stop()` on shutdown. A new root `Dockerfile` and `docker-compose.yml` run Brewcam and `mrlt8/wyze-bridge:2.10.3` as two services on a shared Docker network, with the bridge's ports bound to `127.0.0.1` only.

**Tech Stack:** Python 3.15 (Pipenv), PyYAML, pytest — no new Python dependencies. `ffmpeg` (subprocess, installed in the Docker image). Docker Compose for the two-service topology.

**Spec:** `docs/superpowers/specs/2026-10-29-wyze-camera-pipeline-design.md` (approved, no open questions) — executors should read both documents; this plan does not repeat the design's rationale.

## Global Constraints

- Flat `brewcam/` package layout (no `src/`); every `.py` file ≤100 lines; short functions; no new abstractions beyond what a task needs.
- `pipenv run python -m pytest` (not bare `pytest`) for every test run — the flat layout requires it.
- `-an` (no audio) and `-rtsp_transport tcp` are present on every FFmpeg invocation of the camera stream; never add an audio track.
- `brewcam/video/` is never imported from `overlay.py`, `schedule.py`, or `brewcam/sensors/` — camera/video code stays independent of display and sensor logic.
- JSONL records are built field-by-field, never `**dict` passthrough — matches the existing `monitoring.py` pattern.
- A camera outage, stall, or missing config must never raise out of `brewcam/video/`'s public functions and must never stop the main loop's sensor/schedule/snapshot cadence.
- No secret (the RTSP URL's embedded credentials, or any part of it) may appear unredacted in a log line, a JSONL record, or raised/printed exception text.
- `.env` holds real secrets and is already gitignored; `.env.example` holds placeholders only.
- No FFmpeg-level video compositing (`filters.py`) and no YouTube publishing (`youtube.py`) in this plan — out of scope.
- Docker Wyze Bridge is pinned to `mrlt8/wyze-bridge:2.10.3`; its ports are published to `127.0.0.1` only; no Cloudflare Tunnel or `0.0.0.0` binding; no WebRTC/RTMP/HLS ports published.
- Supervisor defaults (tunable constants, not hardware-validated): snapshot interval 5s, stall timeout 15s, backoff 2s doubling to a 60s cap, healthy-reset window 60s.
- This plan's executor has no working `docker` CLI — Docker Compose/image work is validated structurally (YAML parses, no literal secrets, correct port bindings) and written up for the user to run; it is not executed live by the plan's executor.

## Review Focus

- **A crash-looping camera must not busy-loop or spam logs:** if FFmpeg exits immediately on every attempt (e.g. a bad URL), the retry delay must actually grow call-over-call and cap at 60s, not retry every tick. Covered in Task 2.
- **A stale leftover snapshot file must not fake "connected":** if `runtime/<snapshot>.jpg` exists from a previous run before this run's FFmpeg has produced a frame, the supervisor must not report `"connected"` off that old file's mtime. Covered in Task 2.
- **Redaction must survive an exception path, not just the happy path:** if starting FFmpeg raises (e.g. `Popen` fails) with the raw command/URL embedded in the exception, nothing reaching a JSONL event or stderr may contain the unredacted secret. Covered in Task 2.
- **`stop()` must be a safe no-op with nothing running:** calling shutdown while the supervisor has no live process (mid-backoff, or never started) must not raise. Covered in Task 2.
- **A missing `CAMERA_SOURCE` env var must degrade, not crash:** the main loop must keep writing sensor snapshots every interval even when the camera has never once connected. Covered in Task 2 and exercised end-to-end in Task 3's manual verification.

---

### Task 1: Camera source resolution and URL redaction (`brewcam/video/camera.py`)

**Files:**
- Create: `brewcam/video/__init__.py` (empty, matches `brewcam/sensors/__init__.py`'s original style)
- Create: `brewcam/video/camera.py`
- Test: `tests/test_video.py` (new file)

**Interfaces:**
- Consumes: nothing from other Brewcam modules — operates on a plain `dict` (the existing `BrewcamConfig.camera` field) and `os.environ`.
- Produces:
  - `class CameraSourceError(Exception)`
  - `resolve_rtsp_url(camera_config: dict) -> str`
  - `redact_url(url: str) -> str`

- [ ] **Step 1: Create `brewcam/video/__init__.py` (empty) and write failing tests for `resolve_rtsp_url`**

```python
import pytest
from brewcam.video.camera import CameraSourceError, resolve_rtsp_url

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: FAIL — `brewcam.video.camera` does not exist.

- [ ] **Step 3: Implement `CameraSourceError` and `resolve_rtsp_url` in `brewcam/video/camera.py`**

Read `os.environ.get(camera_config["source_env"])`; raise `CameraSourceError(f"environment variable {env_name!r} is not set")` if the value is `None` or empty.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for `redact_url`**

```python
from brewcam.video.camera import redact_url

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
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: FAIL — `redact_url` not defined.

- [ ] **Step 7: Implement `redact_url` in `brewcam/video/camera.py`**

Use `urllib.parse.urlsplit`/`urlunsplit`: if `netloc` contains `"@"`, replace the userinfo portion with `"***:***"`. Re-encode the query string via `parse_qsl`/`urlencode`, replacing any parameter whose name (case-insensitive) contains `"token"`, `"key"`, `"pass"`, or `"secret"` with `"***"`.

- [ ] **Step 8: Run tests to verify they pass, confirm `camera.py` is ≤100 lines, then commit**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: PASS

```bash
git add brewcam/video/__init__.py brewcam/video/camera.py tests/test_video.py
git commit -m "feat: resolve and redact the Wyze bridge RTSP source URL"
```

---

### Task 2: FFmpeg command and supervisor state machine (`brewcam/video/video.py`)

**Files:**
- Create: `brewcam/video/video.py`
- Test: `tests/test_video.py` (append)

**Interfaces:**
- Consumes: `brewcam.video.camera.{CameraSourceError, resolve_rtsp_url, redact_url}` (Task 1); `brewcam.config.monitoring.write_event` (existing).
- Produces:
  - `@dataclass class VideoState` with fields: `process: subprocess.Popen | None = None`, `status: str = "unavailable"` (one of `"unavailable" | "connecting" | "connected" | "stalled"`), `failure_count: int = 0`, `next_restart_at: datetime | None = None`, `process_started_at: datetime | None = None`, `connected_since: datetime | None = None`
  - `build_snapshot_command(rtsp_url: str, snapshot_path: Path) -> list[str]`
  - `tick(state: VideoState, camera_config: dict, snapshot_path: Path, now: datetime, now_local: datetime, monitoring_dir: str, retention_days: int) -> dict` — mutates `state` in place; returns `{"background_mode": "camera" | "black", "ffmpeg_running": bool}` for `monitoring.write_snapshot`.
  - `stop(state: VideoState) -> None`

- [ ] **Step 1: Write failing tests for `build_snapshot_command`**

```python
from pathlib import Path
from brewcam.video.video import build_snapshot_command

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: FAIL — `brewcam.video.video` does not exist.

- [ ] **Step 3: Implement `build_snapshot_command` in `brewcam/video/video.py`**

Return `["ffmpeg", "-rtsp_transport", "tcp", "-rw_timeout", "10000000", "-i", rtsp_url, "-an", "-vf", "fps=1/5", "-update", "1", "-y", str(snapshot_path)]`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for starting the process when configured, and degrading when not**

```python
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from brewcam.video.video import VideoState, tick

NOW = datetime(2026, 11, 7, 9, 0, 0, tzinfo=timezone.utc)
CAMERA_CONFIG = {"source_env": "CAMERA_SOURCE"}

def _tick(state, snapshot_path, now, monkeypatch_env=None, **kwargs):
    return tick(state, CAMERA_CONFIG, snapshot_path, now, now, str(kwargs.get("log_dir")), 30)

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
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: FAIL — `VideoState`/`tick` not defined.

- [ ] **Step 7: Implement `VideoState` and the "no process yet" half of `tick` in `brewcam/video/video.py`**

When `state.process is None`: if `state.next_restart_at is not None and now < state.next_restart_at`, do nothing this tick (this is what paces retries — a resolve/start attempt only happens once per backoff window, so the event below can never fire more than once per ~60s even without extra dedup bookkeeping). Otherwise call `resolve_rtsp_url`; on `CameraSourceError`, set `state.status = "unavailable"`, `state.next_restart_at = now + timedelta(seconds=60)`, and call `monitoring.write_event(monitoring_dir, now_local, "camera_unavailable", {}, retention_days)`. On success, call `build_snapshot_command` and `subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)`; on success set `state.process`, `state.process_started_at = now`, `state.status = "connecting"`; wrap the `Popen` call in `try/except OSError` — on failure, redact the command before using it in any event detail, increment `state.failure_count`, set `state.next_restart_at` via the backoff helper from Step 11. Return `{"background_mode": "black", "ffmpeg_running": state.process is not None}` for this branch (never `"camera"` before a frame is confirmed).

- [ ] **Step 8: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: PASS

- [ ] **Step 9: Write failing tests for the connected/stall transitions and the stale-snapshot guard**

```python
import os

def _running_process():
    return MagicMock(poll=MagicMock(return_value=None), terminate=MagicMock(), kill=MagicMock(), wait=MagicMock())

def test_tick_transitions_to_connected_once_snapshot_file_updated(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    snapshot_path = tmp_path / "snap.jpg"
    state = VideoState(process=_running_process(), status="connecting", process_started_at=NOW)
    snapshot_path.write_bytes(b"jpg")
    os.utime(snapshot_path, (NOW.timestamp() + 1, NOW.timestamp() + 1))
    status = tick(state, CAMERA_CONFIG, snapshot_path, NOW + timedelta(seconds=2), NOW + timedelta(seconds=2), str(tmp_path / "logs"), 30)
    assert state.status == "connected"
    assert status == {"background_mode": "camera", "ffmpeg_running": True}

def test_tick_ignores_a_snapshot_left_over_from_a_previous_run(tmp_path, monkeypatch):
    # snapshot predates this attempt's process start — must not count as a fresh frame
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wyze-bridge:8554/cam1")
    snapshot_path = tmp_path / "snap.jpg"
    snapshot_path.write_bytes(b"old")
    os.utime(snapshot_path, (NOW.timestamp() - 100, NOW.timestamp() - 100))
    state = VideoState(process=_running_process(), status="connecting", process_started_at=NOW)
    status = tick(state, CAMERA_CONFIG, snapshot_path, NOW + timedelta(seconds=2), NOW + timedelta(seconds=2), str(tmp_path / "logs"), 30)
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
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: FAIL — the "process running" branch of `tick` isn't implemented yet.

- [ ] **Step 11: Implement the "process running" half of `tick`, the backoff helper, and `stop`**

When `state.process is not None` and `state.process.poll() is None` (still running): read `snapshot_path.stat().st_mtime` if the file exists; treat it as a fresh frame only if `mtime >= state.process_started_at.timestamp()` (guards the stale-leftover case) **and** `now - mtime <= 15` seconds (stall timeout). If fresh and `state.status != "connected"`: set `status = "connected"`, `connected_since = now`, and emit `"camera_connected"` — this only fires on the transition itself, since the condition is false on every later tick while already connected. If `state.status == "connected"` and the frame is no longer fresh (stall): call `state.process.terminate()`, set `state.process = None`, `status = "stalled"`, bump `failure_count`, set `next_restart_at` via the backoff helper, emit `"camera_stalled"`. If `state.status == "connected"` and `now - state.connected_since >= 60` seconds: reset `state.failure_count = 0` (no event — this only affects future backoff timing). If `state.process.poll()` is not `None` (it exited on its own): capture the previous status, set `state.process = None`, `status = "unavailable"`, bump `failure_count`, set `next_restart_at`; if the previous status was `"connected"` or `"stalled"`, emit `"camera_disconnected"`.

Backoff helper: `_backoff_seconds(failure_count: int) -> float` returns `min(2 * (2 ** (failure_count - 1)), 60)`. It is always called *after* `state.failure_count` has just been incremented (so `failure_count >= 1`), giving the sequence 2s, 4s, 8s, 16s, 32s, 60s(capped from 64s), ... matching the Global Constraints.

`stop(state: VideoState) -> None`: if `state.process is None`, return. Otherwise call `state.process.terminate()`, then `state.process.wait(timeout=5)`, catching `subprocess.TimeoutExpired` and calling `state.process.kill()` in that case. Always set `state.process = None` afterward.

Every call that logs an event detail containing the camera config or URL runs it through `redact_url` first.

- [ ] **Step 12: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: PASS

- [ ] **Step 13: Write failing tests for backoff growth/cap, the healthy-reset window, redaction under an exception, and a safe `stop()`**

```python
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
    state = VideoState(process=_running_process(), status="connected", process_started_at=NOW, connected_since=NOW, failure_count=3)
    later = NOW + timedelta(seconds=61)
    os.utime(snapshot_path, (later.timestamp(), later.timestamp()))
    tick(state, CAMERA_CONFIG, snapshot_path, later, later, str(tmp_path / "logs"), 30)
    assert state.failure_count == 0

def test_popen_failure_never_leaks_the_raw_url_into_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wb:super-secret@wyze-bridge:8554/cam1")
    state = VideoState()
    log_dir = tmp_path / "logs"
    with patch("brewcam.video.video.subprocess.Popen", side_effect=OSError("rtsp://wb:super-secret@wyze-bridge:8554/cam1 unreachable")):
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
```

- [ ] **Step 14: Run tests, fix any gap (most likely the exception-path redaction), then confirm all pass**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: PASS. If `test_popen_failure_never_leaks_the_raw_url_into_logs` fails, the `OSError` branch in Step 11 is logging `str(exc)` directly — redact it (e.g. regex-replace the resolved URL, or simply never include the exception text verbatim in event `details`, only a fixed message).

- [ ] **Step 15: Confirm `video.py` is ≤100 lines (split a helper into `camera.py` or a new small module if not), then commit**

```bash
git add brewcam/video/video.py tests/test_video.py
git commit -m "feat: supervise an FFmpeg subprocess reading the Wyze RTSP feed"
```

---

### Task 3: Wire the video supervisor into the main loop (`main.py`)

**Files:**
- Modify: `brewcam/main.py`

**Interfaces:**
- Consumes: `brewcam.video.video.{VideoState, tick, stop}` (Task 2).
- Produces: `run(config_path: str) -> None` (signature unchanged) now reports live camera status instead of the hardcoded placeholder.

- [ ] **Step 1: Replace the hardcoded `VIDEO_STATUS` with a `VideoState` and a per-iteration `tick()` call**

Remove the module-level `VIDEO_STATUS` constant. In `run()`, create `video_state = VideoState()` and a `snapshot_path = Path(config.monitoring["directory"]).parent / "runtime" / "camera_preview.jpg"` (create the `runtime` directory if missing). Each loop iteration, before building `overlay`, call `video_status = video.tick(video_state, config.camera, snapshot_path, now, now_local, config.monitoring["directory"], config.monitoring["retention_days"])` and pass `video_status` into `monitoring.write_snapshot` where `VIDEO_STATUS` was used before.

- [ ] **Step 2: Call `video.stop(video_state)` on every exit path**

Add it immediately before each existing `monitoring.write_event` call in `run()` — the one in the `except Exception` block and the one after the loop — so FFmpeg is always terminated before Brewcam's own shutdown/error event is logged.

- [ ] **Step 3: Manually verify the loop still runs cleanly without a camera configured**

Run: `pipenv run python -m brewcam --config config/session.example.yaml` (with `CAMERA_SOURCE` unset or pointing at an unreachable address), let it run a few seconds, press Ctrl+C.
Expected: no traceback; `logs/<today>.jsonl` contains `"event_type": "camera_unavailable"` and ongoing `"event_type": "snapshot"` lines; process exits cleanly.

- [ ] **Step 4: Run the full test suite**

Run: `pipenv run python -m pytest -v`
Expected: all tests PASS (Tasks 1–2's new tests plus every pre-existing test).

- [ ] **Step 5: Commit**

```bash
git add brewcam/main.py
git commit -m "feat: supervise the Wyze camera feed from the main loop"
```

---

### Task 4: Docker Compose topology, Dockerfile, and env

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Modify: `.env.example`
- Test: `tests/test_deployment.py` (new file — structural checks only, no live Docker)

**Interfaces:**
- Consumes: nothing from Python code.
- Produces: a `wyze-bridge` service and a `brewcam` service on one Compose network; `.env.example`'s `CAMERA_SOURCE` placeholder updated to the credentialed form.

- [ ] **Step 1: Write `Dockerfile`**

Base on `python:3.15-slim` (verify this tag exists on Docker Hub when implementing; if not, use the closest available Python 3.15 slim tag and note the substitution in the commit message). Install `ffmpeg` via `apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*`. Copy `Pipfile`/`Pipfile.lock`, run `pipenv install --deploy --ignore-pipfile --system`. Copy the rest of the repo. Use the shell-form `CMD python -m brewcam --config "${BREWCAM_CONFIG:-config/session.example.yaml}"` (see design doc's builder-check correction — this must stay shell form, not exec form, so the `${...}` default expands).

- [ ] **Step 2: Write `docker-compose.yml`**

Two services on one user-defined network (e.g. `brewcam-net`):
- `wyze-bridge`: `image: mrlt8/wyze-bridge:2.10.3`; `ports: ["127.0.0.1:8554:8554", "127.0.0.1:5050:5000"]`; `environment:` block with `WB_AUTH=true`, `WB_USERNAME`/`WB_PASSWORD` left commented out (per the design's builder note — set later via the bridge's web UI), `API_ID=${WYZE_KEY_ID}`, `API_KEY=${WYZE_API_KEY}`, `WB_API=${WYZE_API_KEY}`.
- `brewcam`: `build: .`; `environment:` passing through `CAMERA_SOURCE` and `BREWCAM_CONFIG` (both read from `.env`, no default baked into the compose file for `CAMERA_SOURCE`); `volumes:` mounting `./config:/app/config`, `./runtime:/app/runtime`, `./logs:/app/logs`, `./assets:/app/assets`; `depends_on: [wyze-bridge]`; no published ports.

- [ ] **Step 3: Update `.env.example`'s `CAMERA_SOURCE` line**

```
CAMERA_SOURCE=rtsp://wb:REPLACE_WITH_YOUR_WYZE_API_KEY@wyze-bridge:8554/REPLACE_WITH_CAMERA_PATH_FROM_BRIDGE_UI
```

- [ ] **Step 4: Write a failing structural test for the compose file**

```python
import yaml

def test_compose_file_parses_and_has_expected_services():
    with open("docker-compose.yml") as f:
        compose = yaml.safe_load(f)
    assert set(compose["services"]) == {"wyze-bridge", "brewcam"}

def test_compose_ports_are_bound_to_loopback_only():
    with open("docker-compose.yml") as f:
        compose = yaml.safe_load(f)
    ports = compose["services"]["wyze-bridge"].get("ports", [])
    assert ports, "wyze-bridge should publish at least the RTSP and web UI ports"
    for mapping in ports:
        assert str(mapping).startswith("127.0.0.1:"), f"{mapping} is not bound to loopback"
    assert "ports" not in compose["services"]["brewcam"]

def test_compose_file_contains_no_literal_secrets():
    text = open("docker-compose.yml").read()
    for placeholder in ("WYZE_KEY_ID", "WYZE_API_KEY"):
        assert f"${{{placeholder}}}" in text  # substitution syntax present
    assert "@" not in text  # no embedded-credential URL written literally into the compose file
```

- [ ] **Step 5: Run the test to verify it fails, then write the files from Steps 1–3, then verify it passes**

Run: `pipenv run python -m pytest tests/test_deployment.py -v`
Expected: FAIL (no `docker-compose.yml` yet) → write `Dockerfile`, `docker-compose.yml`, update `.env.example` → PASS.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml .env.example tests/test_deployment.py
git commit -m "feat: add Docker Compose topology for Brewcam and Docker Wyze Bridge"
```

---

### Task 5: README documentation

**Files:**
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add README sections covering the camera pipeline**

Cover, in this order: (1) prerequisites (Docker, Docker Compose, a Wyze account with API credentials) and the deployment topology (both services run in Docker on one host); (2) starting/stopping the bridge (`docker compose up -d wyze-bridge`, `docker compose down`); (3) providing Wyze auth (`.env` from `.env.example`: `WYZE_KEY_ID`, `WYZE_API_KEY`, bridge web UI login on first visit to `http://127.0.0.1:5050`); (4) finding the camera's real RTSP path in the bridge web UI (explicitly: do not assume it matches the camera's display name); (5) testing the stream independently with `ffplay "rtsp://wb:$WYZE_API_KEY@127.0.0.1:8554/<path>"` before launching Brewcam; (6) the exact Brewcam launch command (`docker compose up brewcam`) and where to watch the result (`runtime/camera_preview.jpg`, `logs/<date>.jsonl`); (7) host-vs-Docker addressing (`wyze-bridge` service name inside Compose vs `127.0.0.1` from the host); (8) troubleshooting: auth errors (wrong `WYZE_API_KEY`/expired bridge login), camera missing from the bridge UI (re-check the Wyze account/region), connection timeouts (`camera_unavailable`/`camera_stalled` events in the JSONL log), and an interrupted stream (expect `camera_disconnected` followed by automatic retry with growing backoff, capped at 60s).

- [ ] **Step 2: Update the "Status" section to reflect what increment 2 (partial) added vs. what's still missing**

State plainly that camera ingestion is live but video compositing and YouTube publishing are not yet implemented — avoid any wording that implies YouTube output exists.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the Wyze camera pipeline setup and troubleshooting"
```
