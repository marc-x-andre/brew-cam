STANDBY_SOURCE = {"mode": "image", "path": "assets/standby.png"}


def resolve_display_source(configured_mode: dict, status: str) -> dict:
    if configured_mode["mode"] == "camera" and status != "connected":
        return {"mode": "black"}
    return configured_mode
