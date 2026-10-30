from dataclasses import dataclass

from brewcam.config import BrewcamConfig
from brewcam.schedule import StageState
from brewcam.sensors import SensorReading


@dataclass(frozen=True)
class OverlayData:
    session_name: str
    stage_name: str
    stage_description: str
    status_label: str
    stage_progress_pct: float
    overall_progress_pct: float
    temperature_display: str


def build_overlay(
    config: BrewcamConfig, schedule_state: StageState, temperature: SensorReading
) -> OverlayData:
    stage = schedule_state.stage
    if temperature.status == "available":
        temperature_display = f"{temperature.value}{temperature.unit}"
    else:
        # "stale" is also shown as "Unavailable" per the charter's rule that a
        # stale reading must never display as current.
        temperature_display = "Unavailable"
    return OverlayData(
        session_name=config.session.name,
        stage_name=stage.name if stage is not None else "",
        stage_description=stage.description if stage is not None else "",
        status_label=schedule_state.status,
        stage_progress_pct=schedule_state.stage_progress_pct,
        overall_progress_pct=schedule_state.overall_progress_pct,
        temperature_display=temperature_display,
    )
