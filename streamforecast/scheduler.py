"""Stage 6: run the pipeline stages on a schedule (the pipeline container's process).

Each pass runs ``ingest -> features -> train (only if needed) -> forecast`` as
subprocesses (``python -m streamforecast.<stage>``), never by import (D11). A failed
stage never stops the pass: Forecast is the freshness gate (AC-6.10). Train runs only
when there is no published checkpoint or the newest is older than ``RETRAIN_DAYS``
(D5). Between passes the process waits ``RUN_INTERVAL_HOURS``; SIGTERM or SIGINT
stops it promptly, terminating a running stage (AC-6.6).

``python -m streamforecast.scheduler --once`` runs one pass and exits with Forecast's
exit code (AC-6.9, ``make pipeline``).
"""

import argparse
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from logging import Logger
from pathlib import Path

from streamforecast import config, logs, paths

STAGES = ("ingest", "features", "train", "forecast")
POLL_S = 0.5
TERMINATE_GRACE_S = 5

Runner = Callable[[str], int]


def run_stage(stage: str, stop: threading.Event) -> int:
    """Run one stage as a subprocess; terminate it if ``stop`` is set meanwhile."""
    proc = subprocess.Popen([sys.executable, "-m", f"streamforecast.{stage}"])
    while proc.poll() is None:
        if stop.wait(POLL_S):
            proc.terminate()
            try:
                proc.wait(TERMINATE_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            break
    return proc.returncode


def newest_published_age(models_dir: Path, now: datetime) -> timedelta | None:
    """Age of the newest checkpoint that has its metrics sidecar, from its <ts>."""
    for path in sorted(models_dir.glob("model_*.joblib"), reverse=True):
        stamp = path.stem.removeprefix("model_")
        if (models_dir / f"metrics_{stamp}.json").exists():
            made = datetime.strptime(stamp, paths.TIMESTAMP_FORMAT)
            return now - made.replace(tzinfo=timezone.utc)
    return None


def needs_training(settings: config.Settings, now: datetime) -> tuple[bool, str]:
    age = newest_published_age(paths.subdir("models", settings), now)
    if age is None:
        return True, "no checkpoint"
    if age > timedelta(days=settings.retrain_days):
        return True, f"newest checkpoint is {age.days} days old"
    return False, f"newest checkpoint is {age.days} days old"


def run_pass(
    settings: config.Settings,
    log: Logger,
    runner: Runner,
    stop: threading.Event | None = None,
) -> int:
    """One pass over every stage; returns Forecast's exit code (AC-6.10).

    Stages after a stop request are not started (reported as ``stopped``).
    """
    results = {}
    for stage in STAGES:
        if stop is not None and stop.is_set():
            results[stage] = "stopped"
            continue
        if stage == "train":
            needed, why = needs_training(settings, paths.utc_now())
            if not needed:
                log.info("train: skipped (%s; limit %d)", why, settings.retrain_days)
                results[stage] = "skipped"
                continue
            log.info("train: needed (%s)", why)
        log.info("running %s", stage)
        code = runner(stage)
        results[stage] = code
        if code != 0:
            log.warning("%s exited %s; continuing with the next stage", stage, code)
        else:
            log.info("%s exited 0", stage)
    log.info(
        "pass summary: %s",
        " ".join(f"{name}={value}" for name, value in results.items()),
    )
    forecast = results["forecast"]
    return forecast if isinstance(forecast, int) else 1


def _hours(h: float) -> str:
    return f"{h:g}h"


def serve(
    settings: config.Settings, log: Logger, runner: Runner, stop: threading.Event
) -> int:
    """Run passes until ``stop`` is set; a pass starts immediately on start (D5)."""
    while not stop.is_set():
        run_pass(settings, log, runner, stop)
        if stop.is_set():
            break
        log.info("sleeping %s", _hours(settings.run_interval_hours))
        stop.wait(settings.run_interval_hours * 3600)
    log.info("stopping")
    return 0


def install_signal_handlers(stop: threading.Event, log: Logger) -> None:
    def handle(signum, frame):
        log.info("received %s; stopping", signal.Signals(signum).name)
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m streamforecast.scheduler")
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    args = parser.parse_args(argv)
    try:
        settings = config.load()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    log = logs.get_logger("scheduler", settings)
    stop = threading.Event()
    install_signal_handlers(stop, log)

    def runner(stage: str) -> int:
        return run_stage(stage, stop)

    if args.once:
        return run_pass(settings, log, runner, stop)
    return serve(settings, log, runner, stop)


if __name__ == "__main__":
    sys.exit(main())
