"""Connection test: resolve the configured YouTube ingest URL/key from env vars
and push a few seconds of black video to it via ffmpeg, the same way the app's
compose pipeline does. Prints OK/FAIL and exits 0/1.

Usage:
    pipenv run python -m scripts.test_youtube_connection [--config PATH] [--seconds N]
"""
import argparse
import subprocess
import sys

from brewcam.config import load_config
from brewcam.video import youtube


def build_probe_command(stream_config: dict, target: youtube.YoutubeTarget, seconds: int) -> list[str]:
    width, height, fps = stream_config["width"], stream_config["height"], stream_config["fps"]
    bitrate = stream_config.get("bitrate_kbps", 3000)
    gop = str(2 * fps)
    return [
        "ffmpeg", "-nostats", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(seconds),
        "-c:v", "libx264", "-profile:v", "main", "-pix_fmt", "yuv420p",
        "-b:v", f"{bitrate}k", "-minrate", f"{bitrate}k", "-maxrate", f"{bitrate}k",
        "-bufsize", f"{bitrate * 2}k",
        "-g", gop, "-keyint_min", gop, "-sc_threshold", "0", "-bf", "2",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-f", "flv", target.rtmps_url,
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/session.example.yaml")
    parser.add_argument("--seconds", type=int, default=3, help="length of the test clip")
    args = parser.parse_args()

    config = load_config(args.config)
    try:
        target = youtube.resolve_youtube_target(config.stream)
    except youtube.YoutubeConfigError as exc:
        print(f"FAIL: {exc}", file=sys.stdout)
        return 1
    if target is None:
        print("FAIL: stream.destination is not \"youtube\" in this config", file=sys.stdout)
        return 1

    print(f"probing {youtube.redact_target(target)} for {args.seconds}s...", file=sys.stderr)
    command = build_probe_command(config.stream, target, args.seconds)
    try:
        result = subprocess.run(command, capture_output=True, timeout=args.seconds + 15)
    except FileNotFoundError:
        print("FAIL: ffmpeg not found on PATH", file=sys.stdout)
        return 1
    except subprocess.TimeoutExpired:
        print("FAIL: ffmpeg did not exit in time (ingest likely unreachable)", file=sys.stdout)
        return 1

    if result.returncode == 0:
        print("OK", file=sys.stdout)
        return 0

    excerpt = youtube.redact_stderr(result.stderr.decode(errors="replace")).strip().splitlines()[-5:]
    print("FAIL: ffmpeg exited with code " + str(result.returncode), file=sys.stdout)
    for line in excerpt:
        print(f"  {line}", file=sys.stdout)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
