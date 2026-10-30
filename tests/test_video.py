import pytest

from brewcam.video.camera import CameraSourceError, redact_url, resolve_rtsp_url


def test_resolve_rtsp_url_reads_named_env_var(monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "rtsp://wb:secret@wyze-bridge:8554/cam1")
    url = resolve_rtsp_url({"source_env": "CAMERA_SOURCE"})
    assert url == "rtsp://wb:secret@wyze-bridge:8554/cam1"


def test_resolve_rtsp_url_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("CAMERA_SOURCE", raising=False)
    with pytest.raises(CameraSourceError):
        resolve_rtsp_url({"source_env": "CAMERA_SOURCE"})


def test_resolve_rtsp_url_empty_env_var_raises(monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "")
    with pytest.raises(CameraSourceError):
        resolve_rtsp_url({"source_env": "CAMERA_SOURCE"})


def test_redact_url_masks_userinfo():
    redacted = redact_url("rtsp://wb:super-secret@wyze-bridge:8554/cam1")
    assert "super-secret" not in redacted
    assert redacted == "rtsp://***:***@wyze-bridge:8554/cam1"


def test_redact_url_masks_secret_query_params():
    redacted = redact_url("http://host/path?api_key=super-secret&other=1")
    assert "super-secret" not in redacted
    assert "other=1" in redacted


def test_redact_url_leaves_plain_url_unchanged():
    assert redact_url("rtsp://wyze-bridge:8554/cam1") == "rtsp://wyze-bridge:8554/cam1"
