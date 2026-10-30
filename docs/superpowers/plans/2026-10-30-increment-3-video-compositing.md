# Brewcam Increment 3 — Video Compositing & Overlay Burn-in Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the periodic single-JPEG snapshot grab with a continuously-running, supervised FFmpeg process that resolves which background (camera / image / black) is configured for *right now*, burns the current overlay text onto every frame, and keeps the local preview (`runtime/camera_preview.jpg`) working — all without ever getting stuck once the camera falls back to `black`, and without a new destination (YouTube is increment 4).

**Architecture:** Three new pure/near-pure modules (`background.py` resolves configured vs. displayed source; `overlay_text.py` renders and atomically writes the text a long-running `drawtext` filter reloads every frame; `filters.py` builds the FFmpeg command for whichever source is active) feed a reworked `video.tick()` that replaces increment 2's single "process or no process" gate with a small priority-ordered decision: keep monitoring an in-progress camera attempt, retry the camera on the existing backoff schedule the instant it's due, or run a filler (image/black) process in between — so a camera that falls back to `black` is always retried, never abandoned.

**Tech Stack:** Python 3.15 (Pipenv), pytest — no new dependencies. Real local `ffmpeg` (confirmed present: 9.0.2 with `libx264`) for the synthetic-source verification task.

**Spec:** `docs/superpowers/specs/2026-10-30-video-compositing-design.md` (approved, builder-check findings folded in) — executors should read it; this plan does not repeat its rationale.

## Global Constraints

