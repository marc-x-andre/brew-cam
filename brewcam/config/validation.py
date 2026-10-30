from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class ConfigError(Exception):
    pass


def validate_step_sequence(spans: list[tuple[datetime, datetime]]) -> None:
    for start, end in spans:
        if end <= start:
            raise ConfigError(f"span {start.isoformat()}..{end.isoformat()} has end <= start")
    for i in range(len(spans) - 1):
        if spans[i][1] != spans[i + 1][0]:
            raise ConfigError(
                f"span {i} ends at {spans[i][1].isoformat()} but span {i + 1} "
                f"starts at {spans[i + 1][0].isoformat()}"
            )


def validate_timezone_offset(moment: datetime, tz_name: str) -> None:
    expected = moment.astimezone(ZoneInfo(tz_name)).utcoffset()
    if moment.utcoffset() != expected:
        raise ConfigError(
            f"{moment.isoformat()} has offset {moment.utcoffset()}, "
            f"but {tz_name} is {expected} at that instant"
        )


def validate_background_mode(spec: dict, assets_dir: Path | None = None) -> None:
    assets_dir = assets_dir if assets_dir is not None else Path.cwd()
    mode = spec["mode"]
    if mode not in {"camera", "image", "black"}:
        raise ConfigError(f"unknown background mode {mode!r}")
    if mode == "image" and not (assets_dir / spec["path"]).is_file():
        raise ConfigError(f"background image {spec['path']!r} not found in {assets_dir}")
