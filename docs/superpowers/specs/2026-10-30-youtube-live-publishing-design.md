# Brewcam Increment 4 — YouTube Live Publishing Design

> Scope note: second half of the two-part split. Builds on increment 3's
> compositing pipeline (`docs/superpowers/specs/2026-10-30-video-compositing-design.md`)
> — assumed approved and implemented before this one starts. This
> increment adds a YouTube RTMPS output leg onto the same composited
> FFmpeg process; it does not build a second encoding pipeline.

## Intent and why

The original ask was "live streaming of the processed video feed to
YouTube." Increment 3 makes the "processed video feed" real (composited,
overlay-burned-in, continuously running). This increment's entire job is
narrower than it first looked: add a second output destination to an
already-working process, using whatever credentials YouTube Studio hands
the user manually (no API/OAuth — broadcast creation stays a manual
Studio step, confirmed non-goal). YouTube live activation is externally
pending (~24h out as of this writing); everything here must be buildable
and verifiable without it, with the real end-to-end test documented as
the one remaining manual step.

**Why now:** activation is in flight; doing the implementation now means
the only thing waiting on YouTube's side, once it activates, is pointing
real credentials at already-tested code.

## Target flow

```
[increment 3's composited FFmpeg process]
        │
        ├─ existing output leg: runtime/camera_preview.jpg (unchanged)
        │
        └─ new output leg: H.264/AAC → FLV → RTMPS → YouTube ingestion
           (silent AAC track; real ingestion URL + stream key from
            YouTube Studio, via env vars; never logged, never in git)
```

One process, two output legs on the same filter graph — not two FFmpeg
processes, not a second compositing stage.

## Decisions made in conversation (theirs)

- Two-increment split (this is the second), agreed with the user; this
  spec assumes increment 3 already exists and is approved.
- RTMPS (not plain RTMP) is required by the original ask — non-negotiable.
- No YouTube API/OAuth, no automated broadcast creation — the user
  manages the broadcast in Studio manually; Brewcam only ever pushes
  video to whatever ingestion URL + key they paste into `.env`.
- Do not connect to YouTube or start a real broadcast without explicit
  authorization — this increment's own validation stops at "local checks
  + a synthetic-source RTMPS-shaped command construction," never an
  actual network connection to `*.youtube.com`.

## Goals

- Read the ingestion URL and stream key from environment variables (two
  separate values — Studio's "Stream URL" and "Stream key" are shown and
  copied separately, and treating them as one opaque pasted string would
  make the key unredactable from the URL half). New config fields on the
  existing `stream` dict: `ingest_url_env` (name of the env var holding
  the RTMPS ingestion URL) alongside the already-present `key_env`
  (holds the env var name for the stream key) — same "`*_env` holds a
  variable *name*, never the value" convention `camera.source_env`
  already established.
- `.env.example` gains `YOUTUBE_INGEST_URL=` alongside the existing
  `YOUTUBE_STREAM_KEY=` (currently present but unused by any code).
- Validate both resolve to non-empty values before attempting to add the
  YouTube output leg; on failure, behave like `camera.py`'s
  `CameraSourceError` pattern — degrade (no YouTube leg this run, local
  preview keeps working), log a redacted `youtube_unavailable`-style
  event, never raise out of `brewcam/video/`.
