"""
log.txt was empty with the default logging level (16.2.0): the discovery lines were INFO and
the level without -v is ERROR. They now always reach the file when --log is on.
"""

from __future__ import annotations

import logging

import pytest

import campaign_sources as cs
from logsetup import configure_logging
from constants import LOGGING_LEVELS


@pytest.fixture
def clean_loggers():
    names = ("TwitchDrops", "TwitchDrops.discovery", "TwitchDrops.gql", "TwitchDrops.websocket")
    saved = {n: (logging.getLogger(n).level, logging.getLogger(n).propagate, list(logging.getLogger(n).handlers)) for n in names}
    yield
    for n, (level, propagate, handlers) in saved.items():
        logger = logging.getLogger(n)
        for h in logger.handlers:
            if h not in handlers:
                h.close()
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate


def test_log_file_gets_the_discovery_lines_with_the_default_level(tmp_path, clean_loggers):
    """
    With no -v, the level is ERROR: INFO lines never reached log.txt, which stayed empty.
    """
    log = tmp_path / "log.txt"
    handler = configure_logging(LOGGING_LEVELS[0], log, debug_gql=0, debug_ws=0)
    logging.getLogger("TwitchDrops").info("an ordinary info line")
    cs.discovery_logger.info("Campaign discovery: source=scan, campaigns=45")
    cs.discovery_logger.info("Discover debug: op=Inventory client=SMARTBOX http=200")
    logging.getLogger("TwitchDrops").error("an error")
    handler.flush()
    text = log.read_text(encoding="utf8")
    assert "Campaign discovery: source=scan" in text
    assert "Discover debug: op=Inventory" in text
    assert "an error" in text
    assert "an ordinary info line" not in text  # the -v level still applies to the rest
    # non-ASCII game names don't break the file (it used the Windows code page)
    cs.discovery_logger.info("Drops scan: Pokémon GO, 原神")
    handler.flush()
    assert "Pokémon GO, 原神" in log.read_text(encoding="utf8")


def test_the_old_setup_dropped_them(tmp_path, clean_loggers):
    """How 16.2.0 did it: the file handler on the main logger only, at ERROR."""
    log = tmp_path / "log.txt"
    logger = logging.getLogger("TwitchDrops")
    logger.setLevel(LOGGING_LEVELS[0])
    handler = logging.FileHandler(log)
    logger.addHandler(handler)
    logging.getLogger("TwitchDrops.old_discovery").info("Campaign discovery: source=scan")
    handler.flush()
    assert log.read_text() == ""
