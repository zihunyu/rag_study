"""Direct migration entry; database implementation awaits its G0 ADR."""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the migration adapter boundary")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        print("migration_port=ready implementation=unapproved real_migrations_applied=false")
        return 0
    print("No migration ran: the control-plane database ADR is not approved.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
