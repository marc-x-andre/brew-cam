import yaml


def test_compose_file_parses_and_has_expected_services():
    with open("docker-compose.yml") as f:
        compose = yaml.safe_load(f)
    assert set(compose["services"]) == {"wyze-bridge", "brewcam"}


def test_compose_ports_are_bound_to_loopback_only():
    with open("docker-compose.yml") as f:
        compose = yaml.safe_load(f)
    ports = compose["services"]["wyze-bridge"].get("ports", [])
    assert ports, "wyze-bridge should publish at least the RTSP and web UI ports"
    for mapping in ports:
        assert str(mapping).startswith("127.0.0.1:"), f"{mapping} is not bound to loopback"
    assert "ports" not in compose["services"]["brewcam"]


def test_compose_file_contains_no_literal_secrets():
    text = open("docker-compose.yml").read()
    for placeholder in ("WYZE_KEY_ID", "WYZE_API_KEY"):
        assert f"${{{placeholder}}}" in text  # substitution syntax present
    assert "@" not in text  # no embedded-credential URL written literally into the compose file
