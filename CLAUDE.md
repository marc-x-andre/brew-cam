# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup (if `pipenv install --dev` appears to install nothing, a known quirk
# in some environments, fall back to `pipenv sync --dev`)
pipenv install --dev

# Run the app
pipenv run python -m brewcam --config config/session.example.yaml

# Run the full test suite
pipenv run python -m pytest

# Run a single test file / test
pipenv run python -m pytest tests/test_schedule.py
pipenv run python -m pytest tests/test_schedule.py::test_mid_step_progress -v
```

**Always use the `python -m` form** (`pipenv run python -m pytest`, `pipenv run python -m brewcam`), never bare `pipenv run pytest`. The package uses a flat layout (`brewcam/` at the repo root, no `src/`) and isn't installed anywhere — only the `-m` form puts the repo root on `sys.path` so `import brewcam` resolves. Bare `pipenv run pytest` fails with `ModuleNotFoundError`.

## Architecture

Data flows one way through the main loop (`brewcam/main.py`): YAML config → schedule state → sensor reading → overlay data → monitoring log. Each stage is a separate module so any one of them can be swapped (e.g. a real sensor for the simulated one) without touching the others.

- `brewcam/config/` — config-and-observability concerns grouped together: `config.py` (YAML → `BrewcamConfig` dataclasses, via `load_config`), `validation.py` (pure semantic validators: step sequencing, timezone-offset-vs-DST consistency, background-mode validity), `monitoring.py` (JSONL snapshot/event record builders). `brewcam/config/__init__.py` re-exports the public config API (`load_config`, `Step`, `SessionConfig`, `BrewcamConfig`, `ConfigError`) so other modules import from `brewcam.config` rather than reaching into `brewcam.config.config`.
- `brewcam/schedule.py` — pure functions computing the current stage and progress from `(steps, now)`. No clock reads, no persisted state: restart-safety falls out of purity (resuming after a restart is just `load_config(path)` + the current wall clock).
- `brewcam/sensors/` — a shared reading contract (`SensorReading`, `classify_reading`) in `__init__.py`, with `simulated.py` (a live demo driver) and `thermopro.py` (a stub — the real ThermoPro Bluetooth model isn't confirmed yet, don't invent the protocol) as interchangeable drivers.
- `brewcam/overlay.py` — composes display data from config + schedule + sensor snapshots only. It must never import from camera/video/youtube modules; that's what lets a new overlay element be added without touching those.
- `brewcam/utils/` — reserved for genuinely cross-cutting helpers only: `clock.py` (injectable `utc_now()`) and `jsonl.py` (timezone-agnostic append/rotate/retention). Business rules never live here.
- `brewcam/video/` (not created yet) — future home for `camera.py`, `video.py`, `filters.py`, `youtube.py` once hardware/streaming integration starts. Don't add stub files for these before they're actually needed.

## Project-specific conventions

- **Every `.py` file stays at or under 100 lines.** Split into a new module rather than compressing code to fit.
- **Pure functions for schedule and validation logic.** No I/O, no clock reads inside `schedule.py` or `validation.py` — `now`/paths/directories are always passed in as arguments.
- **One `ConfigError` for every malformed-config case.** `load_config` must never let a raw `KeyError`/`TypeError`/`ValueError`/`yaml.YAMLError`/`zoneinfo.ZoneInfoNotFoundError` escape — a bad timestamp, an unknown background mode, a missing step field, or an empty step list should all surface as `ConfigError`.
- **JSONL record builders use an explicit named-field allowlist, never a dict/object passthrough** (no `**some_dict`, no `**overlay.__dict__`). This is the mechanism that guarantees a secret can never leak into a log line, even if one shows up in an unrelated caller-supplied dict.
- **Secrets stay indirected through `*_env` config fields** (env var *names*, never values) until the module that actually needs the value is implemented. Don't call `os.environ` for a feature that isn't built yet, and never log a resolved secret.
- **TDD:** write the failing test, run it, implement the minimal code to pass, run it again, commit.
- **No premature abstraction:** no base classes, factories, or design patterns unless they solve a concrete current need; no HTTP server or plugin system.
- **Never hard-code production stage names or their count** — everything about the schedule comes from the YAML config, re-read fresh on every process start.
- **Pipenv only** (not pip/poetry/uv) for dependency management. `Pipfile.lock` must always come from a real `pipenv install --dev` resolution — never hand-edit or fabricate it.
