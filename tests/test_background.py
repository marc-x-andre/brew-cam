from brewcam.config import SessionConfig, Step
from brewcam.schedule import StageState
from brewcam.video.background import resolve_configured_mode, resolve_display_source

SESSION = SessionConfig(
    name="Test", timezone="UTC",
    background={"mode": "camera"}, before={"mode": "black"},
    after={"mode": "image", "path": "assets/standby.png"},
)
STAGE_WITH_OVERRIDE = Step(
    name="s", description="d", start=None, end=None,
    target_temperature_c=None, background={"mode": "image", "path": "assets/other.png"},
)
STAGE_NO_OVERRIDE = Step(
    name="s", description="d", start=None, end=None,
    target_temperature_c=None, background=None,
)


def test_resolve_configured_mode_prefers_step_override():
    state = StageState(stage=STAGE_WITH_OVERRIDE, status="active", stage_progress_pct=0, overall_progress_pct=0)
    assert resolve_configured_mode(SESSION, state) == {"mode": "image", "path": "assets/other.png"}


def test_resolve_configured_mode_uses_session_background_without_step_override():
    state = StageState(stage=STAGE_NO_OVERRIDE, status="active", stage_progress_pct=0, overall_progress_pct=0)
    assert resolve_configured_mode(SESSION, state) == {"mode": "camera"}


def test_resolve_configured_mode_upcoming_uses_before():
    state = StageState(stage=None, status="upcoming", stage_progress_pct=0, overall_progress_pct=0)
    assert resolve_configured_mode(SESSION, state) == {"mode": "black"}


def test_resolve_configured_mode_done_uses_after():
    state = StageState(stage=None, status="done", stage_progress_pct=100, overall_progress_pct=100)
    assert resolve_configured_mode(SESSION, state) == {"mode": "image", "path": "assets/standby.png"}


def test_resolve_display_source_falls_back_to_black_when_camera_not_connected():
    assert resolve_display_source({"mode": "camera"}, "connecting") == {"mode": "black"}
    assert resolve_display_source({"mode": "camera"}, "unavailable") == {"mode": "black"}
    assert resolve_display_source({"mode": "camera"}, "stalled") == {"mode": "black"}


def test_resolve_display_source_shows_camera_when_connected():
    assert resolve_display_source({"mode": "camera"}, "connected") == {"mode": "camera"}


def test_resolve_display_source_passes_through_non_camera_modes_unchanged():
    spec = {"mode": "image", "path": "assets/standby.png"}
    assert resolve_display_source(spec, "unavailable") == spec
