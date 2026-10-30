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

## Status

This is a hardware-free increment: there is no FFmpeg, camera, YouTube
streaming, or ThermoPro sensor integration yet. The temperature readings are
simulated and the video status written to the logs is a fixed placeholder.
That integration work is planned for a later increment.
