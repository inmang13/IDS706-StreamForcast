"""``python -m streamforecast config`` prints the resolved settings."""

import argparse
import sys

from streamforecast import config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m streamforecast")
    parser.add_argument("command", choices=["config"])
    parser.parse_args(argv)
    try:
        settings = config.load()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for name, value in config.as_env(settings).items():
        print(f"{name}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
