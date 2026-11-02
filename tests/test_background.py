from brewcam.video.background import resolve_display_source


def test_resolve_display_source_falls_back_to_black_when_camera_not_connected():
    assert resolve_display_source({"mode": "camera"}, "connecting") == {"mode": "black"}
    assert resolve_display_source({"mode": "camera"}, "unavailable") == {"mode": "black"}
    assert resolve_display_source({"mode": "camera"}, "stalled") == {"mode": "black"}


def test_resolve_display_source_shows_camera_when_connected():
    assert resolve_display_source({"mode": "camera"}, "connected") == {"mode": "camera"}


def test_resolve_display_source_passes_through_non_camera_modes_unchanged():
    spec = {"mode": "image", "path": "assets/standby.png"}
    assert resolve_display_source(spec, "unavailable") == spec
