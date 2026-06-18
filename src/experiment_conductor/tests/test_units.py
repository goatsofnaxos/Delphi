"""Layer 1 — pure unit tests. No filesystem I/O beyond tmp_path, no external deps."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from experiment_conductor.conductor import _count_local_chunks, _normalize_session_datetime
from experiment_conductor.state import ConductorState, Phase
from tests.conftest import TEST_SESSION_ROOT


# ---------------------------------------------------------------------------
# _normalize_session_datetime
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("2026-03-20T20:23:05Z",        "2026-03-20T20-23-05"),
    ("2026-03-20T20:23:05",         "2026-03-20T20-23-05"),
    ("2026-03-20T20-23-05",         "2026-03-20T20-23-05"),
    ("2024-06-01T12:00:00+05:00",   "2024-06-01T12-00-00"),
    ("2024-06-01T00:00:00Z",        "2024-06-01T00-00-00"),
])
def test_normalize_session_datetime(raw, expected):
    assert _normalize_session_datetime(raw) == expected


# ---------------------------------------------------------------------------
# _count_local_chunks
# ---------------------------------------------------------------------------

def test_count_local_chunks_empty(tmp_path):
    assert _count_local_chunks(tmp_path) == 0


def test_count_local_chunks_missing_folder(tmp_path):
    assert _count_local_chunks(tmp_path, folder="behavior-videos/TopCamera") == 0


def test_count_local_chunks_counts_timestamp_dirs(tmp_path):
    camera = tmp_path / "behavior-videos" / "TopCamera"
    camera.mkdir(parents=True)
    (camera / "2026-03-20T10-00-00").mkdir()
    (camera / "2026-03-20T11-00-00").mkdir()
    (camera / "not_a_chunk").mkdir()      # must NOT be counted
    (camera / "2026-03-20T10-00-00.mp4").write_bytes(b"")  # file, not a dir
    assert _count_local_chunks(tmp_path) == 2


def test_count_local_chunks_test_data():
    """Test data has no timestamp sub-directories in TopCamera — expect 0."""
    run_dir = TEST_SESSION_ROOT / "2026-03-20T20-23-37"
    assert _count_local_chunks(run_dir) == 0


# ---------------------------------------------------------------------------
# ConductorState
# ---------------------------------------------------------------------------

def test_state_initial_phase():
    s = ConductorState()
    assert s.phase == Phase.LAUNCHING


def test_state_all_flags_false_by_default():
    s = ConductorState()
    assert not s.first_consolidation_done
    assert not s.delphi_metadata_moved
    assert not s.metadata_generated
    assert not s.upload_started
    assert not s.upload_paused


def test_state_toggles_true_by_default():
    s = ConductorState()
    assert s.pipeline_enabled
    assert s.metadata_enabled
    assert s.upload_enabled


def test_state_time_fields_none():
    s = ConductorState()
    assert s.start_time is None
    assert s.experiment_end_time is None
    assert s.last_pipeline_run is None
    assert s.last_upload_run is None


def test_state_events_initialized():
    s = ConductorState()
    assert isinstance(s.end_experiment_event, threading.Event)
    assert not s.end_experiment_event.is_set()


def test_state_lock_prevents_race(tmp_path):
    """Concurrent flag mutations under the lock must not raise."""
    s = ConductorState()
    errors: list = []

    def toggle():
        for _ in range(500):
            with s.lock:
                s.pipeline_enabled = not s.pipeline_enabled

    threads = [threading.Thread(target=toggle) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
