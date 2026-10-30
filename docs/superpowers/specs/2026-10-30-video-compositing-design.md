# Brewcam Increment 3 — Video Compositing & Overlay Burn-in Design

> Scope note: this is the first half of the two-part split agreed with the
> user for "YouTube live streaming." It builds the compositing pipeline
> that the camera-pipeline design (increment 2) deliberately deferred —
> continuous video, background-mode switching, overlay burn-in — using
> only the local JPEG preview as output. YouTube/RTMPS publishing is a
> separate increment (see
> `2026-10-30-youtube-live-publishing-design.md`) that reuses this
> increment's compositing pipeline rather than building its own.

## Intent and why

Increment 2 shipped RTSP ingestion with a periodic single-JPEG snapshot
(`ffmpeg -vf fps=1/5 -update 1`), and explicitly deferred "video
compositing (filters.py, overlay burn-in) and YouTube publishing
(youtube.py) ... to a later increment." That deferred work turns out to be
a prerequisite for YouTube, not an add-on to it: a continuous destination
can't be fed a once-every-5-seconds still image, and the original ask's
requirement to "preserve overlays and camera/image/black background
selection" assumes a compositing step that doesn't exist — `overlay.py`
only ever produced a data structure (session name, stage, temperature,
progress), never pixels, and the `background: {mode: ...}` config already
validated in `session.example.yaml` (camera / image / black, per step and
for session/before/after) has never been read by any video code. This
increment builds that missing middle, verified locally, so increment 4 can
add a YouTube output leg onto an already-working pipeline instead of
building compositing and publishing at the same time.

**Why now:** decided in conversation — splitting lets the compositing
pipeline (the larger unknown, since nothing like it exists yet) be built
and verified on its own before YouTube activation (external, ~24h
pending) is even available, instead of blocking on both at once.

## Target flow (this increment's slice)

```
Wyze Cam V2 → Docker Wyze Bridge → RTSP ─┐
assets/standby.png (image mode) ─────────┼→ FFmpeg: scale to one
synthetic black (lavfi color) ───────────┘   resolution + drawtext
                                              overlay burn-in
                                                    │
                                                    ▼
                                    runtime/camera_preview.jpg
                                    (refreshed local JPEG, same
                                     artifact as today — now shows
                                     the actual composited frame)
```

No YouTube, no RTMP/RTMPS, no new destination. The deliverable is: one
continuously-running FFmpeg process that always has *some* valid video
source (never a bare failure), burns the current overlay text onto every
frame, and keeps exporting that composited result as the existing JPEG
preview artifact.

## Decisions made in conversation (theirs)

- Split into two increments: this one (compositing, local-only) first,
  YouTube publishing second, each with its own spec + plan.
- Everything else about *how* to composite (module layout, filter
  approach, restart-vs-hot-swap) is the builder's call — not raised as a
  question since it doesn't change what the user is getting, only how.

## Goals

- Resolve which background is *configured* for the current moment —
  step override, else session `background` while a step is active, else
  `before` (nothing started yet) or `after` (last step ended) — as a pure
  function, independent of camera connectivity. Config already validates
  the shape (`validate_background_mode`); nothing yet resolves it against
  the schedule.
- Combine that configured mode with live camera health: `camera` mode
  falls back to `black` when the camera isn't actually connected (same
  fallback behavior increment 2 already reports in
  `background_mode`, now actually reflected in the rendered frame instead
  of being a status label with no corresponding pixels). Critically, this
  fallback must not get the system stuck: whenever the configured mode is
  `camera`, the supervisor keeps actively retrying the real camera
  connection on the existing backoff schedule (`next_restart_at`,
  `failure_count` — unchanged from increment 2) even while currently
  rendering the `black` filler; a `black`-rendering process is torn down
  and replaced by a fresh camera-input attempt the instant a retry is
  due, exactly as increment 2 already retries today. (Flagged by
  builder-check: a naive "compute `camera_connected` as a plain boolean,
  diff it against what's running" design would either never re-probe once
  it fell back to `black`, or would restart on every single tick while a
  camera attempt is still proving itself within the stall timeout. See
  "Supervisor changes" below for how this stays coupled to the existing
  backoff/stall machinery instead of becoming a second state machine.)
- One continuously-running FFmpeg process per effective source, instead
  of increment 2's periodic restart-per-snapshot model: `camera` (RTSP
  input), `image` (looped static image from `assets/`), or `black`
  (synthetic `lavfi` color source) — each scaled to one fixed resolution
  (`stream.width`/`stream.height`/`stream.fps` from config, already
  present in `session.example.yaml` but unused by any code).
