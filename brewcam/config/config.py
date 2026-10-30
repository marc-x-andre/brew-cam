from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .validation import (
    ConfigError,
    validate_background_mode,
    validate_step_sequence,
    validate_timezone_offset,
)


@dataclass(frozen=True)
class Step:
    name: str
    description: str
    start: datetime
    end: datetime
    target_temperature_c: float | None
    background: dict | None


@dataclass(frozen=True)
class SessionConfig:
    name: str
    timezone: str
    background: dict
    before: dict
    after: dict


@dataclass(frozen=True)
class BrewcamConfig:
    session: SessionConfig
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
        background=step_raw.get("background"),
    )


def load_config(path: str) -> BrewcamConfig:
    try:
        raw = yaml.safe_load(Path(path).read_text())
        session_raw = raw["session"]
        session = SessionConfig(
            name=session_raw["name"],
            timezone=session_raw["timezone"],
            background=session_raw["background"],
            before=session_raw["before"],
            after=session_raw["after"],
        )
        config = BrewcamConfig(
            session=session,
            steps=[_build_step(step_raw) for step_raw in raw["steps"]],
            camera=raw["camera"],
            sensors=raw["sensors"],
            stream=raw["stream"],
            overlays=raw["overlays"]["enabled"],
            monitoring=raw["monitoring"],
        )
    except (KeyError, TypeError, yaml.YAMLError) as exc:
        raise ConfigError(f"malformed config at {path}: {exc}") from exc

    validate_step_sequence([(step.start, step.end) for step in config.steps])
    for background_spec in (config.session.background, config.session.before, config.session.after):
        validate_background_mode(background_spec)
    for step in config.steps:
        if step.background is not None:
            validate_background_mode(step.background)
        validate_timezone_offset(step.start, config.session.timezone)
        validate_timezone_offset(step.end, config.session.timezone)

    return config
