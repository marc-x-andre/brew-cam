from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .validation import (
    ConfigError,
    validate_step_sequence,
    validate_stream_dimensions,
    validate_timezone_offset,
    validate_youtube_stream,
)


@dataclass(frozen=True)
class Step:
    name: str
    description: str
    start: datetime
    end: datetime
    target_temperature_c: float | None


@dataclass(frozen=True)
class BrewcamConfig:
    timezone: str
    steps: list[Step]
    camera: dict
    sensors: dict
    stream: dict
    overlays: list[str]
    monitoring: dict


def _parse_timestamp(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ConfigError(f"timestamp {value!r} is missing a UTC offset")
    return moment


def _build_step(step_raw: dict) -> Step:
    target = step_raw.get("target_temperature_c")
    return Step(
        name=step_raw["name"],
        description=step_raw["description"],
        start=_parse_timestamp(step_raw["start"]),
        end=_parse_timestamp(step_raw["end"]),
        target_temperature_c=float(target) if target is not None else None,
    )


def load_config(path: str) -> BrewcamConfig:
    try:
        raw = yaml.safe_load(Path(path).read_text())
        config = BrewcamConfig(
            timezone=raw["timezone"],
            steps=[_build_step(step_raw) for step_raw in raw["steps"]],
            camera=raw["camera"],
            sensors=raw["sensors"],
            stream=raw["stream"],
            overlays=raw["overlays"]["enabled"],
            monitoring=raw["monitoring"],
        )
        validate_step_sequence([(step.start, step.end) for step in config.steps])
        validate_stream_dimensions(config.stream)
        validate_youtube_stream(config.stream)
        for step in config.steps:
            validate_timezone_offset(step.start, config.timezone)
            validate_timezone_offset(step.end, config.timezone)
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise ConfigError(f"malformed config at {path}: {exc}") from exc

    return config
