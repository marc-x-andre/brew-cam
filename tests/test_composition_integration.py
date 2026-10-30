import shutil
import subprocess
import time

import pytest

from brewcam.video.filters import build_compose_command
from brewcam.video.overlay_text import write_overlay_text

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="real ffmpeg/ffprobe not available on PATH",
)

OVERLAY_TEXT = "Brassin maison\nEmpatage\n67.0C\nStage: 42%  Overall: 10%"


def test_black_source_produces_a_correctly_sized_jpeg(tmp_path):
    overlay_path = tmp_path / "overlay.txt"
    write_overlay_text(overlay_path, OVERLAY_TEXT)
    snapshot_path = tmp_path / "snap.jpg"
    command = build_compose_command({"mode": "black"}, None, overlay_path,
                                     {"width": 320, "height": 240, "fps": 10}, snapshot_path)
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if snapshot_path.exists():
                break
            time.sleep(0.1)
        assert snapshot_path.exists()
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(snapshot_path)],
            capture_output=True, text=True, check=True,
        )
        assert probe.stdout.strip() == "320,240"
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_image_source_produces_a_correctly_sized_jpeg(tmp_path):
    overlay_path = tmp_path / "overlay.txt"
    write_overlay_text(overlay_path, OVERLAY_TEXT)
    snapshot_path = tmp_path / "snap.jpg"
    command = build_compose_command({"mode": "image", "path": "assets/standby.png"}, None, overlay_path,
                                     {"width": 320, "height": 240, "fps": 10}, snapshot_path)
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if snapshot_path.exists():
                break
            time.sleep(0.1)
        assert snapshot_path.exists()
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(snapshot_path)],
            capture_output=True, text=True, check=True,
        )
        assert probe.stdout.strip() == "320,240"
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_missing_image_file_exits_quickly_instead_of_hanging(tmp_path):
    overlay_path = tmp_path / "overlay.txt"
    write_overlay_text(overlay_path, "test")
    command = build_compose_command({"mode": "image", "path": str(tmp_path / "does-not-exist.png")},
                                      None, overlay_path, {"width": 320, "height": 240, "fps": 10},
                                      tmp_path / "snap.jpg")
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        exit_code = process.wait(timeout=5)  # must exit on its own, not hang
        assert exit_code != 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
