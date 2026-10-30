# Brewcam Increment 4 — YouTube Live Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second, RTMPS output leg onto increment 3's already-composited FFmpeg process so the same picture shown locally (`runtime/camera_preview.jpg`) is also pushed to YouTube — with no microphone audio (a generated silent track instead), no new encoding/compositing stage, and credentials that never reach git, logs, or an exception message.

**Architecture:** `youtube.py` resolves the two required env vars (ingestion URL, stream key) into a small value object and provides the only place they're ever assembled into a URL or redacted from text. `filters.build_compose_command` (from increment 3) gains an optional `youtube_target` parameter that adds a silent-audio input and a second `-map`'d output using the encoder settings YouTube's current Help page specifies. The existing camera/backoff supervisor (increment 2/3) stays the single source of truth for restart/retry — a YouTube connection failure is just another way the one process exits, handled by the restart logic that already exists, now with FFmpeg's stderr captured and redacted into the resulting monitoring event.

**Tech Stack:** Python 3.15 (Pipenv), pytest. Real local `ffmpeg` (9.0.2, `libx264`, OpenSSL-enabled — confirmed RTMPS-capable) for the local-output verification task. No new Python dependencies.

**Spec:** `docs/superpowers/specs/2026-10-30-youtube-live-publishing-design.md` (approved, builder-check findings folded in) — executors should read it; this plan does not repeat its rationale. Assumes `docs/superpowers/plans/2026-10-30-increment-3-video-compositing.md` is implemented first; this plan's "Consumes" blocks reference that plan's exact function names and signatures.

## Global Constraints