- Flat `brewcam/` layout; every `.py` file ≤100 lines; short functions; no new abstractions beyond what a task needs.
- `pipenv run python -m pytest` (not bare `pytest`).
- `brewcam/video/` is never imported from `overlay.py`, `schedule.py`, or `brewcam/sensors/`; the reverse (video importing `overlay.OverlayData`) is fine.
- JSONL records built field-by-field, never `**dict` passthrough.
- Camera input keeps `-rtsp_transport tcp -timeout 10000000` exactly as shipped in increment 2 (note: `-timeout`, not `-rw_timeout` — the increment-2 design doc's narrative says the latter, but the shipped code uses the former; this plan reuses the shipped flag).
- The camera, once configured, is always retried on the existing backoff schedule (`next_restart_at`/`failure_count`, unchanged formula: `min(2 * 2**(failure_count-1), 60)`) even while a `black` filler is currently rendering — a fallback to `black` must never be a dead end.
- No microphone, no audio of any kind in this increment — `-an` on every FFmpeg invocation.
- No new standing video file beyond the existing `runtime/camera_preview.jpg` artifact.
- A missing camera, missing/invalid background image, or any FFmpeg failure must degrade to a working composited frame, never an exception escaping `brewcam/video/` and never a stopped main loop.

## Review Focus

- **A camera that reconnects after falling back to `black` must actually be detected and switched back to**, not just retried once and left — the backoff timer must keep firing attempts indefinitely, not only until the first failure. Covered in Task 4.
- **A step boundary that changes `background` away from `camera` mid-connection must switch immediately**, even if the camera is currently healthy — configured intent overrides a live-but-no-longer-wanted camera feed. Covered in Task 4.
- **A `black`/`image` filler process that dies unexpectedly (crash, not a deliberate switch) must be noticed and restarted the same or next tick**, not leave the JPEG frozen forever with no supervisor watching it. Covered in Task 4.
- **An invalid or missing background image file must not crash FFmpeg startup into an unhandled state** — `validate_stream_dimensions`/`validate_background_mode` catch config-shape problems at load time, but a file that's deleted after config load (between `load_config` and this tick) must still degrade to `black` rather than leave the process in a broken loop. Covered in Task 4 and exercised in Task 7's manual check.
- **The overlay text file must never be read mid-write by the long-running `drawtext reload=1` filter** — a non-atomic write would risk an occasional visibly garbled or truncated frame. Covered in Task 2.

---

### Task 1: Background mode resolution (`brewcam/video/background.py`)

**Files:**
- Create: `brewcam/video/background.py`
- Test: `tests/test_background.py` (new file)

**Interfaces:**
- Consumes: `brewcam.config.SessionConfig` (existing — `background`, `before`, `after` dict fields), `brewcam.schedule.StageState` (existing — `.status: "upcoming"|"active"|"done"`, `.stage: Step | None`).
- Produces:
  - `resolve_configured_mode(session: SessionConfig, schedule_state: StageState) -> dict`
  - `resolve_display_source(configured_mode: dict, status: str) -> dict`

- [ ] **Step 1: Write failing tests for `resolve_configured_mode`**

```python
from brewcam.config import SessionConfig, Step
from brewcam.schedule import StageState
from brewcam.video.background import resolve_configured_mode

SESSION = SessionConfig(
    name="Test", timezone="UTC",
    background={"mode": "camera"}, before={"mode": "black"},
    after={"mode": "image", "path": "assets/standby.png"},
)
STAGE_WITH_OVERRIDE = Step(
    name="s", description="d", start=None, end=None,
    target_temperature_c=None, background={"mode": "image", "path": "assets/other.png"},
)
STAGE_NO_OVERRIDE = Step(
    name="s", description="d", start=None, end=None,
    target_temperature_c=None, background=None,
)

def test_resolve_configured_mode_prefers_step_override():
    state = StageState(stage=STAGE_WITH_OVERRIDE, status="active", stage_progress_pct=0, overall_progress_pct=0)
    assert resolve_configured_mode(SESSION, state) == {"mode": "image", "path": "assets/other.png"}

def test_resolve_configured_mode_uses_session_background_without_step_override():
    state = StageState(stage=STAGE_NO_OVERRIDE, status="active", stage_progress_pct=0, overall_progress_pct=0)
    assert resolve_configured_mode(SESSION, state) == {"mode": "camera"}

def test_resolve_configured_mode_upcoming_uses_before():
    state = StageState(stage=None, status="upcoming", stage_progress_pct=0, overall_progress_pct=0)
    assert resolve_configured_mode(SESSION, state) == {"mode": "black"}

def test_resolve_configured_mode_done_uses_after():
    state = StageState(stage=None, status="done", stage_progress_pct=100, overall_progress_pct=100)
    assert resolve_configured_mode(SESSION, state) == {"mode": "image", "path": "assets/standby.png"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_background.py -v`
Expected: FAIL — `brewcam.video.background` does not exist.

- [ ] **Step 3: Implement `resolve_configured_mode` in `brewcam/video/background.py`**

Pure function, no I/O, no clock read (matches `schedule.py`'s discipline): if `schedule_state.status == "active"`, return `schedule_state.stage.background` when not `None`, else `session.background`; if `"upcoming"`, return `session.before`; otherwise (`"done"`) return `session.after`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_background.py -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for `resolve_display_source`**

```python
from brewcam.video.background import resolve_display_source

def test_resolve_display_source_falls_back_to_black_when_camera_not_connected():
    assert resolve_display_source({"mode": "camera"}, "connecting") == {"mode": "black"}
    assert resolve_display_source({"mode": "camera"}, "unavailable") == {"mode": "black"}
    assert resolve_display_source({"mode": "camera"}, "stalled") == {"mode": "black"}

def test_resolve_display_source_shows_camera_when_connected():
    assert resolve_display_source({"mode": "camera"}, "connected") == {"mode": "camera"}

def test_resolve_display_source_passes_through_non_camera_modes_unchanged():
    spec = {"mode": "image", "path": "assets/standby.png"}
    assert resolve_display_source(spec, "unavailable") == spec
```

- [ ] **Step 6: Run tests to verify they fail, then implement, then verify they pass**

Run: `pipenv run python -m pytest tests/test_background.py -v` (expect FAIL, then implement, then PASS).

`resolve_display_source`: return `{"mode": "black"}` when `configured_mode["mode"] == "camera" and status != "connected"`; otherwise return `configured_mode` unchanged.

- [ ] **Step 7: Confirm `background.py` is ≤100 lines, then commit**

```bash
git add brewcam/video/background.py tests/test_background.py
git commit -m "feat: resolve the configured and displayed background mode"
```

---

### Task 2: Overlay text rendering and atomic write (`brewcam/video/overlay_text.py`)

**Files:**
- Create: `brewcam/video/overlay_text.py`
- Test: `tests/test_overlay_text.py` (new file)

**Interfaces:**
- Consumes: `brewcam.overlay.OverlayData` (existing, unchanged).
- Produces: `render_overlay_text(overlay: OverlayData) -> str`, `write_overlay_text(path: Path, text: str) -> None`.

- [ ] **Step 1: Write failing tests for `render_overlay_text`**

```python
from brewcam.overlay import OverlayData
from brewcam.video.overlay_text import render_overlay_text

OVERLAY = OverlayData(
    session_name="Brassin maison", stage_name="Empatage",
    stage_description="Extraction des sucres", status_label="active",
    stage_progress_pct=42.5, overall_progress_pct=10.0,
    temperature_display="67.0C",
)

def test_render_overlay_text_includes_all_fields():
    text = render_overlay_text(OVERLAY)
    for expected in ("Brassin maison", "Empatage", "Extraction des sucres", "67.0C", "42", "10"):
        assert expected in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pipenv run python -m pytest tests/test_overlay_text.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `render_overlay_text` in `brewcam/video/overlay_text.py`**

Return a multi-line `str`: session name; stage name and description; temperature; stage and overall progress (rounded to whole percent, e.g. via `f"{pct:.0f}"`). Exact layout/line breaks are the builder's call — the test above only checks presence, not formatting.

- [ ] **Step 4: Run test to verify it passes**

Run: `pipenv run python -m pytest tests/test_overlay_text.py -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for `write_overlay_text`'s atomicity**

```python
from brewcam.video.overlay_text import write_overlay_text

def test_write_overlay_text_writes_full_content(tmp_path):
    path = tmp_path / "overlay.txt"
    write_overlay_text(path, "hello\nworld")
    assert path.read_text() == "hello\nworld"

def test_write_overlay_text_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "overlay.txt"
    write_overlay_text(path, "line one")
    assert list(tmp_path.glob("*.tmp")) == []

def test_write_overlay_text_overwrites_cleanly_on_repeated_calls(tmp_path):
    path = tmp_path / "overlay.txt"
    write_overlay_text(path, "first")
    write_overlay_text(path, "second, shorter than the overwrite could leave trailing bytes from")
    write_overlay_text(path, "third")
    assert path.read_text() == "third"
```

- [ ] **Step 6: Run tests to verify they fail, then implement, then verify they pass**

Run: `pipenv run python -m pytest tests/test_overlay_text.py -v` (expect FAIL, then implement, then PASS).

`write_overlay_text`: write `text` to `path.with_suffix(path.suffix + ".tmp")`, then `os.replace(tmp_path, path)` — the rename is atomic on POSIX, so `drawtext`'s `reload=1` never observes a partially-written file.

- [ ] **Step 7: Confirm `overlay_text.py` is ≤100 lines, then commit**

```bash
git add brewcam/video/overlay_text.py tests/test_overlay_text.py
git commit -m "feat: render overlay text and write it atomically for drawtext reload"
```

---

### Task 3: FFmpeg compose command (`brewcam/video/filters.py`)

**Files:**
- Create: `brewcam/video/filters.py`
- Test: `tests/test_filters.py` (new file)

**Interfaces:**
- Consumes: nothing from other new modules — takes plain `dict`/`Path`/`str` arguments.
- Produces: `build_compose_command(display_source: dict, rtsp_url: str | None, overlay_text_path: Path, stream_config: dict, snapshot_path: Path) -> list[str]`.

- [ ] **Step 1: Write failing tests for each source mode**

```python
from pathlib import Path
from brewcam.video.filters import build_compose_command

STREAM = {"width": 1280, "height": 720, "fps": 25}
TEXT_PATH = Path("/tmp/overlay.txt")
SNAPSHOT = Path("/tmp/snap.jpg")

def test_camera_mode_uses_rtsp_input_with_timeout():
    cmd = build_compose_command({"mode": "camera"}, "rtsp://host/cam1", TEXT_PATH, STREAM, SNAPSHOT)
    assert cmd[cmd.index("-rtsp_transport") + 1] == "tcp"
    assert cmd[cmd.index("-timeout") + 1] == "10000000"
    assert cmd[cmd.index("-i") + 1] == "rtsp://host/cam1"

def test_image_mode_loops_the_configured_path():
    cmd = build_compose_command({"mode": "image", "path": "assets/standby.png"}, None, TEXT_PATH, STREAM, SNAPSHOT)
    assert "-loop" in cmd and cmd[cmd.index("-loop") + 1] == "1"
    assert cmd[cmd.index("-i") + 1] == "assets/standby.png"

def test_black_mode_uses_lavfi_color_matching_configured_dimensions():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT)
    assert cmd[cmd.index("-f") + 1] == "lavfi"
    color_arg = cmd[cmd.index("-i") + 1]
    assert "color=c=black" in color_arg and "s=1280x720" in color_arg and "r=25" in color_arg

def test_output_always_excludes_audio_and_sets_pixel_format():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT)
    assert "-an" in cmd
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert cmd[-1] == str(SNAPSHOT)

def test_filter_chain_scales_and_reloads_overlay_text():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT)
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=1280:720" in vf
    assert f"textfile={TEXT_PATH}" in vf
    assert "reload=1" in vf
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_filters.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `build_compose_command` in `brewcam/video/filters.py`**

```python
def build_compose_command(display_source, rtsp_url, overlay_text_path, stream_config, snapshot_path):
    width, height, fps = stream_config["width"], stream_config["height"], stream_config["fps"]
    mode = display_source["mode"]
    command = ["ffmpeg"]
    if mode == "camera":
        command += ["-rtsp_transport", "tcp", "-timeout", "10000000", "-i", rtsp_url]
    elif mode == "image":
        command += ["-loop", "1", "-framerate", str(fps), "-i", display_source["path"]]
    else:
        command += ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}"]
    filter_chain = (
        f"scale={width}:{height},"
        f"drawtext=textfile={overlay_text_path}:reload=1:fontcolor=white:fontsize=24:"
        "x=20:y=20:box=1:boxcolor=black@0.5"
    )
    command += ["-vf", filter_chain, "-an", "-pix_fmt", "yuv420p", "-r", "1/5", "-update", "1", "-y", str(snapshot_path)]
    return command
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_filters.py -v`
Expected: PASS

- [ ] **Step 5: Confirm `filters.py` is ≤100 lines, then commit**

```bash
git add brewcam/video/filters.py tests/test_filters.py
git commit -m "feat: build the FFmpeg compose command for camera/image/black sources"
```

---

### Task 4: Retry-coupled supervisor orchestration (`state.py`, `starter.py`, `video.py`)

**Files:**
- Modify: `brewcam/video/state.py`
- Modify: `brewcam/video/starter.py`
- Modify: `brewcam/video/video.py`
- Test: `tests/test_video.py` (append)

**Interfaces:**
- Consumes: `background.{resolve_configured_mode, resolve_display_source}` (Task 1), `overlay_text` module (Task 2, used by `main.py`, not here), `filters.build_compose_command` (Task 3), `brewcam.video.camera.{CameraSourceError, resolve_rtsp_url, redact_url}` (existing), `brewcam.video.supervisor.watch_running_process` (existing, unchanged), `brewcam.config.{SessionConfig}`, `brewcam.schedule.StageState`.
- Produces:
  - `VideoState` gains `running_source: dict | None = None`.
  - `starter.start_camera_attempt(state, camera_config, stream_config, overlay_text_path, snapshot_path, now, now_local, monitoring_dir, retention_days) -> None`
  - `starter.start_filler(state, target_mode, stream_config, overlay_text_path, snapshot_path, now_local, monitoring_dir, retention_days) -> None`
  - `video.tick(state, camera_config, stream_config, session, schedule_state, overlay_text_path, snapshot_path, now, now_local, monitoring_dir, retention_days) -> dict` (signature grows; return shape unchanged: `{"background_mode": str, "ffmpeg_running": bool}`).

- [ ] **Step 1: Add `running_source` to `VideoState`**

In `brewcam/video/state.py`, add `running_source: dict | None = None` to the dataclass. No test needed for a bare field addition (covered by the behavioral tests below).

- [ ] **Step 2: Write failing tests for `start_camera_attempt` and `start_filler`**

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from brewcam.video.state import VideoState
from brewcam.video.starter import start_camera_attempt, start_filler

NOW = datetime(2026, 11, 7, 9, 0, 0, tzinfo=timezone.utc)
STREAM = {"width": 1280, "height": 720, "fps": 25}
CAMERA_CONFIG = {"source_env": "CAMERA_SOURCE"}

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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: FAIL — `start_camera_attempt`/`start_filler` not defined.

- [ ] **Step 4: Implement `start_camera_attempt` and `start_filler` in `brewcam/video/starter.py`**

`start_camera_attempt`: resolve the RTSP URL via `resolve_rtsp_url` (existing); on `CameraSourceError`, set `state.status = "unavailable"`, `state.process = None`, `state.running_source = None`, `state.next_restart_at = now + timedelta(seconds=UNAVAILABLE_RETRY_SECONDS)`, log `camera_unavailable` (unchanged from increment 2). On success, build the command via `filters.build_compose_command({"mode": "camera"}, rtsp_url, overlay_text_path, stream_config, snapshot_path)`, `Popen` it (same `OSError` handling, backoff, and `redact_url`-based redaction as increment 2's `start_if_due`), and on success set `state.process`, `state.process_started_at = now`, `state.status = "connecting"`, `state.running_source = {"mode": "camera"}`.

`start_filler`: build the command via `filters.build_compose_command(target_mode, None, overlay_text_path, stream_config, snapshot_path)`, `Popen` it (no RTSP URL, so no secret to redact; still wrap in `try/except OSError` and log an event on failure, mirroring the existing pattern), and on success set `state.process`, `state.running_source = target_mode`. A filler never touches `state.status`, `state.failure_count`, or `state.next_restart_at` — those stay meaningful only for camera connectivity.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: PASS

- [ ] **Step 6: Write failing tests for `video.tick`'s orchestration — the two Review Focus scenarios**

```python
from brewcam.config import SessionConfig, Step
from brewcam.schedule import StageState
from brewcam.video.video import tick
from brewcam.video.state import VideoState, UNAVAILABLE_RETRY_SECONDS
from datetime import timedelta

SESSION = SessionConfig(name="T", timezone="UTC", background={"mode": "camera"},
                         before={"mode": "black"}, after={"mode": "black"})
ACTIVE_NO_OVERRIDE = Step(name="s", description="d", start=None, end=None,
                           target_temperature_c=None, background=None)
SCHEDULE_ACTIVE = StageState(stage=ACTIVE_NO_OVERRIDE, status="active", stage_progress_pct=0, overall_progress_pct=0)

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
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: FAIL — `tick`'s new signature/orchestration isn't implemented yet.

- [ ] **Step 8: Rewrite `video.tick` in `brewcam/video/video.py`**

```python
def tick(state, camera_config, stream_config, session, schedule_state, overlay_text_path,
          snapshot_path, now, now_local, monitoring_dir, retention_days):
    configured_mode = background.resolve_configured_mode(session, schedule_state)
    camera_retry_due = state.next_restart_at is None or now >= state.next_restart_at

    if configured_mode["mode"] == "camera":
        desired = {"mode": "camera"} if (state.running_source == {"mode": "camera"} or camera_retry_due) else {"mode": "black"}
    else:
        desired = configured_mode

    if state.running_source == {"mode": "camera"} and desired == {"mode": "camera"}:
        watch_running_process(state, snapshot_path, now, now_local, monitoring_dir, retention_days)
        if state.process is None:  # just failed/stalled this tick — keep the preview fresh immediately
            fallback = {"mode": "black"} if configured_mode["mode"] == "camera" else configured_mode
            start_filler(state, fallback, stream_config, overlay_text_path, snapshot_path, now_local, monitoring_dir, retention_days)
    elif state.running_source != desired or (state.process is not None and state.process.poll() is not None):
        if state.process is not None:
            stop(state)
        if desired["mode"] == "camera":
            start_camera_attempt(state, camera_config, stream_config, overlay_text_path, snapshot_path,
                                  now, now_local, monitoring_dir, retention_days)
        else:
            start_filler(state, desired, stream_config, overlay_text_path, snapshot_path, now_local, monitoring_dir, retention_days)

    display_source = background.resolve_display_source(configured_mode, state.status)
    return {"background_mode": display_source["mode"], "ffmpeg_running": state.process is not None}
```

Walk through why each branch exists against the Review Focus list: the first `if` keeps monitoring an in-progress/healthy camera attempt without tearing it down every tick (fixes the "flapping" risk); its inner `if state.process is None` immediately starts a filler the same tick a camera attempt just failed, so the preview never goes stale for even one tick; the `elif`'s `state.running_source != desired` is what makes a step-boundary change away from `camera` take effect immediately even though the camera was healthy; its `state.process.poll() is not None` clause catches a filler that died on its own between ticks.

- [ ] **Step 9: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: PASS. If `test_camera_falling_back_to_black_is_retried_on_the_backoff_schedule_not_abandoned` fails on the final retry, check that `camera_retry_due`'s comparison uses `>=` against the exact `next_restart_at` the backoff helper computed.

- [ ] **Step 10: Confirm `state.py`/`starter.py`/`video.py` are each ≤100 lines (split `start_camera_attempt`/`start_filler` into a separate `filler.py` if `starter.py` overflows), then commit**

```bash
git add brewcam/video/state.py brewcam/video/starter.py brewcam/video/video.py tests/test_video.py
git commit -m "feat: retry-couple camera reconnection with background-mode fallback"
```

---

### Task 5: Stream dimension validation (`brewcam/config/validation.py`, `config.py`)

**Files:**
- Modify: `brewcam/config/validation.py`
- Modify: `brewcam/config/config.py`
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Produces: `validate_stream_dimensions(spec: dict) -> None` (raises `ConfigError`), called from `load_config`.

- [ ] **Step 1: Write failing tests**

```python
import pytest
from brewcam.config.validation import ConfigError, validate_stream_dimensions

def test_validate_stream_dimensions_accepts_positive_integers():
    validate_stream_dimensions({"width": 1280, "height": 720, "fps": 25})  # must not raise

def test_validate_stream_dimensions_rejects_non_positive_width():
    with pytest.raises(ConfigError):
        validate_stream_dimensions({"width": 0, "height": 720, "fps": 25})

def test_validate_stream_dimensions_rejects_non_integer_fps():
    with pytest.raises(ConfigError):
        validate_stream_dimensions({"width": 1280, "height": 720, "fps": "fast"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_config.py -v`
Expected: FAIL — function not defined.

- [ ] **Step 3: Implement `validate_stream_dimensions` in `validation.py`, call it from `load_config` in `config.py`**

Raise `ConfigError` unless `width`, `height`, `fps` are all `int` and `> 0`. In `config.py`, call `validate_stream_dimensions(config.stream)` alongside the existing validation calls (inside the same `try` block so it's wrapped into `ConfigError` consistently with the others).

- [ ] **Step 4: Run tests to verify they pass, then commit**

Run: `pipenv run python -m pytest tests/test_config.py -v`
Expected: PASS

```bash
git add brewcam/config/validation.py brewcam/config/config.py tests/test_config.py
git commit -m "feat: validate stream width/height/fps at config load time"
```

---

### Task 6: Wire compositing into the main loop (`main.py`)

**Files:**
- Modify: `brewcam/main.py`

**Interfaces:**
- Consumes: `overlay_text.{render_overlay_text, write_overlay_text}` (Task 2), `video.tick`'s new signature (Task 4).

- [ ] **Step 1: Add the overlay text path and write it every iteration**

Add `overlay_text_path = snapshot_path.parent / "overlay.txt"` next to the existing `snapshot_path` construction. Each loop iteration, immediately after building `overlay`, call `overlay_text.write_overlay_text(overlay_text_path, overlay_text.render_overlay_text(overlay))` — before the `video.tick(...)` call, so the file the FFmpeg process reads this tick already reflects the current overlay.

- [ ] **Step 2: Pass the new arguments into `video.tick(...)`**

Update the call site to pass `config.stream`, `config.session`, `state` (the `StageState` from `compute_schedule_state`), and `overlay_text_path` alongside the existing arguments, matching Task 4's new `tick` signature.

- [ ] **Step 3: Manually verify the loop still runs cleanly**

Run: `pipenv run python -m brewcam --config config/session.example.yaml` (with `CAMERA_SOURCE` unset), let it run a few seconds, press Ctrl+C.
Expected: no traceback; `runtime/camera_preview.jpg` shows a black frame with overlay text burned in and updates every ~5s; `logs/<today>.jsonl` keeps writing `snapshot` records; process exits cleanly.

- [ ] **Step 4: Run the full test suite**

Run: `pipenv run python -m pytest -v`
Expected: all tests PASS (Tasks 1–5's new tests plus every pre-existing test).

- [ ] **Step 5: Commit**

```bash
git add brewcam/main.py
git commit -m "feat: composite overlay text onto the video feed from the main loop"
```

---

### Task 7: Real-FFmpeg synthetic-source verification and README status update

**Files:**
- Test: `tests/test_composition_integration.py` (new file — uses the real local `ffmpeg`, not mocked)
- Modify: `README.md`

**Interfaces:** none new — exercises Tasks 1–4's real output end-to-end.

- [ ] **Step 1: Write a test that runs the real `build_compose_command` output against a synthetic `black` source**

```python
import subprocess
import time
from pathlib import Path
from brewcam.video.filters import build_compose_command
from brewcam.video.overlay_text import write_overlay_text

def test_black_source_produces_a_correctly_sized_jpeg(tmp_path):
    overlay_path = tmp_path / "overlay.txt"
    write_overlay_text(overlay_path, "Brassin maison\nEmpatage\n67.0C")
    snapshot_path = tmp_path / "snap.jpg"
    command = build_compose_command({"mode": "black"}, None, overlay_path,
                                     {"width": 320, "height": 240, "fps": 10}, snapshot_path)
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if snapshot_path.exists():
                break
            time.sleep(0.1)
        assert snapshot_path.exists()
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(snapshot_path)],
            capture_output=True, text=True, check=True,
        )
        assert probe.stdout.strip() == "320,240"
    finally:
        process.terminate()
        process.wait(timeout=5)
```

Mark this test to be skipped in environments without `ffmpeg`/`ffprobe` on `PATH` (e.g. `pytest.importorskip` isn't right for a binary — use a module-level `pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, ...)`).

- [ ] **Step 2: Run the test to verify it fails, implement nothing further (Tasks 1–3 already provide everything needed), then verify it passes**

Run: `pipenv run python -m pytest tests/test_composition_integration.py -v`
Expected: FAIL only if `build_compose_command`'s flags don't actually produce valid output (a real bug, not a stub) — if so, fix `filters.py`, not this test. Then: PASS.

- [ ] **Step 3: Repeat for `image` mode against `assets/standby.png`**

Same structure, `{"mode": "image", "path": "assets/standby.png"}`, asserting the output JPEG matches the configured `width`/`height` (the `scale` filter normalizes the source image's own dimensions).

- [ ] **Step 4: Write a real-FFmpeg test for a missing background image file**

```python
def test_missing_image_file_exits_quickly_instead_of_hanging(tmp_path):
    overlay_path = tmp_path / "overlay.txt"
    write_overlay_text(overlay_path, "test")
    command = build_compose_command({"mode": "image", "path": str(tmp_path / "does-not-exist.png")},
                                      None, overlay_path, {"width": 320, "height": 240, "fps": 10},
                                      tmp_path / "snap.jpg")
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    exit_code = process.wait(timeout=5)  # must exit on its own, not hang
    assert exit_code != 0
```

This is what makes Task 4's "filler that dies is noticed and restarted" behavior (already covered there with a mocked process) meaningful end-to-end: a real missing-image config produces a real process that exits quickly, which `tick()`'s `state.process.poll() is not None` branch then catches on the next tick and retries with `black` — degrading as the spec requires rather than hanging.

- [ ] **Step 5: Update README's status section**

State plainly that background-mode switching and overlay burn-in are now implemented and locally verified (camera/image/black, via the synthetic-source tests above and manual verification against the real camera), but YouTube publishing is still not implemented — avoid any wording implying a YouTube destination exists yet.

- [ ] **Step 6: Run the full test suite one more time, then commit**

Run: `pipenv run python -m pytest -v`
Expected: all PASS.

```bash
git add tests/test_composition_integration.py README.md
git commit -m "test: verify composited output locally with a synthetic source; update README status"
```
