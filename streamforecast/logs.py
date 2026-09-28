"""Stage loggers: ``2026-09-23T14:00:00Z INFO ingest usgs: fetched 7 rows ...``."""

import logging
import sys
import time

from streamforecast import config

FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class _StderrHandler(logging.StreamHandler):
    """Writes to whatever ``sys.stderr`` is at emit time (so test capture works)."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value):
        pass


def get_logger(stage: str, settings: config.Settings | None = None) -> logging.Logger:
    settings = settings or config.load()
    logger = logging.getLogger(stage)
    if not logger.handlers:
        formatter = logging.Formatter(FORMAT, DATE_FORMAT)
        formatter.converter = time.gmtime
        handler = _StderrHandler()
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(settings.log_level.upper())
    return logger