- Burn the current overlay text (session name, stage, temperature,
  progress — everything already in `OverlayData`) onto every frame via a
  `drawtext` filter that reloads its text from a small file the main loop
  rewrites every tick — so overlay content updates (progress %,
  temperature) never require restarting FFmpeg. Only a genuine *source*
  change (background mode, camera connect/disconnect) restarts it.
- Replace increment 2's `-vf fps=1/5 -update 1 <jpg>` snapshot output with
  the same JPEG artifact (`runtime/camera_preview.jpg`), now fed from the
  composited/filtered stream — same local preview workflow, same file
  path, same refresh cadence, but showing the real composited picture.
- Extend (not replace) the existing supervisor (`starter.py`,
  `supervisor.py`, `state.py`): add a restart trigger for "effective
  source changed" alongside the existing restart-on-exit and
  restart-on-stall triggers; the JPEG-freshness stall check keeps working
  unchanged, since the composited process still refreshes that same file.
- A missing camera, a missing/invalid background image file, or an
  unresolvable background mode must each degrade to a working (black, if
  necessary) composited frame, never a dead process and never an
  exception escaping `brewcam/video/`.

## Non-goals / anti-goals

- No YouTube publishing, no RTMP/RTMPS, no stream key / ingestion URL
  handling — entirely out of scope, covered by increment 4.
- No audio of any kind — the composited process stays video-only in this
  increment (silence generation for a required audio track is increment
  4's concern, once there's an actual destination that needs one).
- No new always-growing video file on disk (no rotating MP4/segment
  archive) — the only standing output artifact stays the single refreshed
  JPEG, matching today's UAT workflow and avoiding new retention/rotation
  scope that nothing asked for.
- No multi-input filter-graph that keeps all three sources (camera/
  image/black) open simultaneously for instant hot-switching — mode
  changes are infrequent (step boundaries, camera reconnects), so a
  restart is simple and reuses the supervisor's existing restart
  machinery; a hot-swap filtergraph would be premature abstraction for a
  need that doesn't exist yet.
- Would be a failure even if "working": a frozen or stale overlay (e.g.
  progress % stuck from before a restart) silently shown as current;
  claiming a background image is displayed when the file failed to load;
  a background-mode resolution bug that picks yesterday's step; any
  change that makes increment 4's YouTube output leg need a different
  encoding pipeline than this one (the whole point of the split is reuse).

## Constraints carried over from the repo

- Flat `brewcam/` layout, Python files ≤100 lines, short functions, no new
  abstractions beyond what this needs.
- `brewcam/video/` still never imported by `overlay.py`, `schedule.py`, or
  sensor code; the reverse (video importing `overlay.OverlayData` to
  render its text) is fine and already implicitly allowed.
- JSONL records built field-by-field, no `**dict` passthrough.
- No secret exists in this increment (no YouTube key yet), but the RTSP
  URL redaction discipline from increment 2 (`redact_url`) still applies
  everywhere the camera source is touched.
- `pipenv run python -m pytest` (not bare `pytest`).
- TDD: failing test → minimal implementation → passing test → commit.

## Architecture

### `brewcam/video/background.py` (new)

- `resolve_configured_mode(session, steps, schedule_state, now) -> dict`:
  pure function (no I/O, no clock read — `now`/`schedule_state` passed
  in, same discipline as `schedule.py`). Returns the `{"mode": ..., "path":
  ...}` spec that applies right now: the active step's `background` if
  set, else `session.background` while a step is active; `session.before`
  if `schedule_state.status == "upcoming"`; `session.after` if
  `"done"`.
- `resolve_display_source(configured_mode: dict, status: str) -> dict`:
  pure function — returns `configured_mode` unchanged unless
  `configured_mode["mode"] == "camera"` and `status` (the supervisor's
  existing `VideoState.status`: `"unavailable" | "connecting" |
  "connected" | "stalled"`) is not `"connected"`, in which case returns
  `{"mode": "black"}`. This only decides what to *render* right now — it
  does not decide whether to keep attempting the camera connection; that
  stays the supervisor's job (below), driven by the backoff timer that
  already exists, so a fallback to `black` is never a dead end.

### `brewcam/video/overlay_text.py` (new)

- `render_overlay_text(overlay: OverlayData) -> str`: formats the
  existing `OverlayData` fields into the multi-line text block to burn
  in (session name / stage + description / temperature / progress).
  Escaping: `drawtext`'s `textfile` option reads the file's raw bytes as
  text, so no FFmpeg command-line escaping is needed here — only
  newlines and non-UTF8 input need guarding against, both fully under
  our control since we generate the string ourselves.
