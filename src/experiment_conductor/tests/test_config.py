"""Layer 2 — config parsing tests. No rig, no data directory required."""
from __future__ import annotations

import sys

import pytest


def _build(monkeypatch, extra_env: dict | None = None):
    """Helper: patch sys.argv and env, then call build_config()."""
    from experiment_conductor.config import build_config

    monkeypatch.setattr(sys, "argv", ["conductor"])
    monkeypatch.setenv("SUBJECT_ID", "99999")
    monkeypatch.setenv("DATA_ROOT", "C:/tmp/fake_session")
    for k, v in (extra_env or {}).items():
        monkeypatch.setenv(k, v)
    return build_config()


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------

def test_missing_subject_id_raises(tmp_path, monkeypatch):
    from experiment_conductor.config import build_config

    monkeypatch.setattr(sys, "argv", ["conductor"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SUBJECT_ID", raising=False)
    monkeypatch.setenv("DATA_ROOT", "C:/tmp/fake")
    with pytest.raises(ValueError, match="SUBJECT_ID"):
        build_config()


def test_missing_data_root_and_server_root_raises(tmp_path, monkeypatch):
    from experiment_conductor.config import build_config

    monkeypatch.setattr(sys, "argv", ["conductor"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SUBJECT_ID", "99999")
    monkeypatch.delenv("DATA_ROOT", raising=False)
    monkeypatch.delenv("SERVER_ROOT", raising=False)
    with pytest.raises(ValueError):
        build_config()


def test_both_data_root_and_server_root_raises(tmp_path, monkeypatch):
    from experiment_conductor.config import build_config

    monkeypatch.setattr(sys, "argv", ["conductor"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SUBJECT_ID", "99999")
    monkeypatch.setenv("DATA_ROOT", "C:/tmp/session")
    monkeypatch.setenv("SERVER_ROOT", "C:/tmp/server")
    with pytest.raises(ValueError):
        build_config()


# ---------------------------------------------------------------------------
# Experiment type
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("etype", ["delphi", "pirouette", "delphi_pirouette"])
def test_experiment_type_from_env(tmp_path, monkeypatch, etype):
    monkeypatch.chdir(tmp_path)
    cfg = _build(monkeypatch, {"EXPERIMENT_TYPE": etype})
    assert cfg.experiment_type == etype


# ---------------------------------------------------------------------------
# dry_run
# ---------------------------------------------------------------------------

def test_dry_run_env_false_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = _build(monkeypatch)
    assert cfg.dry_run is False


def test_dry_run_env_true(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = _build(monkeypatch, {"DRY_RUN": "true"})
    assert cfg.dry_run is True


def test_dry_run_cli_overrides_env(tmp_path, monkeypatch):
    from experiment_conductor.config import build_config

    monkeypatch.setattr(sys, "argv", ["conductor", "--dry-run"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SUBJECT_ID", "99999")
    monkeypatch.setenv("DATA_ROOT", "C:/tmp/fake")
    monkeypatch.setenv("DRY_RUN", "false")
    cfg = build_config()
    assert cfg.dry_run is True


# ---------------------------------------------------------------------------
# schedule_minute_of_hour
# ---------------------------------------------------------------------------

def test_schedule_minute_none_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCHEDULE_MINUTE_OF_HOUR", raising=False)
    cfg = _build(monkeypatch)
    assert cfg.schedule_minute_of_hour is None


def test_schedule_minute_from_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = _build(monkeypatch, {"SCHEDULE_MINUTE_OF_HOUR": "45"})
    assert cfg.schedule_minute_of_hour == 45


# ---------------------------------------------------------------------------
# keep_local_patterns
# ---------------------------------------------------------------------------

def test_keep_local_patterns_default_non_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = _build(monkeypatch)
    assert len(cfg.keep_local_patterns) > 0
    assert any("delphi_dataset.csv" in p for p in cfg.keep_local_patterns)


# ---------------------------------------------------------------------------
# enable flags
# ---------------------------------------------------------------------------

def test_enable_flags_default_true(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = _build(monkeypatch)
    assert cfg.enable_pipeline is True
    assert cfg.enable_metadata is True
    assert cfg.enable_upload is True


def test_disable_upload_via_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = _build(monkeypatch, {"ENABLE_UPLOAD": "false"})
    assert cfg.enable_upload is False


# ---------------------------------------------------------------------------
# cadence scheduler
# ---------------------------------------------------------------------------

def test_cadence_scheduler_interval_mode():
    from experiment_conductor.conductor import _CadenceScheduler

    calls = []
    sched = _CadenceScheduler(callback=lambda: calls.append(1), interval_seconds=0.1)
    sched.start()
    import time
    time.sleep(0.35)
    sched.stop()
    assert len(calls) >= 2, f"Expected >=2 ticks, got {len(calls)}"


def test_cadence_scheduler_invalid_minute():
    from experiment_conductor.conductor import _CadenceScheduler

    with pytest.raises(ValueError):
        _CadenceScheduler(callback=lambda: None, on_minute_of_hour=60)
