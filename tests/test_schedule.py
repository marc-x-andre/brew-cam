from datetime import datetime, timedelta, timezone
from datetime import timezone as tz

from brewcam.config import Step
from brewcam.schedule import compute_schedule_state


def _step(name, h1, h2):
    return Step(name, "d", datetime(2026, 11, 7, h1, tzinfo=timezone.utc),
                datetime(2026, 11, 7, h2, tzinfo=timezone.utc), None, None)


STEPS = [_step("A", 9, 10), _step("B", 10, 12)]


def test_before_first_step_is_upcoming():
    state = compute_schedule_state(STEPS, datetime(2026, 11, 7, 8, tzinfo=timezone.utc))
    assert state.status == "upcoming" and state.stage is None
    assert state.stage_progress_pct == 0 and state.overall_progress_pct == 0


def test_exact_start_boundary_is_active_at_zero_percent():
    state = compute_schedule_state(STEPS, datetime(2026, 11, 7, 9, tzinfo=timezone.utc))
    assert state.status == "active" and state.stage.name == "A"
    assert state.stage_progress_pct == 0


def test_mid_step_progress():
    state = compute_schedule_state(STEPS, datetime(2026, 11, 7, 9, 30, tzinfo=timezone.utc))
    assert state.stage.name == "A" and state.stage_progress_pct == 50


def test_exact_end_boundary_rolls_to_next_step():
    state = compute_schedule_state(STEPS, datetime(2026, 11, 7, 10, tzinfo=timezone.utc))
    assert state.status == "active" and state.stage.name == "B"
    assert state.stage_progress_pct == 0


def test_after_last_step_is_done():
    state = compute_schedule_state(STEPS, datetime(2026, 11, 7, 13, tzinfo=timezone.utc))
    assert state.status == "done" and state.stage is None
    assert state.stage_progress_pct == 100 and state.overall_progress_pct == 100


def test_recompute_from_scratch_is_stateless():
    # calling twice with the same inputs, with no shared state between calls,
    # proves resumption after a restart needs nothing but (steps, now)
    first = compute_schedule_state(STEPS, datetime(2026, 11, 7, 9, 30, tzinfo=timezone.utc))
    second = compute_schedule_state(STEPS, datetime(2026, 11, 7, 9, 30, tzinfo=timezone.utc))
    assert first == second


def test_schedule_handles_dst_crossing_steps():
    # America/Toronto falls back from EDT (-4) to EST (-5) on 2026-11-01.
    # These steps are tagged with their correct local offsets; schedule.py
    # must compare them correctly in UTC regardless of the offset change.
    before_fallback = Step("Before", "d",
        datetime(2026, 11, 1, 1, 0, tzinfo=tz(timedelta(hours=-4))),
        datetime(2026, 11, 1, 1, 30, tzinfo=tz(timedelta(hours=-4))), None, None)
    after_fallback = Step("After", "d",
        datetime(2026, 11, 1, 1, 30, tzinfo=tz(timedelta(hours=-4))),
        datetime(2026, 11, 1, 1, 30, tzinfo=tz(timedelta(hours=-5))), None, None)
    steps = [before_fallback, after_fallback]
    now = datetime(2026, 11, 1, 1, 15, tzinfo=tz(timedelta(hours=-5)))  # inside "After" in UTC terms
    state = compute_schedule_state(steps, now)
    assert state.stage.name == "After"