- `write_overlay_text(path: Path, text: str) -> None`: atomic write
  (write to `path.with_suffix(".tmp")`, then `os.replace`) — `drawtext`'s
  `reload=1` re-reads the file every frame, so a half-written file would
  otherwise risk a visibly garbled frame.

### `brewcam/video/filters.py` (new)

- `build_compose_command(display_source: dict, rtsp_url: str | None, overlay_text_path: Path, stream_config: dict, jpeg_path: Path) -> list[str]`:
  builds the full FFmpeg argv for whichever process the supervisor's
  restart-trigger rules (below) decide to run — `display_source` is
  `resolve_display_source`'s output when rendering a filler, or simply
  `{"mode": "camera"}` when the supervisor is attempting/running the real
  camera input (rule 1 below).
  - Input selection on `display_source["mode"]`:
    - `camera`: `-rtsp_transport tcp -timeout 10000000 -i <rtsp_url>`
      (unchanged from increment 2's actual implementation in
      `camera.build_snapshot_command` — note the flag is `-timeout`, not
      `-rw_timeout`; the increment-2 design doc's narrative mentions
      `-rw_timeout`, but the shipped code uses `-timeout`, and this
      increment reuses the shipped flag as-is rather than silently
      changing camera timeout behavior. Flagged by builder-check.)
    - `image`: `-loop 1 -framerate <stream.fps> -i <display_source["path"]>`.
    - `black`: `-f lavfi -i color=c=black:s=<w>x<h>:r=<fps>`.
  - Filter chain: `-vf
    "scale=<w>:<h>,drawtext=textfile=<overlay_text_path>:reload=1:fontcolor=white:fontsize=24:x=20:y=20:box=1:boxcolor=black@0.5"`
    — one `drawtext` block renders the whole pre-formatted text, not one
    filter per field (keeps the command simple; exact box/position
    values are the builder's call, not load-bearing).
  - Output: `-an -pix_fmt yuv420p -r 1/5 -update 1 -y <jpeg_path>` — same
    cadence and `-an`/no-audio discipline as increment 2's snapshot
    command, now downstream of the filter chain instead of a raw
    passthrough.
- No class hierarchy, no per-mode subclasses — one function with a small
  `if/elif` on `display_source["mode"]`, consistent with "no factories."

### Supervisor changes (`starter.py`, `supervisor.py`, `state.py`)

- `VideoState` gains one field: `running_source: dict | None = None` —
  the source the *currently running* process was actually started with
  (`{"mode": "camera"}`, `{"mode": "image", "path": ...}`, or
  `{"mode": "black"}`), distinct from `resolve_display_source`'s output,
  so the supervisor can tell "what's running" from "what should be
  displayed right now" and from "what's configured."
- Restart triggers, in priority order, each tick:
  1. **Configured mode is `camera` and a retry is due** (`now >=
     state.next_restart_at`, the existing backoff gate, unchanged from
     increment 2): tear down whatever is running (if anything) and start
     a fresh camera-input compose process, set `running_source =
     {"mode": "camera"}`. This is the generalization of increment 2's
     `start_if_due` — it already did exactly this for the snapshot
     process; now it does it for the composited process instead, and may
     need to replace a currently-running `black`/`image` filler to do it.
  2. **Non-camera configured mode changed** (`image` path or `black`
     differs from `running_source`, and rule 1 didn't just fire): restart
     into the newly configured non-camera source. Covers step-boundary
     changes to `background`.
  3. **Camera attempt in progress and still within the stall timeout**:
     leave it running — this is what lets `status` reach `"connected"`
     without being torn down mid-attempt (the builder-check risk: don't
     diff against `camera_connected` every tick and restart before the
     attempt can prove itself).
  4. **Camera attempt exceeded the stall timeout, or exited, without
     reaching `"connected"`**: tear it down, increment `failure_count`,
     set `next_restart_at` via the existing backoff helper (unchanged),
     and start a `black` filler process (`running_source = {"mode":
     "black"}`) so the JPEG keeps refreshing until the next scheduled
     retry — this filler is itself replaced by rule 1 the instant the
     backoff window elapses, so the camera is always retried on schedule,
     never abandoned once in `black`.
  - `resolve_display_source` (used for the composited frame's *content*
    — which filter-chain/background to render) and `running_source`
    (what process is actually alive) stay in sync by construction: rules
    1–4 are the only place either one changes.
- `watch_running_process`'s stall/health detection is unchanged for the
  JPEG-freshness check itself — it already keys off the JPEG file's mtime
  vs. `process_started_at` — but now also drives `state.status` (used by
  `resolve_display_source`) the same way it does today; no new freshness
  logic, only the restart-trigger priority list above layered on top.
- `video.tick(...)` signature grows to accept the already-computed
  `OverlayData`, `SessionConfig`/`steps`/`StageState` (whatever
  `background.resolve_configured_mode` needs) and the `overlay_text_path`
  — `main.py` already computes `overlay`/`state` every iteration, so this
  is wiring, not new computation.

### `main.py` integration

- Each iteration: after building `overlay`, call
  `overlay_text.write_overlay_text(overlay_text_path, overlay_text.render_overlay_text(overlay))`
  before `video.tick(...)` — so the file the running FFmpeg process reads
  is never more than one tick (1s) stale.
- `video.tick(...)` now needs `config.session`, `config.steps`,
  `schedule_state`, and `config.stream` (for width/height/fps) in
  addition to what it already takes — pass them through explicitly, no
  new config-shaped object.

### Config / validation

- `BrewcamConfig.stream` already exists as an unvalidated passthrough
  dict (`width`, `height`, `fps` present in `session.example.yaml`,
  `destination`/`key_env` present but unused until increment 4). Add
  `validate_stream_dimensions(spec: dict)` to `validation.py`: `width`,
  `height`, `fps` must be positive integers — this increment is the
  first code that actually reads them, so this is the point where a
  malformed value should surface as `ConfigError` rather than an opaque
  FFmpeg failure. Does **not** validate `destination`/`key_env` — that's
  increment 4's concern, kept out of this increment's diff.

## Testing approach

- `background.py`: pure-function unit tests — step-override precedence,
  `before`/`upcoming`/`after` resolution, and the camera-disconnected
  fallback from `camera` to `black` — fast, no FFmpeg, no clock reads
  (inject `now`/`schedule_state` directly, matching `schedule.py`'s
  existing test style).
- `overlay_text.py`: unit tests for text rendering (field presence,
  stable ordering) and atomic-write behavior (no partial file ever
  visible to a concurrent reader — simulate by checking the `.tmp` file
  is gone and the target file is complete after the call).
- `filters.py`: command-construction unit tests (no real FFmpeg) — assert
  the right input flags appear per mode (`camera`/`image`/`black`),
  `-an` and `-pix_fmt yuv420p` are always present, `drawtext` references
  the right text-file path with `reload=1`, and `scale=<w>:<h>` matches
  config.
- Supervisor: extend the existing mocked-`Popen`/fake-clock tests with a
  "source changed mid-run → restart" case, covering a step boundary that
  changes `background.mode` while the process is otherwise healthy.
- **Real local verification (not mocked):** using the real `ffmpeg`
  available in this environment (confirmed: FFmpeg 9.0.2 with
  `libx264`), run `build_compose_command` against a synthetic `black`
  source for a few seconds, confirm via `ffprobe` that the output JPEG
  has the configured resolution and that the file's mtime keeps
  advancing — then repeat for `image` mode against
  `assets/standby.png`. This is the "verify composition locally using a
  synthetic source" check the original ask called for; it belongs here,
  since this is the increment that actually builds compositing. Real
  camera/RTSP verification stays a manual step for the user (same
  limitation as increment 2 — no camera/Docker access in this session).

## What's left to the builder

- Exact `drawtext` styling (font size/position/box opacity) — proposed
  values above, cosmetic, easy to tune without a design change.
- Whether `background.py`/`overlay_text.py`/`filters.py` end up as three
  files or two, as long as each stays ≤100 lines and the module
  boundaries in "Architecture" above are respected.
- Exact field/ordering of the burned-in overlay text — the data to show
  (session, stage, description, temperature, progress) is fixed by
  `OverlayData`; the layout is the builder's call.

## Manual verification checklist (for the user, after implementation)

1. `pipenv run python -m pytest` — all tests, including the new
   synthetic-source compositing checks, pass without Docker or a camera.
2. Run Brewcam locally with `CAMERA_SOURCE` unset — confirm
   `runtime/camera_preview.jpg` shows a black frame with the current
   overlay text burned in, refreshing every tick's worth of progress.
3. Point `session.example.yaml`'s active step at `background: {mode:
   image, path: assets/standby.png}` — confirm the preview shows the
   standby image with overlay text burned on top.
4. Against the real Wyze camera (user's own terminal, same limitation as
   increment 2): confirm the preview shows the live camera feed with
   overlay text burned in, and that disconnecting/reconnecting the
   camera restarts the process into `black` and back without crashing
   the main loop.
