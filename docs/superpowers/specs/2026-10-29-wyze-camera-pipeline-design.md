# Brewcam Increment 2 (partial) — Wyze Camera Pipeline Design

> Scope note: this covers the camera-input half of increment 2 only. Video
> compositing (`filters.py`, overlay burn-in) and YouTube publishing
> (`youtube.py`) are explicitly deferred to a later increment — see
> Non-goals.

## Intent and why

Increment 1 delivered a hardware-free vertical slice: config, schedule,
simulated sensors, overlay *data* (not video), and JSONL monitoring, all
driven by a 1-second terminal loop. No camera, FFmpeg, or video code exists
yet — `brewcam/video/` was deliberately not created.

The project charter's confirmed scope is Wyze Cam V2 → YouTube with no room
audio. Docker Wyze Bridge is the chosen way to get Wyze's proprietary
protocol down to a plain RTSP stream, so Brewcam itself never has to speak
Wyze's protocol or hold Wyze account credentials. This increment makes that
RTSP feed real and observable, without yet touching video compositing or
YouTube.

**Why now:** the camera is physically set up and the user has already added
Wyze API credentials to `.env`/`.env.example`, so this is the first point
where hardware-backed verification (not just mocks) is possible.

## Target flow (this increment's slice)

```
Wyze Cam V2 → Docker Wyze Bridge (container) → RTSP → Brewcam/FFmpeg (container) → local JPEG snapshot for UAT
```

YouTube is the eventual destination (see charter) but is out of scope here.

## Decisions made in conversation (theirs)

- Both Brewcam and Docker Wyze Bridge run in Docker, on the same host —
  containerizing Brewcam itself (new `Dockerfile`) is in scope.
- YouTube publishing and FFmpeg-level video compositing (drawtext overlays,
  background-image/black fallback baked into frames) are **out of scope**
  for this increment.
- Local UAT visualization: a single refreshing JPEG snapshot file
  (`ffmpeg -update 1`), not rotating video segments or a local re-stream
  server — chosen for minimal moving parts.
