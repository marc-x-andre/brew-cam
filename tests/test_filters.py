from pathlib import Path
from brewcam.video.filters import build_compose_command

STREAM = {"width": 1280, "height": 720, "fps": 25}
TEXT_PATH = Path("/tmp/overlay.txt")
SNAPSHOT = Path("/tmp/snap.jpg")

def test_camera_mode_uses_rtsp_input_with_timeout():
    cmd = build_compose_command({"mode": "camera"}, "rtsp://host/cam1", TEXT_PATH, STREAM, SNAPSHOT)
    assert cmd[cmd.index("-rtsp_transport") + 1] == "tcp"
    assert cmd[cmd.index("-timeout") + 1] == "10000000"
    assert cmd[cmd.index("-i") + 1] == "rtsp://host/cam1"

def test_image_mode_loops_the_configured_path():
    cmd = build_compose_command({"mode": "image", "path": "assets/standby.png"}, None, TEXT_PATH, STREAM, SNAPSHOT)
    assert "-loop" in cmd and cmd[cmd.index("-loop") + 1] == "1"
    assert cmd[cmd.index("-i") + 1] == "assets/standby.png"

def test_black_mode_uses_lavfi_color_matching_configured_dimensions():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT)
    assert cmd[cmd.index("-f") + 1] == "lavfi"
    color_arg = cmd[cmd.index("-i") + 1]
    assert "color=c=black" in color_arg and "s=1280x720" in color_arg and "r=25" in color_arg

def test_output_always_excludes_audio_and_sets_pixel_format():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT)
    assert "-an" in cmd
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert cmd[-1] == str(SNAPSHOT)

def test_filter_chain_scales_and_reloads_overlay_text():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT)
    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=1280:720" in vf
    assert f"textfile={TEXT_PATH}" in vf
    assert "reload=1" in vf
    assert "expansion=none" in vf
