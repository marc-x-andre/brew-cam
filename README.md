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
(e.g. YouTube, once that's implemented) sees anything.

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
against a real camera feed. YouTube publishing is **not yet implemented** — there is
no `youtube.py` yet, and nothing in this repo publishes a stream anywhere.
ThermoPro sensor integration also remains a stub; the temperature readings
are still simulated.
