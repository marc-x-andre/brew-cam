import os
import re
from dataclasses import dataclass

from brewcam.config import monitoring


class YoutubeConfigError(Exception):
    pass


@dataclass
class YoutubeTarget:
    ingest_url: str
    stream_key: str

    @property
    def rtmps_url(self) -> str:
        return f"{self.ingest_url.rstrip('/')}/{self.stream_key}"


def resolve_youtube_target(stream_config: dict) -> YoutubeTarget | None:
    if stream_config.get("destination") != "youtube":
        return None
    ingest_url = os.environ.get(stream_config["ingest_url_env"])
    stream_key = os.environ.get(stream_config["key_env"])
    if not ingest_url or not stream_key:
        raise YoutubeConfigError("YouTube destination is configured but the ingestion URL or stream key is unset")
    return YoutubeTarget(ingest_url=ingest_url, stream_key=stream_key)


def redact_target(target: YoutubeTarget | None) -> str:
    if target is not None:
        return "rtmps://<redacted>"
    return "rtmps://<not configured>"


def redact_stderr(text: str) -> str:
    text = re.sub(r"rtsp://[^/@\s]+@", "rtsp://***:***@", text)
    return re.sub(r"rtmps?://\S+", "rtmps://<redacted>", text)


def resolve_or_degrade(state, stream_config: dict, monitoring_dir: str, now_local, retention_days: int):
    """Resolve the YouTube target, degrading to None (and logging once) on misconfiguration."""
    try:
        target = resolve_youtube_target(stream_config)
        state.youtube_error_logged = False
        return target
    except YoutubeConfigError:
        if not state.youtube_error_logged:
            monitoring.write_event(monitoring_dir, now_local, "youtube_unavailable", {}, retention_days)
            state.youtube_error_logged = True
        return None
