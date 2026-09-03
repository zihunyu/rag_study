"""Signal-forwarding wrapper that embeds ownership metadata in its command line."""

from __future__ import annotations

import argparse
import signal
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-token", required=True)
    parser.add_argument("--owned-cwd", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        raise ValueError("owned process command is required")
    child = subprocess.Popen(command, cwd=args.owned_cwd)  # noqa: S603

    def forward(signum, frame) -> None:  # noqa: ANN001
        del frame
        if child.poll() is None:
            child.send_signal(signum)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    return child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