- Wyze API credentials (`WYZE_KEY_ID`, `WYZE_API_KEY`) are already present
  in `.env`/`.env.example` (user's own naming — see Config below). Wyze
  account email/password were deliberately left out; the bridge's web UI
  login flow covers that instead.
- The camera is physically running; real end-to-end verification against
  it is expected, but must happen in the user's own terminal — this
  session has no `docker` CLI available, so I cannot pull images or bring
  up containers myself.

## Goals

- A new `docker-compose.yml` running Docker Wyze Bridge, pinned to a
  verified real tag (`mrlt8/wyze-bridge:2.10.3`), reachable from the
  `brewcam` service by service name over a shared Docker network.
- A new `Dockerfile` for Brewcam (Python 3.15 + `ffmpeg` + Pipenv deps).
- A new `brewcam/video/` package that resolves the RTSP source from the
  existing `camera.source_env` config convention, drives an FFmpeg
  subprocess reading that RTSP stream over TCP with audio stripped, and
  writes a continuously-refreshed JPEG snapshot for manual UAT.
- Supervision: detect camera unavailability, a dropped connection, and a
  stalled FFmpeg input (process alive but no new frames); retry with
  capped backoff; recover automatically when the camera returns.
- A camera outage must never stop the sensor-reading / schedule / snapshot
  loop that already runs every second.
- Clean shutdown on Ctrl+C: FFmpeg is terminated, not orphaned.
- Camera connect/disconnect/stall/recovery events land in the existing
  daily JSONL log via the existing `monitoring.write_event`, with RTSP
  URLs and any embedded credentials redacted before they ever reach a log
  line or an exception message.
- README sections per the original ask (prereqs, start/stop bridge, auth,
  finding the real RTSP path, independent stream testing, Brewcam launch,
  host-vs-Docker addressing, troubleshooting).

## Non-goals / anti-goals

- No FFmpeg-level video compositing (`filters.py`), no overlay burn-in
  onto frames, no background-image/black-fallback video switching. The
  existing `overlay.py` (text/data only) is untouched.
- No YouTube publishing (`youtube.py`), no RTMP output, no stream key
  handling.
- No change to calendar/schedule, sensor (ThermoPro), or config-validation
  logic beyond what's needed to resolve the camera source.
- No WebRTC/RTMP/HLS bridge ports published — Brewcam only needs RTSP.
- Would be a failure even if "working": leaking the RTSP URL (which can
  carry bridge auth) or Wyze credentials into logs, git, or exception
  text; exposing the bridge beyond loopback; silently swallowing a camera
  outage in a way that also stops sensor/monitoring; fabricating a
  "hardware verified" claim without the user actually confirming the
  snapshot shows the real camera.

## Constraints carried over from the repo

- Flat `brewcam/` package layout (no `src/`), Python files ≤100 lines,
  short functions, no new abstractions beyond what this needs.
- `brewcam/video/` must not be imported by `overlay.py`, `schedule.py`, or
  sensor code (independence already enforced by an AST-based test for
  `overlay.py`; the same property should hold here by construction).
- JSONL records built field-by-field (no `**dict` passthrough) — same
  pattern as `monitoring.py` today.
- `pipenv run python -m pytest` (not bare `pytest`) for the flat layout.
- Secrets never committed; `.env` already gitignored.

## Architecture

### Docker Compose topology

Two services on one user-defined bridge network (e.g. `brewcam-net`):

- **`wyze-bridge`**: image `mrlt8/wyze-bridge:2.10.3` (confirmed current
  release on Docker Hub as of this writing — re-verify before long-term
  pinning). Ports published to `127.0.0.1` only:
  - `127.0.0.1:8554:8554` (RTSP) — so the user can `ffplay` it directly
    from the host for independent testing.
  - `127.0.0.1:5050:5000` (web UI / API) — for bridge management and
    finding the real camera stream path.
  No WebRTC, RTMP, or HLS ports published (unused). No Cloudflare Tunnel,
  no `0.0.0.0` bindings. Environment: `WB_AUTH=true`, `WB_USERNAME`,
  `WB_PASSWORD` (bridge web UI login), `API_ID=${WYZE_KEY_ID}`,
  `API_KEY=${WYZE_API_KEY}` (Wyze account API credentials, already in
  `.env`/`.env.example`), and `WB_API=${WYZE_API_KEY}` — the bridge's
  own REST/stream auth key, deliberately set to the *same* value as the
  Wyze account API key per the user's choice (one fewer secret to manage;
  the tradeoff is that rotating `WYZE_API_KEY` also changes the RTSP
  stream password). All sourced from `.env` via Compose variable
  substitution, never written into the compose file itself.
  `WYZE_EMAIL`/`WYZE_PASSWORD` stay commented out (the user logs into the
  bridge's web UI instead, consistent with what's already in
  `.env.example`).

  **Correction from builder-check:** `WB_AUTH=true` gates the RTSP
  stream itself, not just the web UI — confirmed against the bridge's
  Authentication wiki page. The built-in `wb` stream user accepts
  `WB_API` as its password, so Brewcam's `CAMERA_SOURCE` must embed
  credentials: `rtsp://wb:${WYZE_API_KEY}@wyze-bridge:8554/<path>`. This
  makes `redact_url` (see `camera.py` below) load-bearing from the first
  run, not just a defensive safety net.

- **`brewcam`**: built from a new root `Dockerfile`. Joins the same
  network; reaches the bridge at
  `rtsp://wb:${WYZE_API_KEY}@wyze-bridge:8554/<camera-path>`.
  No ports need publishing (Brewcam has no HTTP server). Mounts
  `config/`, `runtime/`, `logs/`, `assets/` as volumes so the snapshot
  JPEG and JSONL logs are visible/openable from the host.

### Brewcam Dockerfile

`python:3.15-slim` (or closest available tag) base, `apt-get install
ffmpeg`, `pipenv install --deploy --ignore-pipfile`, copy the package.

**Correction from builder-check:** a hardcoded `CMD` pointing at
`config/session.example.yaml` would mean the only way to ever run a real
brew session in the container is to edit the Dockerfile. Instead, use the
shell form so it reads an overridable env var at container start:
`CMD python -m brewcam --config "${BREWCAM_CONFIG:-config/session.example.yaml}"`.
No Python code changes needed — `docker-compose.yml` can set
`BREWCAM_CONFIG` per environment, defaulting to the example config for
this increment's UAT.

