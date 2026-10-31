# Brewcam

## Prerequisites

- Python 3.15, installed via Homebrew (`brew install python@3.15`)
- [Pipenv](https://pipenv.pypa.io/)

## Setup

```bash
pipenv install --dev
```

If `pipenv install --dev` appears to install nothing (a known quirk in some
environments), use `pipenv sync --dev` instead.

## Running

```bash
pipenv run python -m brewcam --config config/session.example.yaml
```

This loads the session config and runs a terminal loop that computes the
schedule state and a simulated temperature reading, builds the overlay data,
and periodically writes snapshot records to `logs/<local-date>.jsonl`. Stop it
with Ctrl+C; it writes a final `shutdown` event to the same log file and exits
cleanly.

## Tests

```bash
pipenv run python -m pytest
```

Note: this project uses a flat layout (the `brewcam` package lives at the repo
root, not under `src/`), so `pipenv run python -m pytest` must be used instead
of a bare `pipenv run pytest` for the `brewcam` import to resolve correctly.

## Camera pipeline (Wyze Cam V2 via Docker Wyze Bridge)

### Prerequisites and topology

- Docker and Docker Compose.
- A Wyze account with API credentials (`WYZE_KEY_ID` / `WYZE_API_KEY`) —
  see [the Wyze developer API docs](https://developer-api-console.wyze.com/)
  if you don't have these yet.

Both Brewcam and [Docker Wyze Bridge](https://github.com/mrlt8/docker-wyze-bridge)
run as containers on the same host, on a shared Docker network
(`docker-compose.yml` at the repo root). Brewcam never talks to Wyze directly
or holds Wyze protocol logic — it only ever consumes a plain RTSP URL that
the bridge exposes.

### Starting and stopping the bridge

```bash
docker compose up -d wyze-bridge
docker compose down
```

### Providing Wyze authentication

Copy `.env.example` to `.env` (never commit `.env`) and fill in `WYZE_KEY_ID`
and `WYZE_API_KEY`. Leave `WB_USERNAME`/`WB_PASSWORD` unset — on first visit
to the bridge's web UI at `http://127.0.0.1:5050` you'll set its login
there.

### Finding the camera's real RTSP path

Open `http://127.0.0.1:5050`, find your camera, and copy the stream path it
shows. **Do not assume the path matches the camera's display name** — use
exactly what the bridge UI gives you.

### Testing the stream independently, before launching Brewcam

```bash
ffplay "rtsp://wb:$WYZE_API_KEY@127.0.0.1:8554/<path-from-bridge-ui>"
```

If this doesn't show the camera feed, fix it here first — Brewcam can only
ever be as reliable as the bridge connection it's built on.

### Running Brewcam against the camera

Set `CAMERA_SOURCE` in `.env` to the credentialed, service-name form:

```
CAMERA_SOURCE=rtsp://wb:<your WYZE_API_KEY value>@wyze-bridge:8554/<path-from-bridge-ui>
```

```bash
docker compose up brewcam
```

Watch `runtime/camera_preview.jpg` — it refreshes every few seconds once
connected — and `logs/<date>.jsonl` for connection/recovery events.
`ffmpeg_running` in a snapshot record reflects process status only; it is
not confirmation that the image is current or that any downstream viewer
(e.g. YouTube, when the YouTube leg is enabled) sees anything.

### Host vs. Docker addressing

The only difference between the two is the hostname in `CAMERA_SOURCE`:

- Inside the Compose network (how Brewcam normally runs): `wyze-bridge`
  (the service name).
- From the host machine (e.g. for the `ffplay` test above): `127.0.0.1`
  (the loopback-bound published port).

### Troubleshooting

- **Authentication errors against the bridge:** check `WYZE_API_KEY` is
  correct and current, and that your bridge web UI login hasn't expired.
- **Camera missing from the bridge UI:** confirm the camera is online in
  the Wyze app and that your Wyze account/region matches what the bridge
  is configured for.
- **Connection timeouts:** look for `camera_unavailable` (misconfigured or
  unreachable `CAMERA_SOURCE`), `camera_stalled` (camera reachable but no
  frames, including a connection that never produces a first frame), or
  `camera_reconnecting` (ffmpeg started but exited almost immediately —
  often a wrong stream path or credential) events in the day's JSONL log.
- **An interrupted stream:** expect a `camera_disconnected` event followed
  by automatic reconnection attempts with growing backoff (capped at 60s)
  — sensor readings and the main loop keep running throughout.

## YouTube live publishing

Brewcam can publish the same composited video to YouTube as a second RTMPS
output on the FFmpeg process that already drives the local preview.

Set both variables in `.env` (names are listed in `.env.example`):

```
YOUTUBE_INGEST_URL=<RTMPS ingestion URL from YouTube Studio>
YOUTUBE_STREAM_KEY=<stream key from YouTube Studio>
```

The YouTube leg activates automatically once both variables are set and
`config/session.example.yaml` has `stream.destination: youtube`. There is no
separate launch command:

```bash
docker compose up brewcam
```

Stop it with Ctrl+C, as before. If the YouTube configuration is missing or
invalid, Brewcam logs a `youtube_unavailable` event and runs without the
YouTube leg.

### Getting the URL and stream key from YouTube Studio

1. In YouTube Studio, click **Go Live**, then open the **Stream** tab.
2. Click the lock icon next to **Stream URL** to reveal the RTMPS variant
   (plain RTMP is shown by default).
3. Copy the URL and the stream key into `.env` as `YOUTUBE_INGEST_URL` and
   `YOUTUBE_STREAM_KEY`.

### First unlisted-broadcast checklist

1. Create an unlisted broadcast in YouTube Studio; copy its RTMPS ingestion
   URL and stream key into `.env`.
2. Start Brewcam; confirm the local JPEG preview still updates.
3. In Studio's stream health panel, confirm YouTube reports it is receiving
   video; open the unlisted broadcast URL and confirm picture and overlay look
   right.
4. Briefly interrupt network connectivity (or kill the running `ffmpeg`
   process directly, leaving Brewcam running) and confirm the JSONL log shows
   `streaming_failed` → `streaming_retry` → `streaming_recovered`, and that
   Studio shows a real gap, not seamless playback.
5. Stop Brewcam with Ctrl+C; confirm the broadcast ends cleanly in Studio and
   no FFmpeg process is left running.

### Troubleshooting

- **Connection rejected:** the stream key is wrong or expired, or the URL is
  plain RTMP instead of RTMPS. Re-copy both the URL and the stream key fresh
  from Studio.
- **Encoding errors:** look for a `streaming_failed` event in the day's JSONL
  log. When ffmpeg exits on its own, its `details.excerpt` field holds a
  redacted `ffmpeg` stderr excerpt showing what FFmpeg reported. A
  stall-triggered `streaming_failed` has no excerpt.
- **An interrupted stream:** expect `streaming_failed` followed by
  `streaming_retry` events with growing backoff, then `streaming_recovered`
  once reconnected. Studio will show a gap during reconnection. This is
  expected, not a bug: Brewcam does not claim uninterrupted playback while
  reconnecting.

## Status

Camera ingestion from a real Wyze Cam V2, via Docker Wyze Bridge, is now
live: Brewcam supervises an FFmpeg subprocess reading the RTSP feed over
TCP (no audio), recovers from an unavailable camera, a stalled input, or a
dropped connection, and writes a refreshing local JPEG snapshot for manual
verification.

Video compositing is now implemented and locally verified: background-mode
switching (camera, static image, and black-screen fallback) and overlay
text burn-in all run through a real FFmpeg pipeline, confirmed via
synthetic-source automated tests as well as manual verification against
composited output from those same synthetic (black/image) sources — not yet
against a real camera feed. YouTube publishing is implemented and locally
verified (FFmpeg command construction, secret redaction in logged stderr, and
retry behavior), but the real end-to-end test against YouTube is still pending
your own YouTube Studio activation. YouTube playback has **not** been confirmed.
ThermoPro sensor integration also remains a stub; the temperature readings
are still simulated.
