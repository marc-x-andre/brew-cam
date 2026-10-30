# Brewcam Increment 1 — Hardware-Free Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Brewcam repo scaffold and a hardware-free vertical slice — YAML config + validation, pure schedule/progress math, a simulated temperature sensor, overlay composition, and daily JSONL monitoring — wired into a terminal loop with clean Ctrl+C shutdown. No FFmpeg, no camera, no YouTube, no real Bluetooth.

**Architecture:** One Python package (`brewcam`) at the repository root — flat layout, no `src/` wrapper, per python-guide.org ("the library does not belong in an ambiguous src ... subdirectory") and Dagster's default — organized by responsibility. Config-related modules (`config.py`, `validation.py`, `monitoring.py`) live under `brewcam/config/`; camera/video/streaming modules (`camera.py`, `video.py`, `filters.py`, `youtube.py` — all deferred to increment 2) will live under `brewcam/video/`; `schedule.py` and `overlay.py` stay at the package root as cross-cutting domain logic the other groups depend on. `brewcam/config/validation.py` holds pure, dependency-free semantic checks (step sequencing, timezone-offset consistency, background-mode validity) operating on primitives (datetimes, dicts) so it can be built and tested before `config.py` exists. `brewcam/config/config.py` does YAML parsing into dataclasses and calls into `validation.py`, raising a single `ConfigError`; `brewcam/config/__init__.py` re-exports `ConfigError`, `Step`, `SessionConfig`, `BrewcamConfig`, and `load_config` so other modules import from `brewcam.config` rather than the stuttering `brewcam.config.config`. `schedule.py` is pure functions from `(steps, now)` to stage/progress state — no persisted state, so restart-safety falls out of purity. `sensors/` defines a shared reading contract (`SensorReading`, staleness classification) with `simulated.py` (live) and `thermopro.py` (stub) adapters. `overlay.py` composes display data from config + schedule + sensor snapshots only — it has no dependency on camera/video/youtube, which is what lets a new overlay element be added without touching those modules. `brewcam/config/monitoring.py` + `utils/jsonl.py` write daily-rotated JSONL via an explicit field allowlist (never a blind dict dump), which is what guarantees secrets can't leak into logs. `main.py`/`__main__.py` wire these into a ~1s loop with a `SIGINT` handler.

**Tech Stack:** Python 3.15 (Homebrew, `/opt/homebrew/bin/python3.15`), Pipenv 2026.10.4, PyYAML 6.0.3, pytest 9.2.0. No other runtime dependencies.

**Spec:** `docs/brewcam_chart.docx` (French project charter — source of truth for scope and behavior) plus the decisions recorded in this plan where the charter leaves an implementation detail open.

## Global Constraints

