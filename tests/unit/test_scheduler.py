"""AC-6.6, 6.9, 6.10: the scheduler, with stage subprocesses replaced by a recorder."""

import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from streamforecast import config, logs, paths, scheduler

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


class Recorder:
    """Stands in for run_stage: records calls and returns preset exit codes."""

    def __init__(self, codes=None, on_call=None):
        self.calls = []
        self.codes = codes or {}
        self.on_call = on_call

    def __call__(self, stage):
        self.calls.append(stage)
        if self.on_call:
            self.on_call(stage)
        return self.codes.get(stage, 0)


@pytest.fixture
def env(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(paths, "utc_now", lambda: NOW)
    settings = config.load()
    return settings, logs.get_logger("scheduler", settings)


def publish_checkpoint(data_dir, age_days, with_sidecar=True):
    models = data_dir / "models"
    models.mkdir(exist_ok=True)
    stamp = paths.timestamp(NOW - timedelta(days=age_days))
    (models / f"model_{stamp}.joblib").write_bytes(b"x")
    if with_sidecar:
        (models / f"metrics_{stamp}.json").write_text("{}")


# --- AC-6.6 ---------------------------------------------------------------------


def test_scheduler_runs_stages_in_order(env):
    settings, log = env
    runner = Recorder()
    assert scheduler.run_pass(settings, log, runner) == 0
    assert runner.calls == ["ingest", "features", "train", "forecast"]


@pytest.mark.parametrize(
    "age_days, with_sidecar, trains",
    [
        (None, True, True),  # no checkpoint at all
        (1, True, False),  # fresh checkpoint
        (7, True, False),  # exactly RETRAIN_DAYS old: not yet
        (8, True, True),  # older than RETRAIN_DAYS
        (1, False, True),  # a checkpoint without its sidecar is unpublished
    ],
)
def test_scheduler_retrain_only_when_stale(
    env, tmp_data_dir, capsys, age_days, with_sidecar, trains
):
    settings, log = env
    if age_days is not None:
        publish_checkpoint(tmp_data_dir, age_days, with_sidecar)
    runner = Recorder()
    scheduler.run_pass(settings, log, runner)
    assert ("train" in runner.calls) is trains
    if not trains:
        assert "train=skipped" in capsys.readouterr().err


def test_retrain_days_comes_from_config(env, tmp_data_dir, monkeypatch):
    monkeypatch.setenv("RETRAIN_DAYS", "30")
    publish_checkpoint(tmp_data_dir, 8)
    runner = Recorder()
    scheduler.run_pass(config.load(), env[1], runner)
    assert "train" not in runner.calls


def test_scheduler_continues_after_failure(env):
    settings, log = env
    runner = Recorder(codes={"ingest": 1, "features": 1, "train": 1})
    assert scheduler.run_pass(settings, log, runner) == 0
    assert runner.calls == ["ingest", "features", "train", "forecast"]


def test_serve_sleeps_between_passes_and_stops_quickly(env, capsys):
    settings, log = env
    stop = threading.Event()
    runner = Recorder()
    thread = threading.Thread(
        target=scheduler.serve, args=(settings, log, runner, stop), daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 5
    while "sleeping 24h" not in capsys.readouterr().err:
        assert time.monotonic() < deadline, "no 'sleeping 24h' line"
        time.sleep(0.05)
    started = time.monotonic()
    stop.set()  # what the SIGTERM handler does
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert time.monotonic() - started < 2  # far inside the 10 s budget
    assert runner.calls == ["ingest", "features", "train", "forecast"]  # one pass


def test_scheduler_sigterm_stops_quickly(env):
    """The installed handler turns SIGTERM into a stop; a running stage is ended."""
    settings, log = env
    stop = threading.Event()
    previous = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        scheduler.install_signal_handlers(stop, log)
        handler = signal.getsignal(signal.SIGTERM)
        handler(signal.SIGTERM, None)
        assert stop.is_set()
    finally:
        signal.signal(signal.SIGTERM, previous[0])
        signal.signal(signal.SIGINT, previous[1])


def test_run_stage_terminates_a_running_stage_on_stop(monkeypatch):
    """A long stage is ended within seconds once stop is set (no network involved)."""
    real_popen = scheduler.subprocess.Popen
    launched = []

    def sleeper(args):
        launched.append(args)
        return real_popen([sys.executable, "-c", "import time; time.sleep(60)"])

    monkeypatch.setattr(scheduler.subprocess, "Popen", sleeper)
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    started = time.monotonic()
    code = scheduler.run_stage("ingest", stop)
    assert time.monotonic() - started < 8
    assert code != 0  # terminated, not finished
    assert launched == [[sys.executable, "-m", "streamforecast.ingest"]]


def test_stop_during_pass_skips_remaining_stages(env, capsys):
    settings, log = env
    stop = threading.Event()
    runner = Recorder(on_call=lambda stage: stage == "features" and stop.set())
    assert scheduler.run_pass(settings, log, runner, stop) == 1
    assert runner.calls == ["ingest", "features"]
    assert "train=stopped forecast=stopped" in capsys.readouterr().err


# --- AC-6.9 ---------------------------------------------------------------------


@pytest.mark.parametrize("forecast_code", [0, 1])
def test_scheduler_once_exit_code(env, monkeypatch, forecast_code):
    runner = Recorder(codes={"forecast": forecast_code})
    monkeypatch.setattr(scheduler, "run_stage", lambda stage, stop: runner(stage))
    # Keep pytest's own Ctrl+C handling (the handler is tested separately).
    monkeypatch.setattr(scheduler, "install_signal_handlers", lambda stop, log: None)
    assert scheduler.main(["--once"]) == forecast_code
    assert runner.calls[-1] == "forecast"


# --- AC-6.10 --------------------------------------------------------------------


def test_scheduler_soft_failures(env, tmp_data_dir, capsys):
    settings, log = env
    publish_checkpoint(tmp_data_dir, 1)
    runner = Recorder(codes={"ingest": 1, "features": 1, "forecast": 0})
    assert scheduler.run_pass(settings, log, runner) == 0  # the pass exit is Forecast's
    assert runner.calls == ["ingest", "features", "forecast"]
    err = capsys.readouterr().err
    assert "pass summary: ingest=1 features=1 train=skipped forecast=0" in err
    assert "WARNING scheduler ingest exited 1; continuing with the next stage" in err

    runner = Recorder(codes={"forecast": 1})
    assert scheduler.run_pass(settings, log, runner) == 1  # a refusal fails the pass