### `brewcam/video/camera.py`

- `resolve_rtsp_url(camera_config: dict) -> str`: reads
  `os.environ[camera_config["source_env"]]` (reusing the existing
  `camera.source_env` convention from `config.py` — no config schema
  change needed); raises a clear, redaction-safe error if unset.
- `redact_url(url: str) -> str`: masks userinfo (`user:pass@`) and any
  `token`/`key` query parameters, for safe use in log messages and
  exception text.
- Does not know anything about Wyze's protocol — it only ever sees the
  plain RTSP URL the bridge exposes, kept out of `overlay.py`/sensor code.

### `brewcam/video/video.py` — FFmpeg supervisor

- `build_snapshot_command(rtsp_url: str, snapshot_path: Path) -> list[str]`:
  `ffmpeg -rtsp_transport tcp -rw_timeout 10000000 -i <url> -an -vf
  fps=1/5 -update 1 -y <snapshot_path>` (TCP transport, audio stream
  dropped, one refreshed JPEG every 5s — builder's call, revisit if 5s
  feels too slow/fast).

  **Correction from builder-check:** without a read timeout, a reachable
  camera that never answers the RTSP handshake would hang in
  `"connecting"` forever — the stall check only looks at the snapshot
  file, which doesn't exist until a first frame lands. `-rw_timeout
  10000000` (10s, microseconds) makes a dead handshake fail fast into the
  existing retry/backoff path instead.
- A small supervisor (function(s), not a class hierarchy — no factories)
  holding: the `Popen` handle, last-known status
  (`"unavailable" | "connecting" | "connected" | "stalled"`), consecutive
  failure count, and next-allowed-restart time.
- `tick(now) -> VideoStatus`: called once per existing main-loop iteration
  (no new thread/async — fits the current single-threaded 1s-tick design).
  - If no process and backoff timer elapsed: attempt to resolve the URL
    and start FFmpeg; missing env var → `"unavailable"`, logged once per
    transition, not every tick.
  - If running: check the snapshot file's mtime against a stall timeout
    (builder's call: 15s); no progress → kill the process (next tick's
    restart logic takes over).
  - If the process exited: increment failure count, schedule next retry
    with capped exponential backoff (2s → 4s → 8s → … → 60s cap —
    builder's call); reset the backoff after a sustained healthy period
    (builder's call: 60s connected) so a one-off blip doesn't leave future
    reconnects artificially slow.
  - Every *status transition* (not every tick) calls the existing
    `monitoring.write_event(..., event_type, details)` with a redacted
    message — e.g. `"camera_unavailable"`, `"camera_connected"`,
    `"camera_stalled"`, `"camera_reconnecting"`.
- `stop()`: terminate (SIGTERM) then kill after a short grace period —
  called from `main.py`'s existing shutdown path so FFmpeg never gets
  orphaned on Ctrl+C.
- All exceptions from subprocess start/poll are caught inside this
  module — a camera failure must never propagate into `main.py`'s loop.

### `main.py` integration

Replace the hardcoded module-level `VIDEO_STATUS` with a supervisor
instance created once in `run()`; call `supervisor.tick(now)` each
iteration and pass its resulting `{"background_mode": ..., "ffmpeg_running":
...}`-shaped dict into the existing `monitoring.write_snapshot` call
unchanged (same field allowlist as today — no new monitoring
abstraction). `background_mode` reports `"camera"` when connected and
`"black"` when not (matches the charter's recommended camera-failure
fallback label; no actual video compositing yet, so this is a status
label only, not a frame). Add `supervisor.stop()` to the existing
shutdown path (after the loop, and in the exception/finally path) so an
unhandled error still cleans up FFmpeg.

### Config / env

No `BrewcamConfig` schema change — `camera.source_env` already exists
from increment 1. Update `.env.example`'s existing `CAMERA_SOURCE` line
(building on what the user already added):
```
CAMERA_SOURCE=rtsp://wb:REPLACE_WITH_YOUR_WYZE_API_KEY@wyze-bridge:8554/REPLACE_WITH_CAMERA_PATH_FROM_BRIDGE_UI
```
`CAMERA_SOURCE` stays one flat literal string, same as increment 1 — no
`${...}` shell-style substitution inside `.env` itself (plain `.env`
files don't expand that). In the user's real (gitignored) `.env`, both
placeholders get replaced by hand: their actual `WYZE_API_KEY` value
copied into the URL, and the real stream path confirmed via the bridge's
web UI, never assumed from the camera's display name. This also means
`docker-compose.yml` does not need to assemble the URL itself — it just
passes `CAMERA_SOURCE` straight through to the `brewcam` container.

For host-vs-Docker addressing (a documentation requirement): the only
difference is the hostname in `CAMERA_SOURCE` — `wyze-bridge` (service
name) when Brewcam runs in the Compose network, or `127.0.0.1` (the
published loopback port) if Brewcam were ever run directly on the host
instead.

### Observability

- Distinguishes *process status* (`ffmpeg_running`, already in the
  existing snapshot shape) from *observed progress* (the supervisor's
  internal stall check against snapshot mtime) — a running-but-stalled
  FFmpeg is reported as `"stalled"` in event details, not silently shown
  as healthy.
- Never claims YouTube playback is verified — out of scope this
  increment anyway, but the design intentionally has nothing that could
  be mistaken for that claim.
- Redaction: `redact_url` applied anywhere an RTSP URL could reach a log
  line, an exception message, or a monitoring `details` dict.

## Testing approach

- Unit tests (no real FFmpeg, no real camera, no Docker needed):
  - `camera.py`: URL resolution from env, missing-env-var error,
    redaction of userinfo/token query params.
  - `video.py` command construction: asserts `-rtsp_transport tcp` and
    `-an` are present, RTSP URL is wired in correctly.
  - Supervisor retry/backoff/stall logic against a mocked `subprocess.Popen`
    and a fake clock — covers unavailable → connecting → connected →
    stalled → reconnecting transitions without touching real processes.
  - Monitoring: confirms camera events reach the JSONL allowlist pattern
    and that a redacted URL never contains the raw secret/token.
- Docker Compose: validated structurally (YAML parses, service/network
  names consistent) — this session cannot run `docker compose config` or
  pull images (no `docker` CLI available here), so actually bringing up
  the stack and confirming the real RTSP feed is a manual step for the
  user, documented in the README with exact commands.
- No real YouTube broadcast is started (out of scope anyway).

## What's left to the builder

- Exact snapshot interval (proposed 5s), stall timeout (proposed 15s),
  backoff schedule (proposed 2s→60s cap, doubling) and healthy-reset
  window (proposed 60s) — reasonable defaults, not hardware-validated
  yet; easy to tune as constants in `video.py` without a design change.
- Exact base image tag for the Brewcam `Dockerfile` (`python:3.15-slim`
  assumed — confirm it exists on Docker Hub when implementing).
- Whether `WB_USERNAME`/`WB_PASSWORD` get added to `.env`/`.env.example`
  now or the user sets them through the bridge web UI on first run
  (either works with `WB_AUTH=true`; defaulting to web-UI-set since no
  bridge password is in `.env` yet).

## Manual verification checklist (for the user, after implementation)

1. `docker compose up wyze-bridge`, open `http://127.0.0.1:5050`, confirm
   the camera appears, note its real stream path.
2. `ffplay "rtsp://wb:$WYZE_API_KEY@127.0.0.1:8554/<path>"` from the host
   — confirms the bridge→camera hop and auth independently of Brewcam.
3. Set `CAMERA_SOURCE` in `.env` to the real
   `rtsp://wb:<your WYZE_API_KEY value>@wyze-bridge:8554/<path>`
   (service-name form, for the Brewcam container — see Config/env above).
4. `docker compose up brewcam`, watch `runtime/<snapshot>.jpg` update,
   confirm it's really the camera.
5. Unplug/power off the camera (or stop the bridge) mid-run — confirm
   logs show the expected transition events and the main loop keeps
   writing sensor snapshots; restore it and confirm reconnection.
