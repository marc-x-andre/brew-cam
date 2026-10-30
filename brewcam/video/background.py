from brewcam.config import SessionConfig
from brewcam.schedule import StageState


def resolve_configured_mode(session: SessionConfig, schedule_state: StageState) -> dict:
    if schedule_state.status == "active":
        if schedule_state.stage.background is not None:
            return schedule_state.stage.background
        return session.background
    if schedule_state.status == "upcoming":
        return session.before
    return session.after


def resolve_display_source(configured_mode: dict, status: str) -> dict:
    if configured_mode["mode"] == "camera" and status != "connected":
        return {"mode": "black"}
    return configured_mode