- Never let the assembled `<ingest_url>/<stream_key>` RTMPS target appear
  unredacted in: git (only `.env.example` placeholders are committed),
  logs/JSONL events, exception messages, or any FFmpeg command this code
  ever prints/logs for debugging — redact the same way `camera.py`
  already redacts RTSP URLs (reuse the pattern, don't reinvent it).
- Encode settings for the YouTube leg, sourced from the current official
  YouTube Help page (`support.google.com/youtube/answer/2853702`,
  checked 2026-10-30) rather than assumed defaults:
  - Video: H.264 (`libx264`), CABAC entropy (default for `main`/`high`
    profiles — no explicit flag needed), 2 B-frames (`-bf 2`, matches
    libx264's own default — explicit for clarity), progressive scan,
    square pixel aspect ratio, Rec. 709 color (SDR — no HDR in scope),
    8-bit, `-pix_fmt yuv420p`.
  - Keyframe interval: 2 seconds, never exceeding 4 — `-g <2*fps>
    -keyint_min <2*fps> -sc_threshold 0` (disables scene-cut-triggered
    extra keyframes so the GOP stays exactly 2s, matching "do not
    exceed").
  - Rate control: CBR — `-b:v`, `-minrate`, `-maxrate` all equal, plus
    `-bufsize` around 2x the bitrate (standard CBR pattern for `libx264`).
  - Resolution/fps/bitrate: reuse `stream.width`/`height`/`fps` from
    config (already 1280x720/25fps in the example session) with a new
    `stream.bitrate_kbps` config field, defaulting to 3000 — inside
    YouTube's published 720p range (H.264 min 3 Mbps / recommended up to
    8 Mbps), chosen low-side since the actual subject (a kettle/carboy
    with mostly static overlay text) is low-motion.
  - Container: FLV (`-f flv`), the standard RTMP(S) mux — not a new
    container choice, just what RTMP(S) requires.
  - Audio: never microphone. A silent track is generated via a second
    `lavfi` input (`-f lavfi -i anullsrc=channel_layout=stereo:
    sample_rate=44100`), encoded `-c:a aac -b:a 128k -ar 44100 -ac 2` per
    the Help page's stereo AAC figures — mapped only into the YouTube
    output leg, not the JPEG leg (which stays `-an`, unchanged).
  - All of the above as named constants with config-driven overrides
    where config already has a natural home (`width`/`height`/`fps`/
    `bitrate_kbps`); the rest (GOP math, CBR bufsize, B-frames, pixel
    format) derived from those, not separately configurable — keeps the
    config surface small and matches "reasonable defaults configurable
    through the existing configuration system" without inventing a dozen
    new knobs nothing asked for.
- Reuse increment 3's single composited process — add the YouTube leg as
  a second `-map`'d output on the same `ffmpeg` invocation increment 3
  already builds, not a second subprocess, not a second filter graph.
- Supervision: the YouTube leg's health rides on the same process's
  `poll()`/restart/backoff machinery increment 3 already has (if RTMPS
  fails, FFmpeg's whole process exits — by design, no `tee` muxer
  `onfail=ignore` — so the existing restart-with-backoff path handles it
  without a second state machine; the brief restart also recreates the
  local JPEG leg, which is the explicitly-accepted tradeoff for "do not
  claim uninterrupted playback during reconnection").
- Monitoring: new event types layered on the existing transition points
  (`streaming_started`, `streaming_failed`, `streaming_retry`,
  `streaming_recovered`, `streaming_stopped`) — same
  `monitoring.write_event` call, same field-allowlist discipline, no new
  JSONL machinery.
- Snapshot record gains a `youtube` field reporting only what's locally
  observable — `{"configured": bool, "leg_active": bool}` — explicitly
  distinct from (and never implying) confirmed YouTube-side playback,
  since this increment has no API access to ever check that.
- Capture FFmpeg's stderr (increment 3 currently discards it via
  `DEVNULL`) for the composited process, and redact it before any of it
  reaches a log line or JSONL `details` — the assembled RTMPS target
  (and the raw stream key specifically) must never appear verbatim even
  in multi-line FFmpeg diagnostic output.
- Clean shutdown on Ctrl+C: unchanged from increment 3's
  `stop()` — terminating the one process stops both output legs.

## Non-goals / anti-goals

- No YouTube Data API, no OAuth, no automated broadcast creation —
  Studio stays manual.
- No actual connection to YouTube, no real broadcast start, in this
  increment's own validation — that is the one remaining manual/live
  step once activation completes (see Manual verification checklist).
- No microphone capture, ever, under any config.
- No new encoding/compositing stage — if implementing this increment
  seems to require changing how frames are composited, that's a sign the
  split boundary was crossed; it should only ever add an output leg.
- No retry/backoff logic independent from increment 3's existing
  supervisor — no second state machine for "YouTube health" apart from
  the process-level one that already exists.
- Would be a failure even if "working": a leaked stream key (in git, a
  log line, an exception, or a printed command); claiming YouTube
  playback is confirmed when only local process health was observed;
  silently falling back to RTMP when RTMPS was unavailable instead of
  failing loud; adding microphone audio under any circumstance.

## Constraints carried over from the repo

- Flat `brewcam/` layout, Python files ≤100 lines, short functions.
- Secrets indirected through `*_env` config fields (`ingest_url_env`,
  `key_env`), resolved from `os.environ` only at the point something
  actually needs the value — never at config-load time, matching
  `camera.source_env`'s existing pattern.
- `.env` never committed (already gitignored); `.env.example` holds
  placeholders only.
- JSONL records built field-by-field, no `**dict` passthrough.
- `pipenv run python -m pytest` (not bare `pytest`).
- TDD: failing test → minimal implementation → passing test → commit.

## Architecture

### `brewcam/video/youtube.py` (new)

- `class YoutubeConfigError(Exception)`.
- `resolve_youtube_target(stream_config: dict) -> YoutubeTarget | None`:
  reads `stream_config["ingest_url_env"]`/`["key_env"]` from
  `os.environ`; returns `None` (not an exception) when
  `stream_config.get("destination") != "youtube"` (feature simply off);
  raises `YoutubeConfigError` when destination is `"youtube"` but either
  env var is unset/empty.
- `@dataclass YoutubeTarget: ingest_url: str; stream_key: str` with a
  `rtmps_url` property assembling `f"{ingest_url.rstrip('/')}/{stream_key}"`
  — the only place the two values are ever concatenated.
- `redact_target(target: YoutubeTarget | None) -> str`: returns a fixed
  `"rtmps://<redacted>"` (or similar) for any logging/display use —
  never partial-masks the key the way `camera.redact_url` partially
  shows a host, since there's no legitimate reason to show any part of
  a YouTube ingestion URL in a log line.
- `redact_stderr(text: str, youtube_target: YoutubeTarget | None, rtsp_url: str | None) -> str`:
  regex-replaces any literal occurrence of the resolved `stream_key`
  (and, defensively, anything matching an `rtmps://` URL), **and** also
  redacts `rtsp_url` the same way `camera.redact_url` already does.
  FFmpeg's startup banner echoes every input URL verbatim (including the
  RTSP source, which already carries real credentials per
  `.env.example`), and this increment is the first code to ever capture
  that stderr (increment 3 and earlier discard it via `DEVNULL`) — so
  without this, turning stderr capture on would newly leak the camera
  credential into JSONL logs even though no YouTube config is involved.
  Builder-check caught this: redaction must cover both secrets this
  increment's stderr capture can newly expose, not just the one it was
  added for.

### `filters.py` extension (from increment 3)

- `build_compose_command(..., youtube_target: YoutubeTarget | None,
  stream_config: dict) -> list[str]` grows an optional second input
  (`anullsrc`) and a second output leg, added only when `youtube_target`
  is not `None`:
  - `-map <video-filter-output> -map <anullsrc-input> -c:v libx264
    -profile:v main -pix_fmt yuv420p -b:v <bitrate_kbps>k -minrate
    <bitrate_kbps>k -maxrate <bitrate_kbps>k -bufsize <2*bitrate_kbps>k
    -g <2*fps> -keyint_min <2*fps> -sc_threshold 0 -bf 2 -c:a aac -b:a
    128k -ar 44100 -ac 2 -f flv <rtmps_url>`.
  - The JPEG leg's existing flags are untouched; this is purely an
    additional `-map`/output pair on the same command list.
- The function never receives the raw stream key as a reusable value
  beyond building this one argv list — nothing stores `rtmps_url` in
  `VideoState` or logs the returned command list unredacted (tests must
  check this explicitly, not just trust it).

### Supervisor / `main.py` changes

- `video.tick(...)` resolves `resolve_youtube_target(config.stream)`
  once per restart decision (same cadence as effective-source resolution
  from increment 3) and passes it into `build_compose_command`; a
  `YoutubeConfigError` degrades to `youtube_target=None` (JPEG-only
  process) plus a `youtube_unavailable` event — logged once per
  transition, not every tick, same discipline as `camera_unavailable`.
- Stderr: switch the `Popen` call from `stderr=subprocess.DEVNULL` to
  `stderr=subprocess.PIPE`; on process exit, read whatever's buffered,
  run it through `redact_stderr(text, youtube_target, rtsp_url)` (both
  the resolved YouTube target and the camera's RTSP URL, so the camera
  credential is covered even on runs with no YouTube leg configured),
  and include the last ~20 lines in the `streaming_failed`/
  `camera_disconnected` event's `details` (bounded size — never the full
  unbounded stream).
