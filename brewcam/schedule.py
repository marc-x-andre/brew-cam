from dataclasses import dataclass
from datetime import datetime

from brewcam.config import Step


@dataclass(frozen=True)
class StageState:
    stage: Step | None
    status: str  # "upcoming" | "active" | "done"
    stage_progress_pct: float
    overall_progress_pct: float


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _pct(now: datetime, start: datetime, end: datetime) -> float:
    total = (end - start).total_seconds()
    return _clamp((now - start).total_seconds() / total * 100)


def compute_schedule_state(steps: list[Step], now: datetime) -> StageState:
    overall = _pct(now, steps[0].start, steps[-1].end)
    active = next((s for s in steps if s.start <= now < s.end), None)
    if active is not None:
        return StageState(active, "active", _pct(now, active.start, active.end), overall)
    if now >= steps[-1].end:
        return StageState(None, "done", 100.0, 100.0)
    # Before the first step, or in a gap between steps: nothing is running yet.
    return StageState(None, "upcoming", 0.0, overall)
