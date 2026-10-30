import argparse

from brewcam.main import run


def main():
    parser = argparse.ArgumentParser(prog="brewcam")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    run(args.config)


if __name__ == "__main__":
    main()
