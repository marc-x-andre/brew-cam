import os
from pathlib import Path

from brewcam.overlay import OverlayData


def render_overlay_text(overlay: OverlayData) -> str:
    return "\n".join(
        [
            overlay.session_name,
            f"Time: {overlay.time_display}",
            f"{overlay.stage_name} - {overlay.stage_description}",
            f"Temperature: {overlay.temperature_display}",
            f"Stage: {overlay.stage_progress_pct:.0f}%  Overall: {overlay.overall_progress_pct:.0f}%",
        ]
    )


def write_overlay_text(path: Path, text: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text)
    os.replace(tmp_path, path)
