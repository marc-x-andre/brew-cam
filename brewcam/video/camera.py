import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_QUERY_TERMS = ("token", "key", "pass", "secret")


class CameraSourceError(Exception):
    pass


def resolve_rtsp_url(camera_config: dict) -> str:
    env_name = camera_config["source_env"]
    url = os.environ.get(env_name)
    if not url:
        raise CameraSourceError(f"environment variable {env_name!r} is not set")
    return url


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    netloc = parts.netloc
    if "@" in netloc:
        _, host = netloc.rsplit("@", 1)
        netloc = f"***:***@{host}"
    redacted_query = urlencode(
        [
            (key, "***" if _is_secret_param(key) else value)
            for key, value in parse_qsl(parts.query)
        ]
    )
    return urlunsplit((parts.scheme, netloc, parts.path, redacted_query, parts.fragment))


def _is_secret_param(name: str) -> bool:
    lowered = name.lower()
    return any(term in lowered for term in _SECRET_QUERY_TERMS)
