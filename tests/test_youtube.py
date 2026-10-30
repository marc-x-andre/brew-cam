import pytest
from brewcam.video.youtube import YoutubeConfigError, YoutubeTarget, resolve_youtube_target

STREAM_YOUTUBE = {"destination": "youtube", "ingest_url_env": "YOUTUBE_INGEST_URL", "key_env": "YOUTUBE_STREAM_KEY"}

def test_resolve_youtube_target_returns_none_when_destination_is_not_youtube():
    assert resolve_youtube_target({"destination": "none"}) is None

def test_resolve_youtube_target_builds_target_from_env(monkeypatch):
    monkeypatch.setenv("YOUTUBE_INGEST_URL", "rtmps://a.rtmps.youtube.com/live2")
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "abcd-1234")
    target = resolve_youtube_target(STREAM_YOUTUBE)
    assert target.rtmps_url == "rtmps://a.rtmps.youtube.com/live2/abcd-1234"

def test_resolve_youtube_target_raises_when_ingest_url_missing(monkeypatch):
    monkeypatch.delenv("YOUTUBE_INGEST_URL", raising=False)
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "abcd-1234")
    with pytest.raises(YoutubeConfigError):
        resolve_youtube_target(STREAM_YOUTUBE)

def test_resolve_youtube_target_raises_when_stream_key_missing(monkeypatch):
    monkeypatch.setenv("YOUTUBE_INGEST_URL", "rtmps://a.rtmps.youtube.com/live2")
    monkeypatch.delenv("YOUTUBE_STREAM_KEY", raising=False)
    with pytest.raises(YoutubeConfigError):
        resolve_youtube_target(STREAM_YOUTUBE)

def test_rtmps_url_strips_a_trailing_slash_on_the_ingest_url():
    target = YoutubeTarget(ingest_url="rtmps://a.rtmps.youtube.com/live2/", stream_key="k")
    assert target.rtmps_url == "rtmps://a.rtmps.youtube.com/live2/k"

from brewcam.video.youtube import redact_target, redact_stderr

def test_redact_target_never_shows_the_key_or_host():
    target = YoutubeTarget(ingest_url="rtmps://a.rtmps.youtube.com/live2", stream_key="super-secret")
    redacted = redact_target(target)
    assert "super-secret" not in redacted
    assert "a.rtmps.youtube.com" not in redacted

def test_redact_target_handles_none():
    assert "not configured" in redact_target(None)

def test_redact_stderr_masks_rtsp_credentials():
    text = 'Input #0, rtsp, from "rtsp://wb:wyze-secret@wyze-bridge:8554/cam1":'
    redacted = redact_stderr(text)
    assert "wyze-secret" not in redacted

def test_redact_stderr_masks_rtmps_urls():
    text = "Opening 'rtmps://a.rtmps.youtube.com/live2/stream-key-value' for writing"
    redacted = redact_stderr(text)
    assert "stream-key-value" not in redacted
    assert "a.rtmps.youtube.com" not in redacted

def test_redact_stderr_leaves_ordinary_text_unchanged():
    text = "frame=  120 fps= 25 q=23.0 size=    512kB time=00:00:04.80 bitrate= 873.8kbits/s"
    assert redact_stderr(text) == text