- `video.tick(...)`'s returned status dict grows a `youtube` key:
  `{"configured": youtube_target is not None, "leg_active": state.process is not None and youtube_target is not None}`.
- `monitoring.build_snapshot_record` adds the `youtube` field to its
  explicit allowlist (not passthrough) alongside the existing `video`
  field.

### Config / validation

- `validate_stream_config` (extends increment 3's
  `validate_stream_dimensions`, or sits alongside it) checks, only when
  `destination == "youtube"`: `ingest_url_env` and `key_env` are both
  non-empty strings, and `bitrate_kbps` (if present) is a positive
  integer. Does not touch `os.environ` — config validation stays about
  shape, not secret resolution, matching `camera.source_env`'s existing
  split between "config says which env var" (load time) and "resolve
  the env var" (runtime, in `video/`).

## Testing approach

- `youtube.py`: unit tests for `resolve_youtube_target` (both env vars
  present → `YoutubeTarget`; destination not `"youtube"` → `None`;
  destination `"youtube"` with one or both env vars missing →
  `YoutubeConfigError`), `redact_target` (never contains the key or the
  ingest host), and `redact_stderr` (a captured stderr fixture
  containing a literal stream key, RTMPS URL, **and** a raw RTSP
  camera URL with embedded credentials — e.g. an FFmpeg-style startup
  banner line echoing the input URL — comes back scrubbed of all three;
  covered with and without a YouTube target configured, since the RTSP
  credential must be redacted even on camera-only runs now that stderr
  capture is on).
