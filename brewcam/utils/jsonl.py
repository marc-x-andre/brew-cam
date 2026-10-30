import json
from datetime import date
from pathlib import Path


def daily_log_path(directory: str, day: date) -> Path:
    return Path(directory) / f"{day.isoformat()}.jsonl"


def append_jsonl(directory: str, day: date, record: dict) -> None:
    Path(directory).mkdir(parents=True, exist_ok=True)
    with daily_log_path(directory, day).open("a") as f:
        f.write(json.dumps(record) + "\n")


def enforce_retention(directory: str, today: date, retention_days: int) -> None:
    for path in Path(directory).glob("*.jsonl"):
        try:
            file_day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if (today - file_day).days >= retention_days:
            path.unlink()
