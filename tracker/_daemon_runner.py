"""
tracker/_daemon_runner.py

Entry point for the background daemon subprocess.
Launched by `track start` via subprocess.Popen.
Never imported directly — always run as __main__.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: python -m tracker._daemon_runner <session_id>", file=sys.stderr)
        sys.exit(1)

    try:
        session_id = int(sys.argv[1])
    except ValueError as exc:
        print(f"Invalid session_id: {sys.argv[1]!r}", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    from tracker.aw_client import ActivityWatchClient
    from tracker.config import load_config
    from tracker.daemon import Daemon
    from tracker.db.connection import open_database
    from tracker.db.repositories import Database

    try:
        config = load_config()
    except FileNotFoundError as exc:
        logger.error("Config not found: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("Failed to load config: %s", exc)
        sys.exit(1)

    try:
        conn = open_database(config.paths.db_path)
        db = Database(conn)
    except Exception as exc:
        logger.error("Failed to open database: %s", exc)
        sys.exit(1)

    aw = ActivityWatchClient(config.api.aw_base_url)

    daemon = Daemon(
        config=config,
        db=db,
        aw=aw,
        session_id=session_id,
    )

    try:
        daemon.run()
    finally:
        db.close()
        aw.close()


if __name__ == "__main__":
    main()