- `filters.py`: command-construction tests — with a `YoutubeTarget`,
  assert the second `-f flv` output leg is present with the expected
  `-g`/`-keyint_min`/`-bf 2`/`-pix_fmt yuv420p` flags and that `anullsrc`
  (not a real audio device) is the only audio input anywhere in the
  command; with `youtube_target=None`, assert the command is
  byte-for-byte what increment 3 already produces (no behavior change
  when YouTube isn't configured).
- Secret redaction: an explicit test asserts that **no** command list
  returned by `build_compose_command`, when `str()`-joined, contains the
  raw stream key fixture value — this is the test most worth getting
  adversarial about, matching "keep secrets out of ... displayed
  commands" from the original ask.
- Retry behavior: extend increment 3's supervisor tests with a case
  where the process exits specifically with a YouTube leg configured —
  assert `streaming_failed` then `streaming_retry` events fire (not
  silently reusing camera-only event names) and that backoff timing is
  the same proven function from increment 2/3, not a reimplementation.
- Audio exclusion: a command-construction test asserts `-f lavfi` +
  `anullsrc` appears and that no flag referencing a real audio capture
  device (e.g. `avfoundation`, `alsa`, `dshow`) ever appears, with or
  without a YouTube target.
- **Local verification without YouTube:** using the real local `ffmpeg`,
  run `build_compose_command` with a synthetic `black` source and a
  *fake* `YoutubeTarget` pointed at a local RTMP-capable receiver
  reachable in this environment (e.g. `ffmpeg`'s own `-f flv -listen 1
  rtmp://127.0.0.1:<port>` as a throwaway local listener, or simply
  `-f flv file:///tmp/.../out.flv` as a local file target standing in
  for the network leg) to confirm the composed command actually produces
  a valid FLV/H.264/AAC stream end-to-end — proves the encoding
  parameters are mutually compatible without ever touching
  `*.youtube.com`. This is the "local output where possible" check the
  original ask called for.
- No real YouTube connection, no real broadcast, anywhere in this
  increment's test suite or manual steps.

## What's left to the builder

- Exact default `bitrate_kbps` (proposed 3000, inside YouTube's 720p
  H.264 range) — easy to raise later via config, not a design change.
- Exact wording/shape of the bounded stderr excerpt in event `details`
  (proposed: last 20 lines, redacted) — tune if 20 proves too
  noisy/sparse once real stderr output is seen.
- Whether `youtube.py`/the `filters.py` extension stays within the
  ≤100-line budget as one file or needs a small split — same latitude
  increment 3 left for its own new modules.

## Documentation (README)

- Required env vars: `YOUTUBE_INGEST_URL`, `YOUTUBE_STREAM_KEY` (update
  `.env.example` to match — it currently has only the latter).
- Exact launch/stop commands: unchanged from today (`docker compose up
  brewcam` / Ctrl+C) — document that the YouTube leg activates
  automatically whenever both env vars are set and `stream.destination:
  youtube` is configured, no separate command.
- How to obtain the ingestion URL and stream key: YouTube Studio → Go
  Live → Stream → click the lock icon next to "Stream URL" to reveal the
  RTMPS variant (plain RTMP is shown by default) → copy both the
  RTMPS URL and the stream key into `.env`.
- First-unlisted-broadcast checklist (for once activation completes):
  create an unlisted broadcast in Studio, paste `YOUTUBE_INGEST_URL`/
  `YOUTUBE_STREAM_KEY` from that broadcast into `.env`, start Brewcam,
  confirm in Studio's stream health panel that YouTube reports it's
  receiving video before going live, watch the unlisted broadcast URL
  directly to confirm overlay/picture quality, then stop Brewcam and
  confirm the broadcast ends cleanly.
- Troubleshooting: rejected connection (wrong/expired stream key, or
  RTMP used instead of RTMPS — re-copy both values fresh from Studio);
  encoding errors (check `ffmpeg` stderr excerpt in the JSONL event,
  already redacted); interrupted stream (expect `streaming_failed` →
  `streaming_retry` with growing backoff in the log, and a visible gap
  in the Studio-side viewer — this is expected per "do not claim
  uninterrupted playback during reconnection," not a bug).

## Manual verification checklist (for the user, after YouTube activation)

1. Create an unlisted broadcast in YouTube Studio; copy its RTMPS
   ingestion URL and stream key into `.env`.
2. Start Brewcam against the real camera (or `black`/`image` mode if the
   camera isn't connected) and confirm the local JPEG preview still
   updates as before.
3. In Studio's stream health panel, confirm YouTube reports it is
   receiving video, and open the unlisted broadcast URL to confirm the
   picture and overlay look right.
4. Briefly interrupt network access (or stop/restart Brewcam) and
   confirm the JSONL log shows `streaming_failed` → `streaming_retry` →
   `streaming_recovered`, and that Studio shows the stream resume after
   a real gap (not implied as seamless).
5. Stop Brewcam with Ctrl+C; confirm the broadcast ends cleanly in
   Studio and no FFmpeg process is left running.
