from pathlib import Path
from brewcam.video.filters import build_compose_command
from brewcam.video.youtube import YoutubeTarget

STREAM = {"width": 1280, "height": 720, "fps": 25}
TEXT_PATH = Path("/tmp/overlay.txt")
SNAPSHOT = Path("/tmp/snap.jpg")
TARGET = YoutubeTarget(ingest_url="rtmps://a.rtmps.youtube.com/live2", stream_key="unique-marker-key")

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
    assert "-filter_complex" in cmd
    filter_graph = cmd[cmd.index("-filter_complex") + 1]
    assert "scale=1280:720" in filter_graph
    assert f"textfile={TEXT_PATH}" in filter_graph and "reload=1" in filter_graph
    assert "expansion=none" in filter_graph
    assert "[comp]" in filter_graph
    assert cmd[cmd.index("-map") + 1] == "[comp]"


def test_no_youtube_leg_when_target_is_none():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT)
    assert "anullsrc" not in " ".join(cmd)
    assert cmd[-1] == str(SNAPSHOT)
    assert "split" not in " ".join(cmd)


def test_youtube_target_splits_the_composited_pad_for_both_outputs():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT, youtube_target=TARGET)
    filter_graph = cmd[cmd.index("-filter_complex") + 1]
    assert "split=2[comp][yt]" in filter_graph
    # the JPEG leg still maps [comp]
    assert cmd[cmd.index("-map") + 1] == "[comp]"
    # the YouTube leg's video map (immediately preceding the "1:a" audio map) targets [yt], not [comp]
    audio_map_index = cmd.index("1:a")
    assert cmd[audio_map_index - 1] == "-map"
    assert cmd[audio_map_index - 2] == "[yt]"
    assert cmd[audio_map_index - 3] == "-map"


def test_youtube_leg_adds_silent_audio_and_required_encoder_settings():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT, youtube_target=TARGET)
    joined = " ".join(cmd)
    assert "anullsrc=channel_layout=stereo:sample_rate=44100" in joined
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-profile:v") + 1] == "main"
    assert cmd[cmd.index("-b:v") + 1] == "3000k"
    assert cmd[cmd.index("-minrate") + 1] == "3000k"
    assert cmd[cmd.index("-maxrate") + 1] == "3000k"
    assert cmd[cmd.index("-bufsize") + 1] == "6000k"
    assert cmd[cmd.index("-g") + 1] == "50"  # 2 * 25 fps
    assert cmd[cmd.index("-keyint_min") + 1] == "50"
    assert cmd[cmd.index("-sc_threshold") + 1] == "0"
    assert cmd[cmd.index("-bf") + 1] == "2"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-b:a") + 1] == "128k"
    assert cmd[cmd.index("-ar") + 1] == "44100"
    assert cmd[cmd.index("-ac") + 1] == "2"
    assert cmd[-3] == "-f" and cmd[-2] == "flv" and cmd[-1] == TARGET.rtmps_url
    # the output leg's own "-f flv" must come after every lavfi input's own "-f lavfi"
    assert cmd.index("flv") > cmd.count("lavfi")


def test_youtube_leg_respects_configured_bitrate():
    stream = {**STREAM, "bitrate_kbps": 5000}
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, stream, SNAPSHOT, youtube_target=TARGET)
    assert cmd[cmd.index("-b:v") + 1] == "5000k"
    assert cmd[cmd.index("-bufsize") + 1] == "10000k"


def test_no_real_audio_device_ever_appears():
    for cmd in (
        build_compose_command({"mode": "camera"}, "rtsp://host/cam", TEXT_PATH, STREAM, SNAPSHOT, youtube_target=TARGET),
        build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT),
    ):
        joined = " ".join(cmd)
        for device_flag in ("avfoundation", "alsa", "dshow", "pulse"):
            assert device_flag not in joined


def test_stream_key_appears_exactly_once_in_the_whole_command():
    cmd = build_compose_command({"mode": "black"}, None, TEXT_PATH, STREAM, SNAPSHOT, youtube_target=TARGET)
    occurrences = sum(1 for arg in cmd if "unique-marker-key" in arg)
    assert occurrences == 1
