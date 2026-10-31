---
name: uat-preview
description: Run Brewcam locally and show the actual composited video frame FFmpeg is producing — background mode (camera/image/black) plus the overlay text (session, stage, temperature, progress) burned onto it. Use this whenever someone wants to eyeball the real output of the video pipeline rather than just reading test results — e.g. "run a UAT", "show me what the camera looks like", "let me see the overlay", "check the composited preview", or after changing anything in brewcam/video/ (filters.py, overlay_text.py, background.py) or the overlay layout.
---

# Brewcam video pipeline UAT preview

Brewcam's compositor runs as a long-lived FFmpeg process and continuously
rewrites `runtime/camera_preview.jpg` — that file is always the one
honest picture of what the pipeline is actually doing right now. This
skill launches the app, waits for a fresh frame, and shows it to you.

**What this proves, and what it doesn't:** on a dev machine without
Docker Wyze Bridge running, there's no real camera to connect to, so the
compositor falls back to whatever `before`/`after`/step `background` mode
`config/session.example.yaml`'s schedule currently resolves to (usually
`black`, or `image` if a step is configured that way) — exactly the path
real camera unavailability takes in production. That's enough to verify
overlay burn-in, text content, and background-mode selection, but it is
**not** a live-camera test. Seeing the actual Wyze feed requires
`CAMERA_SOURCE` set in `.env` and Docker Wyze Bridge running — see the
README's camera pipeline section for that.

## Steps

### 1. Find an FFmpeg with `drawtext`

This repo's compositor needs the `drawtext` filter. On at least one dev
machine, the default Homebrew `ffmpeg` on `PATH` was built *without* it,
while a separate `ffmpeg-full` keg (installed via `brew install
ffmpeg-full`, and left unlinked on purpose so it doesn't disturb the
default `ffmpeg`) has it. Check before assuming either way:

```bash
ffmpeg -filters 2>/dev/null | grep -q drawtext && echo "default ffmpeg has drawtext" || echo "default ffmpeg is missing drawtext"
```

If it's missing and `ffmpeg-full` is installed, run the rest of this
skill with that build's `bin` directory prepended to `PATH`:

```bash
if ! ffmpeg -filters 2>/dev/null | grep -q drawtext; then
  if brew --prefix ffmpeg-full >/dev/null 2>&1; then
    export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"
  else
    echo "No ffmpeg with drawtext found. Run: brew install ffmpeg-full" >&2
    exit 1
  fi
fi
```

### 2. Launch Brewcam in the background

Always the `-m` form — this repo's flat `brewcam/` package layout (no
`src/`) means a bare `pipenv run brewcam` won't resolve the import.

```bash
cd /Users/marc-andre/repo/brew-cam
rm -f runtime/camera_preview.jpg
pipenv run python -m brewcam --config config/session.example.yaml > /tmp/brewcam_uat.log 2>&1 &
echo $! > /tmp/brewcam_uat.pid
```

Pass a different `--config` path if the person asks to preview a specific
session file instead of the example one.

### 3. Wait for a fresh composited frame

The JPEG tee refreshes roughly every 5 seconds once FFmpeg is actually
producing frames, but the very first attempt (and any camera retry) can
take a few seconds longer. Poll rather than guessing a fixed sleep:

```bash
for _ in $(seq 1 20); do
  [ -f runtime/camera_preview.jpg ] && break
  sleep 1
done
```

If the file never appears, read `/tmp/brewcam_uat.log` — a startup crash
(bad config, missing `assets/standby.png` for an `image`-mode step, a
`ConfigError`) shows up there before any frame would exist.

### 4. Show the frame

Read `runtime/camera_preview.jpg` back as the UAT result — this is the
actual deliverable the person asked for, not a text description of it.
Is the overlay text present, legible, and showing real values (not
stale/placeholder data)?

### 4b. Gate on actual picture content — a black frame is a FAIL, not a pass

The whole point of this UAT is to see what FFmpeg can actually see. A
background that's effectively flat black means there's nothing real to
look at — the camera didn't connect, or the configured standby image is
itself blank — and that must be reported as a failed check, never glossed
over as "well, that's the expected fallback." Measure it, don't eyeball
it — the overlay text alone can make a black frame *look* like it has
content at a glance:

```bash
YAVG=$(ffprobe -v error -f lavfi -i "movie=runtime/camera_preview.jpg,signalstats" \
  -show_entries frame_tags=lavfi.signalstats.YAVG -of default=noprint_wrappers=1:nokey=1)
```

`YAVG` is the frame's average luma (0–255). A few lines of white overlay
text on an otherwise black 1280x720 canvas lands under ~1; any real
camera feed or genuine photo — even a dim one — reads well above that.
Use a threshold around 5:

- **`YAVG` ≥ 5 → PASS.** There's real picture content behind the overlay.
- **`YAVG` < 5 → FAIL.** State this plainly, and say *why*, using the
  latest JSONL `video.background` value from Step 5 to point at the
  actual cause rather than a generic "it's black":
  - `background_mode: "black"` and the session is simply before/after all
    steps or no camera is configured at all — not a bug, but still not a
    passing "see what the camera sees" check; say so and suggest
    configuring an `image`/`camera` background to actually exercise this.
  - `background_mode: "black"` while a camera *is* configured — the
    camera isn't connecting. Check `.env`'s `CAMERA_SOURCE` and whether
    Docker Wyze Bridge is actually running (this skill cannot start
    Docker for you) before re-running.
  - `background_mode: "image"` but `YAVG` is still near zero — the
    configured image file itself is blank (this repo's own
    `assets/standby.png` placeholder measures `YAVG = 0` as of this
    writing) and needs to be replaced with real artwork before this
    check can mean anything for that mode.

Do not report the UAT as passing, working, or "confirmed" when this gate
fails — report it as a failed/incomplete check and name the cause above.

### 5. Cross-check against the JSONL log

The image shows one frame; the log shows the state that produced it —
useful when something in the picture looks off and you want to know why.

```bash
tail -5 logs/$(date +%Y-%m-%d).jsonl
```

Look at the latest `snapshot` record's `video` field (`background_mode`,
`ffmpeg_running`, and `youtube` once increment 4 exists) to confirm it
matches what the image shows.

### 6. Stop cleanly

Always terminate gracefully and confirm the shutdown event landed —
SIGKILL-ing it or leaving it running defeats the point of checking that
the supervisor cleans up FFmpeg properly on exit.

```bash
kill -TERM "$(cat /tmp/brewcam_uat.pid)"
sleep 2
tail -2 logs/$(date +%Y-%m-%d).jsonl   # expect an "event_type": "shutdown" line
ps aux | grep -i "[b]rewcam\|[f]fmpeg"  # expect no output — nothing orphaned
rm -f /tmp/brewcam_uat.pid
```

If anything is still running, that's itself a finding worth reporting,
not something to silently SIGKILL away.
