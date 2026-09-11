"""Outbox operations: inspect, replay and purge quarantined deliveries.

Usage (from the edge directory, with the same environment as the service):

    uv run python -m tools.outbox status
    uv run python -m tools.outbox list [--limit N]
    uv run python -m tools.outbox show <id>
    uv run python -m tools.outbox replay <id> [<id> ...]
    uv run python -m tools.outbox replay-all
    uv run python -m tools.outbox purge --older-than-hours H

Quarantined ("dead_letter") rows are readings or ACKs that core refused. Replay
only after fixing the cause (for example, the device now exists in core); the
immutable snapshot is resent unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="tools.outbox", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="counts per status")
    p_list = sub.add_parser("list", help="quarantined entries")
    p_list.add_argument("--limit", type=int, default=50)
    p_show = sub.add_parser("show", help="one entry with its snapshot")
    p_show.add_argument("id", type=int)
    p_replay = sub.add_parser("replay", help="requeue quarantined entries")
    p_replay.add_argument("ids", type=int, nargs="+")
    sub.add_parser("replay-all", help="requeue every quarantined entry")
    p_purge = sub.add_parser("purge", help="delete quarantined entries older than N hours")
    p_purge.add_argument("--older-than-hours", type=float, required=True)
    args = parser.parse_args(argv)

    from shared.infrastructure.database import db, init_db
    from device.infrastructure.outbox.outbox_repository import OutboxRepository

    init_db()
    db.connect(reuse_if_open=True)
    repository = OutboxRepository()
    try:
        if args.command == "status":
            print(json.dumps(repository.count_by_status()))
        elif args.command == "list":
            for entry in repository.find_dead_letters(limit=args.limit):
                print(f"{entry.id}\t{entry.aggregate_type}\t{entry.event_type}\t{entry.created_at}\t{entry.error_message}")
        elif args.command == "show":
            entry = repository.find_by_id(args.id)
            if entry is None:
                print(f"no outbox entry {args.id}", file=sys.stderr)
                return 1
            print(json.dumps({
                "id": entry.id, "status": entry.status, "aggregate_type": entry.aggregate_type,
                "event_type": entry.event_type, "retry_count": entry.retry_count,
                "error_message": entry.error_message, "created_at": str(entry.created_at),
                "payload": json.loads(entry.payload) if entry.payload else None,
            }, indent=2))
        elif args.command == "replay":
            for entry_id in args.ids:
                print(f"{entry_id}: {'requeued' if repository.requeue(entry_id) else 'not quarantined'}")
        elif args.command == "replay-all":
            count = sum(1 for entry in repository.find_dead_letters(limit=10_000) if repository.requeue(entry.id))
            print(f"requeued {count}")
        elif args.command == "purge":
            cutoff = datetime.now(timezone.utc) - timedelta(hours=args.older_than_hours)
            print(f"purged {repository.delete_dead_letters_older_than(cutoff)}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
