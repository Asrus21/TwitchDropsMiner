"""
The app's logging setup, kept apart from main.py so tests can check it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from constants import FILE_FORMATTER


def configure_logging(
    level: int, log_path: Path | None, *, debug_gql: int, debug_ws: int
) -> logging.FileHandler | None:
    """
    Sets the app's loggers up; with `log_path`, also writes them to that file.

    The campaign discovery summary and the --discover-debug results (the
    "TwitchDrops.discovery" logger) are kept at INFO whatever `level` is, so a log file
    always has them: with the default level (errors only), they used to be dropped and
    log.txt stayed empty. They don't go up to the main logger, because the main window
    gets its own, friendlier lines for them.
    """
    if level > logging.DEBUG:
        # redirect the root logger into a NullHandler, effectively ignoring all logging
        # calls that aren't ours. This always runs, unless the level is DEBUG or lower.
        logging.getLogger().addHandler(logging.NullHandler())
    logger = logging.getLogger("TwitchDrops")
    logger.setLevel(level)
    discovery_logger = logging.getLogger("TwitchDrops.discovery")
    discovery_logger.setLevel(logging.INFO)
    discovery_logger.propagate = False
    handler: logging.FileHandler | None = None
    if log_path is not None:
        # utf8: game names aren't all in the Windows code page
        handler = logging.FileHandler(log_path, encoding="utf8")
        handler.setFormatter(FILE_FORMATTER)
        logger.addHandler(handler)
        discovery_logger.addHandler(handler)
    logging.getLogger("TwitchDrops.gql").setLevel(debug_gql)
    logging.getLogger("TwitchDrops.websocket").setLevel(debug_ws)
    return handler
