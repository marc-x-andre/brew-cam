import argparse
import sys

from brewcam.config import ConfigError
from brewcam.main import run


def main():
    parser = argparse.ArgumentParser(prog="brewcam")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        run(args.config)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
