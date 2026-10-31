import shutil
import subprocess

import pytest

from brewcam.video.filters import build_compose_command
from brewcam.video.overlay_text import write_overlay_text
from brewcam.video.youtube import YoutubeTarget


def _has_drawtext() -> bool:
    try:
        result = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True, timeout=5)
        return "drawtext" in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None or not _has_drawtext(),
    reason="real ffmpeg/ffprobe with drawtext support not available on PATH",
)


def test_youtube_encoding_leg_produces_a_valid_flv_locally(tmp_path):
    overlay_path = tmp_path / "overlay.txt"
    write_overlay_text(overlay_path, "Brassin maison\nFermentation\n19.0C")
    snapshot_path = tmp_path / "snap.jpg"
    (tmp_path / "local_output").mkdir()
    local_target = YoutubeTarget(ingest_url=f"file://{tmp_path}/local_output", stream_key="out.flv")
    command = build_compose_command({"mode": "black"}, None, overlay_path,
                                     {"width": 320, "height": 240, "fps": 10}, snapshot_path,
                                     youtube_target=local_target)
    # Insert "-t 2" as an output-level option for each output leg (right before that
    # leg's own terminal filename/flags), not appended after the whole command -- ffmpeg
    # treats a trailing "-t" as ignored, which would leave the lavfi source running forever.
    snapshot_index = command.index(str(snapshot_path))
    command = command[:snapshot_index] + ["-t", "2"] + command[snapshot_index:]
    flv_index = command.index(local_target.rtmps_url)
    command = command[:flv_index] + ["-t", "2"] + command[flv_index:]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    output_path = tmp_path / "local_output" / "out.flv"
    assert output_path.exists() and output_path.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,codec_type",
         "-of", "csv=p=0", str(output_path)],
        capture_output=True, text=True, check=True,
    )
    assert "h264" in probe.stdout
    assert "aac" in probe.stdout
