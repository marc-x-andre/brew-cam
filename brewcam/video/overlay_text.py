import os
from pathlib import Path

from brewcam.overlay import OverlayData


def render_overlay_text(overlay: OverlayData) -> str:
    lines = [overlay.session_name, f"Time: {overlay.time_display}"]
    if overlay.stage_description:
        lines.append(overlay.stage_description)
    lines.append(f"Temperature: {overlay.temperature_display}")
    lines.append(
        f"Stage: {overlay.stage_progress_pct:.0f}%  Overall: {overlay.overall_progress_pct:.0f}%"
    )
    return "\n".join(lines)


def write_overlay_text(path: Path, text: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text)
    os.replace(tmp_path, path)