- Every `.py` file stays at or under 100 lines; no artificial compression to hit that — split further if a file would need padding or cramming to fit.
- Schedule and validation logic must be pure functions (no I/O, no clock reads inside them — `now`/paths are always passed in).
- `utils/` holds only `clock.py` (injectable current time) and `jsonl.py` (generic append/rotate/retention) — no business rules there.
- Never hard-code stage names or stage count; everything comes from the YAML at `config_path`, re-read fresh on every process start.
- No persisted runtime/schedule state. Resumption after restart is purely `load_config(path)` + current wall clock.
- Step timestamps must carry an explicit UTC offset (reject naive datetimes) and that offset must match the configured IANA timezone at that instant (catches wrong DST offsets).
- Steps must be sorted by `start`, contiguous (`steps[i].end == steps[i+1].start`), non-overlapping, and each `end > start`.
- A step is active on `[start, end)`. `stage_progress = clamp((now - start) / (end - start) * 100, 0, 100)`. `overall_progress` spans `steps[0].start` to `steps[-1].end`, same clamp.
- Background mode must be one of `{"camera", "image", "black"}`; `mode: image` requires an existing file at its `path`, resolved relative to the current working directory (the charter's documented run command is always invoked from the repo root).
- `*_env` fields (`camera.source_env`, `stream.key_env`) are carried through config as the **env var name string only** — this increment never calls `os.environ` for them, since nothing yet consumes a camera or YouTube secret. Actual resolution is deferred to the increment that implements `camera.py`/`youtube.py`.
- JSONL record builders use an explicit field allowlist, never `**dict` passthrough of caller-supplied state, so no accidental field can carry a secret into a log line.
- Daily log files roll over at local-midnight in the configured IANA timezone, named `logs/YYYY-MM-DD.jsonl` (local date), not UTC date.
- Flat layout (no `src/`) means the `brewcam` package isn't installed anywhere — it's only importable because the repo root is on `sys.path`. Bare `pipenv run pytest` does **not** put it there (verified empirically: it fails with `ModuleNotFoundError: No module named 'brewcam'`), so every test run and ad-hoc import check in this plan uses `pipenv run python -m pytest` / `pipenv run python -c "..."` / `pipenv run python -m brewcam`, whose `-m`/`-c` forms add the current directory to `sys.path` themselves. This is a deliberate deviation from the charter's literal `pipenv run pytest` README command, forced by dropping `src/`.
- Out of scope for this increment, not stubbed: `brewcam/video/camera.py`, `brewcam/video/video.py`, `brewcam/video/filters.py`, `brewcam/video/youtube.py` (the `brewcam/video/` package itself isn't created until increment 2, same reasoning), `tests/test_video.py`, the real ThermoPro Bluetooth protocol, FFmpeg stall-detection/restart, disk-full handling. Nothing in this increment imports them, so no placeholder files are created for them (avoids dead scaffolding).

## Review Focus

- **Contiguity, not just overlap:** a gap between `steps[i].end` and `steps[i+1].start` must be rejected exactly like an overlap — easy to implement only the overlap check and let a gap silently pass. Covered in Task 2.
- **Offset-vs-zone consistency across DST:** a syntactically valid UTC-offset timestamp that doesn't match the configured IANA zone's actual offset at that instant (e.g., `-05:00` during Toronto's DST window) must be rejected, not just "has an offset." Covered in Task 2.
- **Stale vs. unavailable must stay distinct:** a reading older than `stale_after_seconds` is a different status than "no reading at all" — collapsing them loses the "never show stale data as current" guarantee. Covered in Task 5.
- **No secret leakage via an unexpected field:** a record builder that forwards any part of an input dict verbatim can leak a secret the moment one shows up in `video_status` or elsewhere — the allowlist behavior must be proven, not assumed. Covered in Task 8.
- **Log rotation keyed on local date, not UTC date:** a naive rollover check using `datetime.utcnow().date()` rolls over at the wrong wall-clock moment for any non-UTC-offset timezone (e.g. America/Toronto at 23:30 local is still "today" locally but already "tomorrow" UTC during EST/EDT only near midnight UTC — the test must use a local time close to local midnight, not UTC midnight). Covered in Task 7.

---

### Task 1: Repo scaffold and Pipenv environment

**Files:**
- Create: `Pipfile`
- Create: `.env.example`
- Create: `config/session.example.yaml`
- Create: `assets/standby.png`
- Create: `brewcam/__init__.py`
- Create: `brewcam/config/__init__.py` (empty for now — Task 3 adds re-exports once `config.py` exists)
- Create: `brewcam/sensors/__init__.py` (empty for now, filled in Task 5)
- Create: `brewcam/utils/__init__.py` (empty)
- Create: `tests/__init__.py` if needed for test discovery (pytest doesn't require it; skip unless `pipenv run python -m pytest` fails to collect without it)

**Interfaces:**
- Produces: a `pipenv`-managed virtualenv on Python 3.15 that later tasks install into; `config/session.example.yaml` as the fixture later tasks' manual runs load; `assets/standby.png` as the file `validate_background_mode` tests check existence against. Note `config/` (repo-root, YAML fixtures) and `brewcam/config/` (the Python subpackage) are deliberately distinct directories at different nesting levels.

- [ ] **Step 1: Create the package directories**

```bash
mkdir -p brewcam/config brewcam/sensors brewcam/utils tests config assets runtime logs
touch brewcam/__init__.py brewcam/config/__init__.py brewcam/sensors/__init__.py brewcam/utils/__init__.py
```

- [ ] **Step 2: Write `Pipfile`**

```toml
[[source]]
url = "https://pypi.org/simple"
verify_ssl = true
name = "pypi"

[packages]
pyyaml = "*"

[dev-packages]
pytest = "*"

[requires]
python_version = "3.15"
```

- [ ] **Step 3: Install the environment against Python 3.15 and verify resolution**

Run: `PIPENV_PYTHON=/opt/homebrew/bin/python3.15 pipenv install --dev`
Expected: completes successfully and writes a real `Pipfile.lock` (resolved, not hand-written).

- [ ] **Step 4: Write `.env.example`**

```
# Copy to .env (never commit .env). Values here are variable NAMES referenced
# from config YAML, not the secrets themselves.
CAMERA_SOURCE=
YOUTUBE_STREAM_KEY=
```

- [ ] **Step 5: Write `config/session.example.yaml`**

Use the charter's own example verbatim (session/steps/camera/sensors/stream/overlays/monitoring blocks, including the `"Empâtage"`/`"Fermentation"` steps with their absolute timestamps), since the charter already fully specifies this fixture.

- [ ] **Step 6: Generate `assets/standby.png`**

A minimal valid 64x64 black PNG, built with the standard library only (no new dependency for an asset):

```python
import struct, zlib

def write_png(path, width=64, height=64):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\x00\x00\x00" * width
    raw = row * height
    idat = zlib.compress(raw)
    with open(path, "wb") as f:
        f.write(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))

write_png("assets/standby.png")
```

Run this once (e.g. `pipenv run python -c "<script above>"`) to produce the committed binary file.

- [ ] **Step 7: Verify the scaffold**

Run: `pipenv run python -c "import brewcam"` and `pipenv run python -m pytest`
Expected: import succeeds; pytest reports "no tests ran" (0 collected, exit code 5) without error.

- [ ] **Step 8: Commit**

```bash
git add Pipfile Pipfile.lock .env.example config/session.example.yaml assets/standby.png brewcam tests
git commit -m "chore: scaffold brewcam package and Pipenv environment"
```

---

### Task 2: Config validation rules (`validation.py`)

**Files:**
- Create: `brewcam/config/validation.py`
- Test: `tests/test_config.py` (validation-only tests; config-loading tests are added in Task 3 in the same file)

**Interfaces:**
- Produces:
  - `class ConfigError(Exception)`
  - `validate_step_sequence(spans: list[tuple[datetime, datetime]]) -> None`
  - `validate_timezone_offset(moment: datetime, tz_name: str) -> None`
  - `validate_background_mode(spec: dict, assets_dir: Path = Path.cwd()) -> None`
- Consumes: nothing from other Brewcam modules (deliberately decoupled from `config.py`'s dataclasses to avoid a circular import — operates on plain tuples/datetimes/dicts).

- [ ] **Step 1: Write failing tests for `validate_step_sequence`**

```python
from datetime import datetime, timezone
import pytest
from brewcam.config.validation import ConfigError, validate_step_sequence

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_config.py -v`
Expected: FAIL — `validation.py` does not exist yet.

- [ ] **Step 3: Implement `ConfigError` and `validate_step_sequence` in `brewcam/config/validation.py`**

`validate_step_sequence` iterates consecutive pairs; raises `ConfigError` with a message naming the offending span if `end <= start`, or if `spans[i][1] != spans[i+1][0]` (covers both gap and overlap, since contiguity requires exact equality).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for `validate_timezone_offset`**

```python
from datetime import datetime, timedelta, timezone
from brewcam.config.validation import validate_timezone_offset

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
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_config.py -v`
Expected: FAIL — function not defined.

- [ ] **Step 7: Implement `validate_timezone_offset` in `brewcam/config/validation.py`**

Use `zoneinfo.ZoneInfo(tz_name)`: compute `moment.astimezone(ZoneInfo(tz_name)).utcoffset()` and compare to `moment.utcoffset()`; raise `ConfigError` on mismatch.

- [ ] **Step 8: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 9: Write failing tests for `validate_background_mode`**

```python
from pathlib import Path
from brewcam.config.validation import validate_background_mode

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
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_config.py -v`
Expected: FAIL — function not defined.

- [ ] **Step 11: Implement `validate_background_mode` in `brewcam/config/validation.py`**

Raise `ConfigError` if `spec["mode"]` not in `{"camera", "image", "black"}`; if `"image"`, raise `ConfigError` unless `(assets_dir / spec["path"]).is_file()`.

- [ ] **Step 12: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 13: Confirm `validation.py` is at or under 100 lines, then commit**

```bash
git add brewcam/config/validation.py tests/test_config.py
git commit -m "feat: add pure config validation rules"
```

---

### Task 3: Config loading (`config.py`)

**Files:**
- Create: `brewcam/config/config.py`
- Modify: `brewcam/config/__init__.py` (add re-exports)
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Consumes: `brewcam.config.validation.{ConfigError, validate_step_sequence, validate_timezone_offset, validate_background_mode}` (Task 2).
- Produces:
  - `@dataclass(frozen=True) class Step: name: str; description: str; start: datetime; end: datetime; target_temperature_c: float | None; background: dict | None`
  - `@dataclass(frozen=True) class SessionConfig: name: str; timezone: str; background: dict; before: dict; after: dict`
  - `@dataclass(frozen=True) class BrewcamConfig: session: SessionConfig; steps: list[Step]; camera: dict; sensors: dict; stream: dict; overlays: list[str]; monitoring: dict`
  - `load_config(path: str) -> BrewcamConfig` (raises `ConfigError`, re-exported from `brewcam.config` for convenience)

- [ ] **Step 1: Write a failing test for the happy path**

```python
import textwrap
from brewcam.config import load_config

VALID_YAML = textwrap.dedent("""
session:
  name: "Test Brew"
  timezone: "America/Toronto"
  background: {mode: black}
  before: {mode: black}
  after: {mode: black}
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
  key_env: "YOUTUBE_STREAM_KEY"
  width: 1280
  height: 720
  fps: 25
overlays:
  enabled: [session, temperature, stage, progress]
monitoring:
  interval_seconds: 60
  directory: logs
  retention_days: 30
""")

def test_load_config_happy_path(tmp_path):
    cfg_path = tmp_path / "session.yaml"
    cfg_path.write_text(VALID_YAML)
    config = load_config(str(cfg_path))
    assert config.session.name == "Test Brew"
    assert len(config.steps) == 1
    assert config.steps[0].name == "Mash"
    assert config.camera["source_env"] == "CAMERA_SOURCE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pipenv run python -m pytest tests/test_config.py::test_load_config_happy_path -v`
Expected: FAIL — `ImportError: cannot import name 'load_config' from 'brewcam.config'` (the package exists from Task 1, but `config.py` and the re-export don't yet).

- [ ] **Step 3: Implement the dataclasses and `load_config` in `brewcam/config/config.py`, then re-export from `brewcam/config/__init__.py`**

`load_config` reads the YAML with `yaml.safe_load`, builds each `Step` by parsing `start`/`end` with `datetime.fromisoformat` (raising `ConfigError` if the result is naive — `tzinfo is None`), passes the parsed spans through `validate_step_sequence`, validates each step's and the session's background dicts through `validate_background_mode`, and validates every step boundary plus session timezone through `validate_timezone_offset` (imported from `.validation` with a relative import, since both live in the same `brewcam/config/` package). `camera`, `sensors`, `stream`, `monitoring` are passed through as plain dicts (no secret resolution — see Global Constraints). Catch `KeyError`/`TypeError` from malformed YAML and re-raise as `ConfigError` with a descriptive message. Add `from .config import ConfigError, Step, SessionConfig, BrewcamConfig, load_config` to `brewcam/config/__init__.py` so callers elsewhere use `from brewcam.config import ...` rather than reaching into `brewcam.config.config`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pipenv run python -m pytest tests/test_config.py::test_load_config_happy_path -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for each rejected case**

```python
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

def test_unknown_background_mode_rejected(tmp_path):
    bad = VALID_YAML.replace("background: {mode: black}", "background: {mode: bogus}")
    with pytest.raises(ConfigError):
        _load(tmp_path, bad)
```

(Import `ConfigError` from `brewcam.config` at the top of the test file alongside `load_config`.)

- [ ] **Step 6: Run tests to verify they fail or pass as expected**

Run: `pipenv run python -m pytest tests/test_config.py -v`
Expected: the four new tests PASS immediately if Step 3's implementation already routes every case through the Task 2 validators; if any fails, it means that code path in `load_config` isn't calling the corresponding validator — fix `load_config`, not the test.

- [ ] **Step 7: Re-run the full test file and commit**

Run: `pipenv run python -m pytest tests/test_config.py -v`
Expected: all PASS

```bash
git add brewcam/config/config.py tests/test_config.py
git commit -m "feat: load and validate session YAML into BrewcamConfig"
```

---

### Task 4: Schedule and progress math (`schedule.py`)

**Files:**
- Create: `brewcam/utils/clock.py`
- Create: `brewcam/schedule.py`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Consumes: `brewcam.config.Step` (Task 3) — only the `.start`/`.end` attributes.
- Produces:
  - `utc_now() -> datetime` in `utils/clock.py`
  - `@dataclass(frozen=True) class StageState: stage: Step | None; status: str; stage_progress_pct: float; overall_progress_pct: float` — `status` is one of `"upcoming" | "active" | "done"`
  - `compute_schedule_state(steps: list[Step], now: datetime) -> StageState`

- [ ] **Step 1: Implement `utc_now` in `brewcam/utils/clock.py`**

```python
from datetime import datetime, timezone

def utc_now() -> datetime:
    return datetime.now(timezone.utc)
```

No test needed for a one-line stdlib wrapper; it exists purely so callers can inject a fixed `now` in tests instead of calling it.

- [ ] **Step 2: Write failing tests for `compute_schedule_state`**

```python
from datetime import datetime, timezone
from brewcam.config import Step
from brewcam.schedule import compute_schedule_state

def _step(name, h1, h2):
    return Step(name, "d", datetime(2026, 11, 7, h1, tzinfo=timezone.utc),
                datetime(2026, 11, 7, h2, tzinfo=timezone.utc), None, None)

STEPS = [_step("A", 9, 10), _step("B", 10, 12)]

def test_before_first_step_is_upcoming():
    state = compute_schedule_state(STEPS, datetime(2026, 11, 7, 8, tzinfo=timezone.utc))
    assert state.status == "upcoming" and state.stage is None
    assert state.stage_progress_pct == 0 and state.overall_progress_pct == 0

def test_exact_start_boundary_is_active_at_zero_percent():
    state = compute_schedule_state(STEPS, datetime(2026, 11, 7, 9, tzinfo=timezone.utc))
    assert state.status == "active" and state.stage.name == "A"
    assert state.stage_progress_pct == 0

def test_mid_step_progress():
    state = compute_schedule_state(STEPS, datetime(2026, 11, 7, 9, 30, tzinfo=timezone.utc))
    assert state.stage.name == "A" and state.stage_progress_pct == 50

def test_exact_end_boundary_rolls_to_next_step():
    state = compute_schedule_state(STEPS, datetime(2026, 11, 7, 10, tzinfo=timezone.utc))
    assert state.status == "active" and state.stage.name == "B"
    assert state.stage_progress_pct == 0

def test_after_last_step_is_done():
    state = compute_schedule_state(STEPS, datetime(2026, 11, 7, 13, tzinfo=timezone.utc))
    assert state.status == "done" and state.stage is None
    assert state.stage_progress_pct == 100 and state.overall_progress_pct == 100

def test_recompute_from_scratch_is_stateless():
    # calling twice with the same inputs, with no shared state between calls,
    # proves resumption after a restart needs nothing but (steps, now)
    first = compute_schedule_state(STEPS, datetime(2026, 11, 7, 9, 30, tzinfo=timezone.utc))
    second = compute_schedule_state(STEPS, datetime(2026, 11, 7, 9, 30, tzinfo=timezone.utc))
    assert first == second
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_schedule.py -v`
Expected: FAIL — `brewcam.schedule` does not exist.

- [ ] **Step 4: Implement `StageState` and `compute_schedule_state` in `brewcam/schedule.py`**

`stage_progress_pct` for an active/upcoming/done state uses `clamp((now - start) / (end - start) * 100, 0, 100)` against the relevant step (or 0/100 for upcoming/done without a stage). `overall_progress_pct` always uses `clamp((now - steps[0].start) / (steps[-1].end - steps[0].start) * 100, 0, 100)`. Find the active step with `next((s for s in steps if s.start <= now < s.end), None)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_schedule.py -v`
Expected: PASS

- [ ] **Step 6: Write a failing test for a DST-crossing timezone scenario**

```python
from datetime import timedelta, timezone as tz
from brewcam.config import Step

def test_schedule_handles_dst_crossing_steps():
    # America/Toronto falls back from EDT (-4) to EST (-5) on 2026-11-01.
    # These steps are tagged with their correct local offsets; schedule.py
    # must compare them correctly in UTC regardless of the offset change.
    before_fallback = Step("Before", "d",
        datetime(2026, 11, 1, 1, 0, tzinfo=tz(timedelta(hours=-4))),
        datetime(2026, 11, 1, 1, 30, tzinfo=tz(timedelta(hours=-4))), None, None)
    after_fallback = Step("After", "d",
        datetime(2026, 11, 1, 1, 30, tzinfo=tz(timedelta(hours=-4))),
        datetime(2026, 11, 1, 1, 30, tzinfo=tz(timedelta(hours=-5))), None, None)
    steps = [before_fallback, after_fallback]
    now = datetime(2026, 11, 1, 1, 15, tzinfo=tz(timedelta(hours=-5)))  # inside "After" in UTC terms
    state = compute_schedule_state(steps, now)
    assert state.stage.name == "After"
```

- [ ] **Step 7: Run test to verify it fails or passes**

Run: `pipenv run python -m pytest tests/test_schedule.py::test_schedule_handles_dst_crossing_steps -v`
Expected: PASS without code changes, since Python's aware-datetime comparison already normalizes to UTC — this test exists to pin that guarantee against regression, not to drive new code.

- [ ] **Step 8: Confirm `schedule.py` is at or under 100 lines, then commit**

```bash
git add brewcam/utils/clock.py brewcam/schedule.py tests/test_schedule.py
git commit -m "feat: add pure schedule and progress calculations"
```

---

### Task 5: Sensor contract and simulated/stub adapters

**Files:**
- Create: `brewcam/sensors/__init__.py` (contract, replacing the empty file from Task 1)
- Create: `brewcam/sensors/simulated.py`
- Create: `brewcam/sensors/thermopro.py`
- Test: `tests/test_overlay.py` (new file; sensor and overlay tests share it per the charter's test-file list)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class SensorReading: sensor_id: str; value: float | None; unit: str; measured_at: datetime | None; status: str` — `status` is one of `"available" | "stale" | "unavailable"`
  - `classify_reading(sensor_id: str, value: float | None, unit: str, measured_at: datetime | None, now: datetime, stale_after_seconds: int) -> SensorReading`
  - `simulated.read_temperature(now: datetime, stale_after_seconds: int) -> SensorReading`
  - `thermopro.read_temperature(now: datetime, stale_after_seconds: int) -> SensorReading` (raises `NotImplementedError`)

- [ ] **Step 1: Write failing tests for `classify_reading`**

```python
from datetime import datetime, timedelta, timezone
from brewcam.sensors import classify_reading

NOW = datetime(2026, 11, 7, 9, 30, tzinfo=timezone.utc)

def test_fresh_reading_is_available():
    r = classify_reading("temp", 20.0, "C", NOW, NOW, stale_after_seconds=120)
    assert r.status == "available"

def test_old_reading_is_stale_not_unavailable():
    measured = NOW - timedelta(seconds=300)
    r = classify_reading("temp", 20.0, "C", measured, NOW, stale_after_seconds=120)
    assert r.status == "stale"

def test_missing_value_is_unavailable():
    r = classify_reading("temp", None, "C", None, NOW, stale_after_seconds=120)
    assert r.status == "unavailable"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_overlay.py -v`
Expected: FAIL — `brewcam.sensors` has no `classify_reading`.

- [ ] **Step 3: Implement `SensorReading` and `classify_reading` in `brewcam/sensors/__init__.py`**

`"unavailable"` if `value is None or measured_at is None`; else `"stale"` if `(now - measured_at).total_seconds() > stale_after_seconds`; else `"available"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_overlay.py -v`
Expected: PASS

- [ ] **Step 5: Implement `simulated.read_temperature` in `brewcam/sensors/simulated.py`**

A deterministic, always-fresh demo signal (no test required — it's a display convenience, not a contract): e.g. `20.0 + 2.0 * math.sin(now.timestamp() / 300)`, `unit="C"`, `measured_at=now`, run through `classify_reading`.

- [ ] **Step 6: Write a failing test for the ThermoPro stub**

```python
import pytest
from brewcam.sensors import thermopro

def test_thermopro_driver_not_yet_implemented():
    with pytest.raises(NotImplementedError):
        thermopro.read_temperature(NOW, stale_after_seconds=120)
```

- [ ] **Step 7: Run test to verify it fails**

Run: `pipenv run python -m pytest tests/test_overlay.py::test_thermopro_driver_not_yet_implemented -v`
Expected: FAIL — module does not exist.

- [ ] **Step 8: Implement `brewcam/sensors/thermopro.py`**

```python
from datetime import datetime
from brewcam.sensors import SensorReading

def read_temperature(now: datetime, stale_after_seconds: int) -> SensorReading:
    raise NotImplementedError(
        "ThermoPro Bluetooth driver pending exact model confirmation"
    )
```

- [ ] **Step 9: Run test to verify it passes, then commit**

Run: `pipenv run python -m pytest tests/test_overlay.py -v`
Expected: PASS

```bash
git add brewcam/sensors tests/test_overlay.py
git commit -m "feat: add sensor reading contract with simulated and stub drivers"
```

---

### Task 6: Overlay composition (`overlay.py`)

**Files:**
- Create: `brewcam/overlay.py`
- Test: `tests/test_overlay.py` (append)

**Interfaces:**
- Consumes: `brewcam.config.BrewcamConfig` (Task 3), `brewcam.schedule.StageState` (Task 4), `brewcam.sensors.SensorReading` (Task 5).
- Produces: `@dataclass(frozen=True) class OverlayData: session_name: str; stage_name: str; stage_description: str; status_label: str; stage_progress_pct: float; overall_progress_pct: float; temperature_display: str` and `build_overlay(config: BrewcamConfig, schedule_state: StageState, temperature: SensorReading) -> OverlayData`.

- [ ] **Step 1: Write failing tests for `build_overlay`**

```python
from brewcam.overlay import build_overlay
from brewcam.sensors import SensorReading

def test_build_overlay_active_stage_with_available_temperature():
    config = load_config(...)  # reuse the VALID_YAML fixture from test_config via a local tmp file, or inline an equivalent BrewcamConfig
    state = compute_schedule_state(config.steps, config.steps[0].start)
    reading = SensorReading("temp", 20.0, "C", config.steps[0].start, "available")
    overlay = build_overlay(config, state, reading)
    assert overlay.session_name == config.session.name
    assert overlay.stage_name == "Mash"
    assert overlay.temperature_display == "20.0C"
    assert overlay.status_label == "active"

def test_build_overlay_shows_unavailable_temperature():
    reading = SensorReading("temp", None, "C", None, "unavailable")
    overlay = build_overlay(config, state, reading)
    assert overlay.temperature_display == "Unavailable"

def test_build_overlay_before_first_step_has_no_stage_name():
    state = compute_schedule_state(config.steps, config.steps[0].start - timedelta(hours=1))
    overlay = build_overlay(config, state, reading)
    assert overlay.status_label == "upcoming"
    assert overlay.stage_name == ""
```

(Build `config`/`state`/`reading` fixtures the same way Task 3's and Task 4's tests do, via a local `tmp_path` YAML file and `compute_schedule_state`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_overlay.py -v`
Expected: FAIL — `brewcam.overlay` does not exist.

- [ ] **Step 3: Implement `OverlayData` and `build_overlay` in `brewcam/overlay.py`**

`stage_name`/`stage_description` come from `schedule_state.stage` if not `None`, else `""`. `status_label` is `schedule_state.status` verbatim. `temperature_display` is `f"{temperature.value}{temperature.unit}"` if `temperature.status == "available"`, else `"Unavailable"` (stale is also shown as `"Unavailable"` per the charter's "a stale reading must not appear as current" rule — there is no separate stale-display string required). This module must import only from `brewcam.config`, `brewcam.schedule`, and `brewcam.sensors` — never `brewcam.camera`, `brewcam.video`, or `brewcam.youtube`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_overlay.py -v`
Expected: PASS

- [ ] **Step 5: Write a failing test proving the camera/youtube independence guarantee**

```python
import ast
from pathlib import Path

def test_overlay_module_has_no_camera_or_youtube_dependency():
    source = Path("brewcam/overlay.py").read_text()
    tree = ast.parse(source)
    imported = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert not any(name and ("camera" in name or "youtube" in name or "video" in name) for name in imported)
```

- [ ] **Step 6: Run test to verify it passes, then commit**

Run: `pipenv run python -m pytest tests/test_overlay.py -v`
Expected: PASS (it should already pass given Step 3's implementation; this test only guards against a future regression)

```bash
git add brewcam/overlay.py tests/test_overlay.py
git commit -m "feat: compose overlay data independent of camera/video/youtube"
```

---

### Task 7: JSONL append, rotation, and retention (`utils/jsonl.py`)

**Files:**
- Create: `brewcam/utils/jsonl.py`
- Test: `tests/test_monitoring.py`

**Interfaces:**
- Produces:
  - `daily_log_path(directory: str, day: date) -> Path`
  - `append_jsonl(directory: str, day: date, record: dict) -> None`
  - `enforce_retention(directory: str, today: date, retention_days: int) -> None`

- [ ] **Step 1: Write failing tests for `daily_log_path` and `append_jsonl`**

```python
import json
from datetime import date
from brewcam.utils.jsonl import daily_log_path, append_jsonl

def test_daily_log_path_names_file_by_date(tmp_path):
    path = daily_log_path(str(tmp_path), date(2026, 11, 7))
    assert path == tmp_path / "2026-11-07.jsonl"

def test_append_jsonl_writes_one_line_per_call(tmp_path):
    append_jsonl(str(tmp_path), date(2026, 11, 7), {"a": 1})
    append_jsonl(str(tmp_path), date(2026, 11, 7), {"a": 2})
    lines = (tmp_path / "2026-11-07.jsonl").read_text().splitlines()
    assert [json.loads(l) for l in lines] == [{"a": 1}, {"a": 2}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_monitoring.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `daily_log_path` and `append_jsonl` in `brewcam/utils/jsonl.py`**

`append_jsonl` opens `daily_log_path(directory, day)` in append mode (creating `directory` with `Path(directory).mkdir(parents=True, exist_ok=True)` first) and writes `json.dumps(record) + "\n"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pipenv run python -m pytest tests/test_monitoring.py -v`
Expected: PASS

- [ ] **Step 5: Write a failing test for local-date rollover (the Review Focus item)**

```python
from zoneinfo import ZoneInfo
from datetime import datetime

def test_rollover_uses_local_date_not_utc_date(tmp_path):
    # 2026-11-07 23:30 America/Toronto (-05:00) is 2026-11-08 04:30 UTC.
    # The log file must be named by the LOCAL date, 2026-11-07.
    local_moment = datetime(2026, 11, 7, 23, 30, tzinfo=ZoneInfo("America/Toronto"))
    append_jsonl(str(tmp_path), local_moment.date(), {"a": 1})
    assert (tmp_path / "2026-11-07.jsonl").exists()
    assert not (tmp_path / "2026-11-08.jsonl").exists()
```

This pins that callers must pass the **local** `date` (callers are responsible for converting `now` to the configured timezone before calling `append_jsonl` — `jsonl.py` itself stays timezone-agnostic and just uses whatever `date` it's given).

- [ ] **Step 6: Run test to verify it passes**

Run: `pipenv run python -m pytest tests/test_monitoring.py -v`
Expected: PASS without further code changes (the module already takes `day` as a parameter); this test exists to pin the contract against a future regression where someone calls it with `datetime.utcnow().date()`.

- [ ] **Step 7: Write failing tests for `enforce_retention`**

```python
from brewcam.utils.jsonl import enforce_retention

def test_retention_deletes_files_older_than_window(tmp_path):
    (tmp_path / "2026-10-01.jsonl").write_text("")
    (tmp_path / "2026-11-05.jsonl").write_text("")
    (tmp_path / "2026-11-07.jsonl").write_text("")
    enforce_retention(str(tmp_path), today=date(2026, 11, 7), retention_days=30)
    remaining = {p.name for p in tmp_path.iterdir()}
    assert remaining == {"2026-11-05.jsonl", "2026-11-07.jsonl"}
```

- [ ] **Step 8: Run test to verify it fails**

Run: `pipenv run python -m pytest tests/test_monitoring.py -v`
Expected: FAIL — function not defined.

- [ ] **Step 9: Implement `enforce_retention` in `brewcam/utils/jsonl.py`**

For each `*.jsonl` file in `directory`, parse its `YYYY-MM-DD` stem as a date; delete it if `(today - file_date).days >= retention_days`.

- [ ] **Step 10: Run tests to verify they pass, confirm file size, then commit**

Run: `pipenv run python -m pytest tests/test_monitoring.py -v`
Expected: PASS

```bash
git add brewcam/utils/jsonl.py tests/test_monitoring.py
git commit -m "feat: add JSONL append, local-date rollover, and retention"
```

---

### Task 8: Monitoring records (`monitoring.py`)

**Files:**
- Create: `brewcam/config/monitoring.py`
- Test: `tests/test_monitoring.py` (append)

**Interfaces:**
- Consumes: `brewcam.overlay.OverlayData` (Task 6), `brewcam.utils.jsonl.append_jsonl` + `enforce_retention` (Task 7).
- Produces:
  - `SCHEMA_VERSION: int = 1`
  - `build_snapshot_record(now: datetime, overlay: OverlayData, video_status: dict) -> dict`
  - `build_event_record(now: datetime, event_type: str, details: dict) -> dict`
  - `write_snapshot(directory: str, now_local: datetime, overlay: OverlayData, video_status: dict, retention_days: int) -> None`
  - `write_event(directory: str, now_local: datetime, event_type: str, details: dict, retention_days: int) -> None`

- [ ] **Step 1: Write a failing test for `build_snapshot_record`'s shape**

```python
from datetime import datetime, timezone
from brewcam.config.monitoring import build_snapshot_record, SCHEMA_VERSION
from brewcam.overlay import OverlayData

NOW = datetime(2026, 11, 7, 9, 20, tzinfo=timezone.utc)
OVERLAY = OverlayData("Test Brew", "Mash", "desc", "active", 10.0, 5.0, "20.0C")

def test_build_snapshot_record_shape():
    record = build_snapshot_record(NOW, OVERLAY, {"background_mode": "black", "ffmpeg_running": False})
    assert record["event_type"] == "snapshot"
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["session"]["stage"] == "Mash"
    assert record["session"]["progress_pct"] == 10.0
    assert record["video"] == {"background": "black", "ffmpeg_running": False}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pipenv run python -m pytest tests/test_monitoring.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `SCHEMA_VERSION` and `build_snapshot_record` in `brewcam/config/monitoring.py`**

Build the dict explicitly, field by field, from `overlay` and the two named `video_status` keys (`background_mode`, `ffmpeg_running`) — never `**video_status`. Shape:
```python
{
    "timestamp": now.isoformat(),
    "event_type": "snapshot",
    "schema_version": SCHEMA_VERSION,
    "session": {"stage": overlay.stage_name, "progress_pct": overlay.stage_progress_pct},
    "video": {"background": video_status["background_mode"], "ffmpeg_running": video_status["ffmpeg_running"]},
    "sensors": {"temperature": {"display": overlay.temperature_display}},
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pipenv run python -m pytest tests/test_monitoring.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing secret-allowlist test (Review Focus item)**

```python
def test_snapshot_record_ignores_unexpected_fields_that_could_carry_secrets():
    leaky_status = {"background_mode": "black", "ffmpeg_running": False, "stream_key": "super-secret"}
    record = build_snapshot_record(NOW, OVERLAY, leaky_status)
    assert "super-secret" not in str(record)
    assert "stream_key" not in record["video"]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pipenv run python -m pytest tests/test_monitoring.py -v`
Expected: PASS without changes, since Step 3 already builds the dict via named-key access rather than passthrough — this test pins that guarantee against a future regression (e.g. someone adding `**video_status`).

- [ ] **Step 7: Write a failing test for `build_event_record`**

```python
def test_build_event_record_shape():
    record = build_event_record(NOW, "shutdown", {"reason": "sigint"})
    assert record["event_type"] == "shutdown"
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["details"] == {"reason": "sigint"}
```

- [ ] **Step 8: Run test to verify it fails, implement `build_event_record`, run again to verify it passes**

Run: `pipenv run python -m pytest tests/test_monitoring.py -v`
Expected: FAIL then PASS. Implementation: `{"timestamp": now.isoformat(), "event_type": event_type, "schema_version": SCHEMA_VERSION, "details": details}`.

- [ ] **Step 9: Write failing tests for `write_snapshot`/`write_event` wiring**

```python
import json
from brewcam.config.monitoring import write_snapshot, write_event

def test_write_snapshot_appends_to_todays_file(tmp_path):
    write_snapshot(str(tmp_path), NOW, OVERLAY, {"background_mode": "black", "ffmpeg_running": False}, retention_days=30)
    lines = (tmp_path / "2026-11-07.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["event_type"] == "snapshot"

def test_write_event_appends_immediately(tmp_path):
    write_event(str(tmp_path), NOW, "error", {"message": "sensor timeout"}, retention_days=30)
    lines = (tmp_path / "2026-11-07.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["event_type"] == "error"
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `pipenv run python -m pytest tests/test_monitoring.py -v`
Expected: FAIL — functions not defined.

- [ ] **Step 11: Implement `write_snapshot` and `write_event` in `brewcam/config/monitoring.py`**

Each builds its record (Steps 3/8), calls `jsonl.append_jsonl(directory, now_local.date(), record)`, then `jsonl.enforce_retention(directory, now_local.date(), retention_days)`.

- [ ] **Step 12: Run tests to verify they pass, confirm `monitoring.py` is ≤100 lines, then commit**

Run: `pipenv run python -m pytest tests/test_monitoring.py -v`
Expected: PASS

```bash
git add brewcam/config/monitoring.py tests/test_monitoring.py
git commit -m "feat: write allowlisted snapshot and event records to daily JSONL"
```

---

### Task 9: Main loop, entry point, and README

**Files:**
- Create: `brewcam/main.py`
- Create: `brewcam/__main__.py`
- Modify: `README.md` (create if it doesn't already exist as a stub)

**Interfaces:**
- Consumes: `brewcam.config.load_config` (Task 3), `brewcam.schedule.compute_schedule_state` (Task 4), `brewcam.sensors.simulated.read_temperature` (Task 5), `brewcam.overlay.build_overlay` (Task 6), `brewcam.config.monitoring.{write_snapshot, write_event}` (Task 8), `brewcam.utils.clock.utc_now` (Task 4).
- Produces: `run(config_path: str) -> None` in `main.py`; a `python -m brewcam --config <path>` CLI via `__main__.py`.

- [ ] **Step 1: Implement `run(config_path: str) -> None` in `brewcam/main.py`**

Loads config once via `load_config(config_path)`. Installs a `signal.SIGINT` handler that flips a `stop` flag. Loop body (while not stopped, `time.sleep(1)` at the end of each iteration): compute `now = utc_now()`; `state = compute_schedule_state(config.steps, now)`; `reading = simulated.read_temperature(now, config.sensors["temperature"]["stale_after_seconds"])`; `overlay = build_overlay(config, state, reading)`; convert `now` to the configured timezone (`now.astimezone(ZoneInfo(config.session.timezone))`) for use as `now_local` in monitoring calls; track the last snapshot time and call `write_snapshot(...)` once `config.monitoring["interval_seconds"]` have elapsed since the last one (or on the very first iteration). On loop exit, call `write_event(config.monitoring["directory"], now_local, "shutdown", {}, config.monitoring["retention_days"])`. No FFmpeg, no camera, no YouTube calls in this increment — `video_status` passed to `write_snapshot` is a fixed placeholder `{"background_mode": "black", "ffmpeg_running": False}`.

- [ ] **Step 2: Implement `brewcam/__main__.py`**

```python
import argparse
from brewcam.main import run

def main():
    parser = argparse.ArgumentParser(prog="brewcam")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    run(args.config)

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Manually verify the loop starts, logs, and shuts down cleanly**

Run: `pipenv run python -m brewcam --config config/session.example.yaml`, let it run a few seconds, then press Ctrl+C.
Expected: process exits without a traceback; `logs/<today>.jsonl` exists and its last line has `"event_type": "shutdown"`; an earlier line has `"event_type": "snapshot"`.

- [ ] **Step 4: Run the full test suite**

Run: `pipenv run python -m pytest -v`
Expected: all tests from Tasks 2–8 PASS.

- [ ] **Step 5: Write `README.md` documenting only what was just verified**

Cover: prerequisites (Python 3.15 via Homebrew, Pipenv), `pipenv install --dev`, `pipenv run python -m brewcam --config config/session.example.yaml`, `pipenv run python -m pytest`, and a short note that FFmpeg/camera/YouTube/ThermoPro integration is not yet implemented (increment 2+) — do not describe streaming or hardware behavior that hasn't been built. Also note that `pipenv run python -m pytest` (not bare `pipenv run pytest`) is required for the flat layout to resolve imports.

- [ ] **Step 6: Commit**

```bash
git add brewcam/main.py brewcam/__main__.py README.md
git commit -m "feat: wire config, schedule, sensors, overlay, and monitoring into a terminal loop"
```
