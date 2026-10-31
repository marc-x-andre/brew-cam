from brewcam.overlay import OverlayData
from brewcam.video.overlay_text import render_overlay_text, write_overlay_text

OVERLAY = OverlayData(
    session_name="Brassin maison", stage_name="Empatage",
    stage_description="Extraction des sucres", status_label="active",
    stage_progress_pct=42.5, overall_progress_pct=10.0,
    temperature_display="67.0C", time_display="14:32:07",
)


def test_render_overlay_text_includes_all_fields():
    text = render_overlay_text(OVERLAY)
    for expected in ("Brassin maison", "Empatage", "Extraction des sucres", "67.0C", "42", "10", "14:32:07"):
        assert expected in text


def test_write_overlay_text_writes_full_content(tmp_path):
    path = tmp_path / "overlay.txt"
    write_overlay_text(path, "hello\nworld")
    assert path.read_text() == "hello\nworld"


def test_write_overlay_text_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "overlay.txt"
    write_overlay_text(path, "line one")
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_overlay_text_overwrites_cleanly_on_repeated_calls(tmp_path):
    path = tmp_path / "overlay.txt"
    write_overlay_text(path, "first")
    write_overlay_text(path, "second, shorter than the overwrite could leave trailing bytes from")
    write_overlay_text(path, "third")
    assert path.read_text() == "third"