- Flat `brewcam/` layout; every `.py` file ≤100 lines; short functions.
- `pipenv run python -m pytest` (not bare `pytest`).
- RTMPS only — `-f flv <rtmps_url>`, never plain `rtmp://`.
- Secrets indirected through `*_env` config fields (`stream.ingest_url_env`, `stream.key_env`), resolved from `os.environ` only at the point something needs the value, never at config-load time.
- `.env` never committed; `.env.example` holds placeholders only.
- Encoder settings, sourced from `support.google.com/youtube/answer/2853702` (checked 2026-10-30): H.264 (`libx264`), `main` profile, `yuv420p`, CBR (`-b:v`/`-minrate`/`-maxrate` equal, `-bufsize` ≈ 2x bitrate), 2-second keyframe interval (`-g`/`-keyint_min` = `2 * fps`, `-sc_threshold 0`), 2 B-frames (`-bf 2`). Audio: AAC, 128 kbps, 44.1 kHz, stereo — always from `anullsrc`, never a real capture device.
- `bitrate_kbps` defaults to 3000 (inside YouTube's published 720p H.264 range) and is config-driven; GOP/bufsize/profile/pixel-format are derived from it and from `stream.width/height/fps`, not separately configurable.
- No microphone capture under any configuration, with or without a YouTube target.
- A missing/invalid YouTube env var degrades to JPEG-only operation (no exception out of `brewcam/video/`, no stopped main loop); the local preview and sensor/schedule loop never stop because of a YouTube-side problem.
- No second retry/backoff state machine for "YouTube health" — a YouTube failure is handled by the same process-level restart/backoff increment 2/3 already built.
- JSONL records built field-by-field, never `**dict` passthrough.

## Review Focus

- **The raw stream key must never appear more than once in the constructed command** (only inside the final `rtmps_url`) — a duplicated flag or echoed value elsewhere would defeat every other redaction effort. Covered in Task 2.
- **Missing or invalid `YOUTUBE_INGEST_URL`/`YOUTUBE_STREAM_KEY` must degrade to JPEG-only, not crash** — camera/sensor monitoring keeps running exactly as if YouTube were never configured. Covered in Task 3.
- **Turning on stderr capture (new in this increment) must not newly leak the camera's own RTSP credential** — this is the exact gap builder-check caught against the original spec draft (which only redacted the YouTube key). Covered in Task 3.
- **No real audio-capture device flag (`avfoundation`, `alsa`, `dshow`, `pulse`) may ever appear in the command**, with or without a YouTube target, under any background mode. Covered in Task 2.
- **The `youtube` monitoring field must only ever report locally-observable facts** (`configured`, `leg_active`) and must never be phrased or tested as if it confirms YouTube-side playback, since this increment has no API access to check that. Covered in Task 5.

---

### Task 1: YouTube target resolution and redaction (`brewcam/video/youtube.py`)

**Files:**
- Create: `brewcam/video/youtube.py`
- Test: `tests/test_youtube.py` (new file)

**Interfaces:**
- Consumes: `os.environ` only.
- Produces:
  - `class YoutubeConfigError(Exception)`
  - `@dataclass YoutubeTarget: ingest_url: str; stream_key: str` with a `rtmps_url` property.
  - `resolve_youtube_target(stream_config: dict) -> YoutubeTarget | None`
  - `redact_target(target: YoutubeTarget | None) -> str`
  - `redact_stderr(text: str) -> str`

- [ ] **Step 1: Write failing tests for `resolve_youtube_target` and `YoutubeTarget`**

```python
import pytest
from brewcam.video.youtube import YoutubeConfigError, YoutubeTarget, resolve_youtube_target

STREAM_YOUTUBE = {"destination": "youtube", "ingest_url_env": "YOUTUBE_INGEST_URL", "key_env": "YOUTUBE_STREAM_KEY"}

def test_resolve_youtube_target_returns_none_when_destination_is_not_youtube():
    assert resolve_youtube_target({"destination": "none"}) is None

def test_resolve_youtube_target_builds_target_from_env(monkeypatch):
    monkeypatch.setenv("YOUTUBE_INGEST_URL", "rtmps://a.rtmps.youtube.com/live2")
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "abcd-1234")
    target = resolve_youtube_target(STREAM_YOUTUBE)
    assert target.rtmps_url == "rtmps://a.rtmps.youtube.com/live2/abcd-1234"

def test_resolve_youtube_target_raises_when_ingest_url_missing(monkeypatch):
    monkeypatch.delenv("YOUTUBE_INGEST_URL", raising=False)
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "abcd-1234")
    with pytest.raises(YoutubeConfigError):
        resolve_youtube_target(STREAM_YOUTUBE)

def test_resolve_youtube_target_raises_when_stream_key_missing(monkeypatch):
    monkeypatch.setenv("YOUTUBE_INGEST_URL", "rtmps://a.rtmps.youtube.com/live2")
    monkeypatch.delenv("YOUTUBE_STREAM_KEY", raising=False)
    with pytest.raises(YoutubeConfigError):
        resolve_youtube_target(STREAM_YOUTUBE)

def test_rtmps_url_strips_a_trailing_slash_on_the_ingest_url():
    target = YoutubeTarget(ingest_url="rtmps://a.rtmps.youtube.com/live2/", stream_key="k")
    assert target.rtmps_url == "rtmps://a.rtmps.youtube.com/live2/k"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_youtube.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `YoutubeConfigError`, `YoutubeTarget`, `resolve_youtube_target` in `brewcam/video/youtube.py`**

```python
@dataclass
class YoutubeTarget:
    ingest_url: str
    stream_key: str

    @property
    def rtmps_url(self) -> str:
        return f"{self.ingest_url.rstrip('/')}/{self.stream_key}"


def resolve_youtube_target(stream_config: dict) -> YoutubeTarget | None:
    if stream_config.get("destination") != "youtube":
        return None
    ingest_url = os.environ.get(stream_config["ingest_url_env"])
    stream_key = os.environ.get(stream_config["key_env"])
    if not ingest_url or not stream_key:
        raise YoutubeConfigError("YouTube destination is configured but the ingestion URL or stream key is unset")
    return YoutubeTarget(ingest_url=ingest_url, stream_key=stream_key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_youtube.py -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for `redact_target` and `redact_stderr`**

```python
from brewcam.video.youtube import redact_target, redact_stderr

def test_redact_target_never_shows_the_key_or_host():
    target = YoutubeTarget(ingest_url="rtmps://a.rtmps.youtube.com/live2", stream_key="super-secret")
    redacted = redact_target(target)
    assert "super-secret" not in redacted
    assert "a.rtmps.youtube.com" not in redacted

def test_redact_target_handles_none():
    assert "not configured" in redact_target(None)

def test_redact_stderr_masks_rtsp_credentials():
    text = 'Input #0, rtsp, from "rtsp://wb:wyze-secret@wyze-bridge:8554/cam1":'
    redacted = redact_stderr(text)
    assert "wyze-secret" not in redacted

def test_redact_stderr_masks_rtmps_urls():
    text = "Opening 'rtmps://a.rtmps.youtube.com/live2/stream-key-value' for writing"
    redacted = redact_stderr(text)
    assert "stream-key-value" not in redacted
    assert "a.rtmps.youtube.com" not in redacted

def test_redact_stderr_leaves_ordinary_text_unchanged():
    text = "frame=  120 fps= 25 q=23.0 size=    512kB time=00:00:04.80 bitrate= 873.8kbits/s"
    assert redact_stderr(text) == text
```

- [ ] **Step 6: Run tests to verify they fail, then implement, then verify they pass**

Run: `pipenv run python -m pytest tests/test_youtube.py -v` (expect FAIL, then implement, then PASS).

`redact_target`: return a fixed `"rtmps://<redacted>"` string when `target is not None`, else `"rtmps://<not configured>"` — never a partial mask, since there's no legitimate reason to show any part of a YouTube URL in a log line.

`redact_stderr`: two `re.sub` passes — `re.sub(r"rtsp://[^/@\s]+@", "rtsp://***:***@", text)` (matches `camera.redact_url`'s userinfo-masking convention) followed by `re.sub(r"rtmps?://\S+", "rtmps://<redacted>", text)`. Regex-based (not literal-value matching) so it catches secrets in captured stderr regardless of which specific camera/YouTube values are in play this run — simpler and more defensive than threading the exact resolved values through every call site.

- [ ] **Step 7: Confirm `youtube.py` is ≤100 lines, then commit**

```bash
git add brewcam/video/youtube.py tests/test_youtube.py
git commit -m "feat: resolve YouTube ingestion target from env vars, with redaction"
```

---

### Task 2: Add the YouTube output leg to the compose command (`brewcam/video/filters.py`)

**Files:**
- Modify: `brewcam/video/filters.py`
- Modify: `tests/test_filters.py` (update the `-vf`-based test from increment 3; add new tests)

**Interfaces:**
- Consumes: `youtube.YoutubeTarget` (Task 1).
- Produces: `build_compose_command(display_source, rtsp_url, overlay_text_path, stream_config, snapshot_path, youtube_target: YoutubeTarget | None = None) -> list[str]` — same name/first five params as increment 3's version; the new sixth parameter is optional and defaults to today's JPEG-only behavior.

- [ ] **Step 1: Update increment 3's `test_filter_chain_scales_and_reloads_overlay_text` for the new filter mechanism**

This increment switches from a single-output `-vf` filter to a named `-filter_complex` pad (`[comp]`), because a second output needs to reuse the same filtered video — a plain `-vf` only feeds the implicit single output. This is an internal mechanism change, not a behavior change: the picture shown in the JPEG stays identical. Replace the existing assertions:

```python
def test_filter_chain_scales_and_reloads_overlay_text():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT)
    assert "-filter_complex" in cmd
    filter_graph = cmd[cmd.index("-filter_complex") + 1]
    assert "scale=1280:720" in filter_graph
    assert f"textfile={TEXT_PATH}" in filter_graph and "reload=1" in filter_graph
    assert "[comp]" in filter_graph
    assert cmd[cmd.index("-map") + 1] == "[comp]"
```

- [ ] **Step 2: Write failing tests for the new YouTube leg**

```python
from brewcam.video.youtube import YoutubeTarget

TARGET = YoutubeTarget(ingest_url="rtmps://a.rtmps.youtube.com/live2", stream_key="unique-marker-key")

def test_no_youtube_leg_when_target_is_none():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT)
    assert "anullsrc" not in " ".join(cmd)
    assert cmd[-1] == str(SNAPSHOT)

def test_youtube_leg_adds_silent_audio_and_required_encoder_settings():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT, youtube_target=TARGET)
    joined = " ".join(cmd)
    assert "anullsrc=channel_layout=stereo:sample_rate=44100" in joined
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-profile:v") + 1] == "main"
    assert cmd[cmd.index("-b:v") + 1] == "3000k"
    assert cmd[cmd.index("-minrate") + 1] == "3000k"
    assert cmd[cmd.index("-maxrate") + 1] == "3000k"
    assert cmd[cmd.index("-bufsize") + 1] == "6000k"
    assert cmd[cmd.index("-g") + 1] == "50"  # 2 * 25 fps
    assert cmd[cmd.index("-keyint_min") + 1] == "50"
    assert cmd[cmd.index("-sc_threshold") + 1] == "0"
    assert cmd[cmd.index("-bf") + 1] == "2"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-b:a") + 1] == "128k"
    assert cmd[cmd.index("-ar") + 1] == "44100"
    assert cmd[cmd.index("-ac") + 1] == "2"
    assert cmd[-2] == "-f" and cmd[-1] == TARGET.rtmps_url
    # the output leg's own "-f flv" must come after every lavfi input's own "-f lavfi"
    assert cmd.index("flv") > cmd.count("lavfi")

def test_youtube_leg_respects_configured_bitrate():
    stream = {**STREAM, "bitrate_kbps": 5000}
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, stream, SNAPSHOT, youtube_target=TARGET)
    assert cmd[cmd.index("-b:v") + 1] == "5000k"
    assert cmd[cmd.index("-bufsize") + 1] == "10000k"

def test_no_real_audio_device_ever_appears():
    for cmd in (
        build_compose_command({"mode": "camera"}, "rtsp://host/cam", TEXT_PATH, STREAM, SNAPSHOT, youtube_target=TARGET),
        build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT),
    ):
        joined = " ".join(cmd)
        for device_flag in ("avfoundation", "alsa", "dshow", "pulse"):
            assert device_flag not in joined

def test_stream_key_appears_exactly_once_in_the_whole_command():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT, youtube_target=TARGET)
    occurrences = sum(1 for arg in cmd if "unique-marker-key" in arg)
    assert occurrences == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_filters.py -v`
Expected: FAIL — `build_compose_command` doesn't accept `youtube_target` yet, filter mechanism still uses `-vf`.

- [ ] **Step 4: Implement the filter-complex switch and the optional YouTube leg in `build_compose_command`**

```python
def build_compose_command(display_source, rtsp_url, overlay_text_path, stream_config, snapshot_path, youtube_target=None):
    width, height, fps = stream_config["width"], stream_config["height"], stream_config["fps"]
    mode = display_source["mode"]
    command = ["ffmpeg"]
    if mode == "camera":
        command += ["-rtsp_transport", "tcp", "-timeout", "10000000", "-i", rtsp_url]
    elif mode == "image":
        command += ["-loop", "1", "-framerate", str(fps), "-i", display_source["path"]]
    else:
        command += ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}"]

    if youtube_target is not None:
        command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

    filter_graph = (
        f"[0:v]scale={width}:{height},"
        f"drawtext=textfile={overlay_text_path}:reload=1:fontcolor=white:fontsize=24:"
        "x=20:y=20:box=1:boxcolor=black@0.5[comp]"
    )
    command += ["-filter_complex", filter_graph]
    command += ["-map", "[comp]", "-an", "-pix_fmt", "yuv420p", "-r", "1/5", "-update", "1", "-y", str(snapshot_path)]

    if youtube_target is not None:
        bitrate = stream_config.get("bitrate_kbps", 3000)
        gop = str(2 * fps)
        command += [
            "-map", "[comp]", "-map", "1:a",
            "-c:v", "libx264", "-profile:v", "main", "-pix_fmt", "yuv420p",
            "-b:v", f"{bitrate}k", "-minrate", f"{bitrate}k", "-maxrate", f"{bitrate}k",
            "-bufsize", f"{bitrate * 2}k",
            "-g", gop, "-keyint_min", gop, "-sc_threshold", "0", "-bf", "2",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-f", "flv", youtube_target.rtmps_url,
        ]
    return command
```

Note the anullsrc input is declared right after the video input and before `-filter_complex` — FFmpeg needs every `-i` input declared before the outputs that reference it, and it becomes input index `1` only because there is always exactly one video input ahead of it, regardless of mode.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_filters.py -v`
Expected: PASS

- [ ] **Step 6: Confirm `filters.py` is still ≤100 lines, then commit**

```bash
git add brewcam/video/filters.py tests/test_filters.py
git commit -m "feat: add an optional YouTube RTMPS output leg to the compose command"
```

---

### Task 3: Wire YouTube resolution and redacted stderr capture into the supervisor

**Files:**
- Modify: `brewcam/video/starter.py`
- Modify: `brewcam/video/supervisor.py`
- Modify: `brewcam/video/state.py`
- Modify: `brewcam/video/video.py`
- Modify: `tests/test_video.py` (update existing process mocks; add new tests)

**Interfaces:**
- Consumes: `youtube.{resolve_youtube_target, redact_stderr, YoutubeConfigError}` (Task 1), `filters.build_compose_command`'s new `youtube_target` parameter (Task 2).
- Produces:
  - `VideoState` gains `youtube_error_logged: bool = False`.
  - `starter.start_camera_attempt(..., youtube_target=None)` and `starter.start_filler(..., youtube_target=None)` — both pass it through to `build_compose_command` and switch their `Popen` calls' `stderr` from `subprocess.DEVNULL` to `subprocess.PIPE`.
  - `video.tick(...)`'s return value gains a `"youtube"` key: `{"configured": bool, "leg_active": bool}`.

- [ ] **Step 1: Update every mocked `Popen`/process fixture in `tests/test_video.py` to include a `stderr` attribute**

Audit `tests/test_video.py` (both increment 2's and increment 3's tests) for every `MagicMock(poll=...)` standing in for a process — including the shared `_running_process()` helper — and add `stderr=MagicMock(read=MagicMock(return_value=b""))` to each. Without this, Step 4 below (which reads `process.stderr.read()` on exit) would call `.read()` on a bare `MagicMock` in every pre-existing test, silently returning another `MagicMock` instead of bytes — not a crash by itself, but it would then fail `monitoring.write_event`'s JSON serialization the first time any existing "process exited" test runs. This step has no new assertions of its own; it exists so Steps 2–5 don't appear to regress unrelated tests.

- [ ] **Step 2: Write failing tests for YouTube target resolution degrading gracefully**

```python
from brewcam.video.video import tick

STREAM_YOUTUBE = {**STREAM, "destination": "youtube", "ingest_url_env": "YOUTUBE_INGEST_URL", "key_env": "YOUTUBE_STREAM_KEY"}

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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: FAIL — `tick` doesn't resolve or report `youtube` yet.

- [ ] **Step 4: Implement YouTube resolution in `video.tick`, pass `youtube_target` through to `starter.start_camera_attempt`/`start_filler`, switch stderr capture on, and redact it in `supervisor.watch_running_process`**

In `video.tick`, before the existing branching logic, resolve the target:

```python
try:
    youtube_target = youtube.resolve_youtube_target(stream_config)
    state.youtube_error_logged = False
except youtube.YoutubeConfigError:
    youtube_target = None
    if not state.youtube_error_logged:
        monitoring.write_event(monitoring_dir, now_local, "youtube_unavailable", {}, retention_days)
        state.youtube_error_logged = True
```

Pass `youtube_target` into every call to `start_camera_attempt`/`start_filler`/`build_compose_command` already present from increment 3. In `starter.py`, change both `Popen(...)` calls from `stderr=subprocess.DEVNULL` to `stderr=subprocess.PIPE`.

In `supervisor.watch_running_process`, in the branch that already detects `state.process.poll() is not None` (the process exited), capture and redact stderr *before* `state.process` is set to `None`:

```python
stderr_bytes = state.process.stderr.read() if state.process.stderr else b""
stderr_text = youtube.redact_stderr(stderr_bytes.decode(errors="replace"))
excerpt = "\n".join(stderr_text.splitlines()[-20:])
```

Include `excerpt` in the `details` dict of whichever event this branch already logs (`camera_disconnected` / a new `streaming_failed` when a YouTube leg was active — see Task 5 for exactly which event name to use based on `leg_active`). Bounded to the last 20 lines, never the full buffer.

Return value gains: `"youtube": {"configured": youtube_target is not None, "leg_active": youtube_target is not None and state.process is not None}`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: PASS. If an old "process exited" test now fails on JSON serialization, it's missing the `stderr` mock from Step 1 — fix the fixture, not the production code.

- [ ] **Step 6: Confirm every touched file is still ≤100 lines (split stderr-capture into a small helper if `supervisor.py` overflows), then commit**

```bash
git add brewcam/video/starter.py brewcam/video/supervisor.py brewcam/video/state.py brewcam/video/video.py tests/test_video.py
git commit -m "feat: resolve YouTube target and capture redacted FFmpeg stderr on failure"
```

---

### Task 4: Config validation and example files

**Files:**
- Modify: `brewcam/config/validation.py`
- Modify: `brewcam/config/config.py`
- Modify: `.env.example`
- Modify: `config/session.example.yaml`
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Produces: `validate_youtube_stream(spec: dict) -> None` (raises `ConfigError`), called from `load_config` alongside `validate_stream_dimensions`.

- [ ] **Step 1: Write failing tests**

```python
from brewcam.config.validation import validate_youtube_stream

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_config.py -v`
Expected: FAIL — function not defined.

- [ ] **Step 3: Implement `validate_youtube_stream` in `validation.py`, call it from `load_config`**

No-op when `spec.get("destination") != "youtube"`. Otherwise: `ingest_url_env` and `key_env` must each be present and a non-empty `str`; `bitrate_kbps`, if present, must be a positive `int`. Call it in `config.py` alongside the existing `validate_stream_dimensions(config.stream)` call, inside the same `try` block.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Update `.env.example`**

Add `YOUTUBE_INGEST_URL=` directly above the existing `YOUTUBE_STREAM_KEY=` line, with a comment noting it's copied from YouTube Studio's "Stream URL" field (the RTMPS variant, revealed via the lock icon — plain RTMP is shown by default).

- [ ] **Step 6: Update `config/session.example.yaml`'s `stream` block**

Add `ingest_url_env: "YOUTUBE_INGEST_URL"` alongside the existing `key_env: "YOUTUBE_STREAM_KEY"`; optionally add a commented-out `# bitrate_kbps: 3000` showing the default is overridable.

- [ ] **Step 7: Commit**

```bash
git add brewcam/config/validation.py brewcam/config/config.py .env.example config/session.example.yaml tests/test_config.py
git commit -m "feat: validate YouTube stream config and document the new env vars"
```

---

### Task 5: Monitoring — streaming lifecycle events and the `youtube` snapshot field

**Files:**
- Modify: `brewcam/config/monitoring.py`
- Modify: `brewcam/video/supervisor.py` (event naming for the YouTube-leg-active case, building on Task 3's stderr capture)
- Test: `tests/test_monitoring.py` (append), `tests/test_video.py` (append)

**Interfaces:**
- Consumes: `video.tick`'s `"youtube"` status key (Task 3).
- Produces: `monitoring.build_snapshot_record` includes a `youtube` field; new event types `streaming_started`, `streaming_failed`, `streaming_retry`, `streaming_recovered`, `streaming_stopped` used alongside the existing camera event types.

- [ ] **Step 1: Write a failing test for the snapshot record's `youtube` field**

```python
def test_build_snapshot_record_includes_youtube_status():
    video_status = {"background_mode": "camera", "ffmpeg_running": True,
                     "youtube": {"configured": True, "leg_active": True}}
    record = build_snapshot_record(NOW, OVERLAY, video_status, TEMPERATURE)
    assert record["video"]["youtube"] == {"configured": True, "leg_active": True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pipenv run python -m pytest tests/test_monitoring.py -v`
Expected: FAIL — `build_snapshot_record` doesn't include the field yet.

- [ ] **Step 3: Add the `youtube` field to `build_snapshot_record`'s explicit allowlist**

Add `"youtube": video_status["youtube"]` inside the existing `"video": {...}` dict — field-by-field, matching the existing `background`/`ffmpeg_running` entries, not a passthrough of the whole `video_status` dict.

- [ ] **Step 4: Run test to verify it passes**

Run: `pipenv run python -m pytest tests/test_monitoring.py -v`
Expected: PASS

- [ ] **Step 5: Write a failing test distinguishing a YouTube-leg failure from a camera-only failure**

```python
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
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pipenv run python -m pytest tests/test_video.py -v`
Expected: FAIL — `watch_running_process` always logs `camera_disconnected`/`camera_stalled` regardless of whether a YouTube leg was active.

- [ ] **Step 7: In `supervisor.watch_running_process`, choose the event name based on whether a YouTube leg was active**

Where the function currently emits `camera_disconnected`/`camera_stalled` for a `running_source == {"mode": "camera"}` process, accept a `youtube_leg_was_active: bool` parameter (the caller — `video.tick`, from Task 3 — already knows this) and emit `streaming_failed` with the redacted stderr excerpt in `details` instead of `camera_disconnected`/`camera_stalled` when it's `True`; keep the existing camera-only event names when it's `False`. Follow the same failed attempt with a `streaming_retry` event carrying the computed backoff delay, and emit `streaming_recovered` the next time `status` transitions back to `"connected"` while a YouTube leg is configured (mirroring the existing `camera_connected` transition-only logging discipline — not every tick).

- [ ] **Step 8: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_video.py tests/test_monitoring.py -v`
Expected: PASS

- [ ] **Step 9: Write a failing test for the clean-shutdown event**

```python
def test_stop_logs_streaming_stopped_when_a_youtube_leg_was_active(tmp_path):
    process = MagicMock(poll=MagicMock(return_value=None), terminate=MagicMock(), wait=MagicMock())
    state = VideoState(process=process, running_source={"mode": "camera"})
    stop(state, youtube_leg_active=True, monitoring_dir=str(tmp_path / "logs"), now_local=NOW, retention_days=30)
    logged = "\n".join(p.read_text() for p in (tmp_path / "logs").glob("*.jsonl"))
    assert '"streaming_stopped"' in logged
```

- [ ] **Step 10: Extend `video.stop` to accept the monitoring parameters and log `streaming_stopped` when applicable, update its one call site in `main.py`**

New signature: `stop(state: VideoState, youtube_leg_active: bool = False, monitoring_dir: str | None = None, now_local: datetime | None = None, retention_days: int = 30) -> None` — all four new parameters default so `stop(state)` alone still behaves exactly as it does today (terminate, no event). `stop`'s existing termination logic is unchanged; only when `youtube_leg_active` is `True` (which implies `monitoring_dir`/`now_local` were passed), call `monitoring.write_event(monitoring_dir, now_local, "streaming_stopped", {}, retention_days)` after the process is terminated. Update `main.py`'s two existing call sites (the exception path and the normal shutdown path) to pass `video_state.running_source == {"mode": "camera"} and youtube.resolve_youtube_target(config.stream) is not None` (wrapped in the same degrade-on-`YoutubeConfigError`-to-`False` pattern as `tick`) for `youtube_leg_active`, plus `config.monitoring["directory"]`, the current `now_local`, and `config.monitoring["retention_days"]`.

- [ ] **Step 11: Run the full test suite, then commit**

Run: `pipenv run python -m pytest -v`
Expected: all PASS.

```bash
git add brewcam/config/monitoring.py brewcam/video/supervisor.py brewcam/video/video.py brewcam/main.py tests/test_monitoring.py tests/test_video.py
git commit -m "feat: distinguish streaming lifecycle events from camera-only events"
```

---

### Task 6: README documentation

**Files:**
- Modify: `README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add a "YouTube live publishing" section**

Cover, in this order: (1) the two required env vars (`YOUTUBE_INGEST_URL`, `YOUTUBE_STREAM_KEY`) and that the YouTube leg activates automatically once both are set and `stream.destination: youtube` is configured — no separate launch command; (2) exact launch/stop commands (unchanged: `docker compose up brewcam` / Ctrl+C); (3) how to get the ingestion URL and stream key from YouTube Studio — Go Live → Stream → click the lock icon next to "Stream URL" to reveal the RTMPS variant (plain RTMP is shown by default) → copy both the URL and the stream key; (4) the first-unlisted-broadcast checklist (see Step 2); (5) troubleshooting: a rejected connection (wrong/expired key, or RTMP copied instead of RTMPS — re-copy both fresh from Studio), encoding errors (check the redacted `ffmpeg` stderr excerpt already in the relevant JSONL event), and an interrupted stream (expect `streaming_failed` → `streaming_retry` with growing backoff in the log, and a real gap in the Studio-side viewer — this is expected, not a bug, per "do not claim uninterrupted playback during reconnection").

- [ ] **Step 2: Write the first-unlisted-broadcast checklist**

1. Create an unlisted broadcast in YouTube Studio; copy its RTMPS ingestion URL and stream key into `.env`.
2. Start Brewcam; confirm the local JPEG preview still updates.
3. In Studio's stream health panel, confirm YouTube reports it is receiving video; open the unlisted broadcast URL and confirm picture and overlay look right.
4. Briefly interrupt the stream (stop/restart Brewcam, or network access) and confirm the JSONL log shows `streaming_failed` → `streaming_retry` → `streaming_recovered`, and that Studio shows a real gap, not seamless playback.
5. Stop Brewcam with Ctrl+C; confirm the broadcast ends cleanly in Studio and no FFmpeg process is left running.

- [ ] **Step 3: Update the "Status" section**

State plainly that YouTube publishing is implemented and locally verified (command construction, secret redaction, retry behavior — see Task 7 for the local-output check), but the real YouTube end-to-end test is the one item still pending the user's own YouTube Studio activation — do not claim YouTube playback has been confirmed.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document YouTube live publishing setup, launch, and troubleshooting"
```

---

### Task 7: Local output verification without touching YouTube

**Files:**
- Test: `tests/test_youtube_integration.py` (new file — real local `ffmpeg`, no network to `*.youtube.com`)

**Interfaces:** none new — exercises Tasks 1–2's real output end-to-end.

- [ ] **Step 1: Write a test that runs the real compose command with a local file standing in for the RTMPS destination**

```python
import subprocess
import time
from brewcam.video.filters import build_compose_command
from brewcam.video.overlay_text import write_overlay_text
from brewcam.video.youtube import YoutubeTarget

def test_youtube_encoding_leg_produces_a_valid_flv_locally(tmp_path):
    overlay_path = tmp_path / "overlay.txt"
    write_overlay_text(overlay_path, "Brassin maison\nFermentation\n19.0C")
    snapshot_path = tmp_path / "snap.jpg"
    local_target = YoutubeTarget(ingest_url=f"file://{tmp_path}/local_output", stream_key="out.flv")
    command = build_compose_command({"mode": "black"}, None, overlay_path,
                                     {"width": 320, "height": 240, "fps": 10}, snapshot_path,
                                     youtube_target=local_target)
    process = subprocess.Popen(command + ["-t", "2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process.wait(timeout=10)
    output_path = tmp_path / "local_output" / "out.flv"
    assert output_path.exists() and output_path.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,codec_type",
         "-of", "csv=p=0", str(output_path)],
        capture_output=True, text=True, check=True,
    )
    assert "h264" in probe.stdout
    assert "aac" in probe.stdout
```

`YoutubeTarget.rtmps_url` assembles `f"{ingest_url.rstrip('/')}/{stream_key}"`, so a `file://` ingest URL plus an `out.flv` key produces a plain local file path ffmpeg can write to with `-f flv` — this proves the encoding parameters (H.264/AAC/FLV, resolution, GOP, CBR) are mutually compatible and produce a real playable stream, without ever resolving or connecting to `*.youtube.com`. The `-t 2` flag (append after the built command) caps the run to 2 seconds since `black` mode would otherwise run forever.

Skip this test (via `pytest.mark.skipif`) when `ffmpeg`/`ffprobe` aren't on `PATH`, matching increment 3's synthetic-source test.

- [ ] **Step 2: Run the test to verify it passes (or fix a real `filters.py` bug if it doesn't — this is not a stub to skip)**

Run: `pipenv run python -m pytest tests/test_youtube_integration.py -v`
Expected: PASS.

- [ ] **Step 3: Run the complete test suite one final time**

Run: `pipenv run python -m pytest -v`
Expected: all tests PASS, including every test from increment 3 and this plan.

- [ ] **Step 4: Commit**

```bash
git add tests/test_youtube_integration.py
git commit -m "test: verify the YouTube encoding leg locally with a file-based stand-in target"
```
