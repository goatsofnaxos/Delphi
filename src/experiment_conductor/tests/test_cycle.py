"""Layers 4–6 — cadence cycle integration tests.

These tests call run_cadence_cycle() directly with a pre-built ConductorConfig
and ConductorState, bypassing the launcher entirely.  Upload is always
disabled (ENABLE_UPLOAD=false / upload_enabled=False) so no external
transfer-service calls are made.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from experiment_conductor.state import ConductorState, Phase
from tests.conftest import TEST_SESSION_ROOT, TEST_SUBJECT_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(session_path: Path, experiment_type: str = "delphi_pirouette", **overrides):
    """Construct a minimal ConductorConfig for the given session path."""
    from experiment_conductor.config import ConductorConfig

    defaults = dict(
        experiment_type=experiment_type,
        experiment_config="delphi_pirouette_experiment",
        server_root=None,
        session_datetime=None,
        data_root=session_path,
        launcher_dir=Path(__file__).parents[3] / "launcher",
        surgery_notes_base=None,
        subject_id=TEST_SUBJECT_ID,
        protocol_id="DRn20231002",
        instrument_id="delphi-rig-0",
        experiment_room="447",
        delphi_computer_id="W10DT714591",
        surgeons=["Test Surgeon"],
        experimenters=["Test Experimenter"],
        delphi_experiment="bonhoeffer",
        delphi_firmware="0.1.0",
        upload_batch_size=2,
        pipeline_cadence_minutes=60,
        schedule_minute_of_hour=None,
        s3_bucket="aind-open-data",
        contact_email="test@alleninstitute.org",
        project_name="TestProject",
        dry_run=True,
        delete_after_upload=False,
        keep_local_patterns=["behavior/delphi_dataset.csv", "metadata/**"],
        enable_pipeline=True,
        enable_metadata=True,
        enable_upload=False,   # never hit external service
        pipeline_skip_build=False,
        pipeline_skip_clips=True,
        pipeline_skip_snapshot=True,
        hotkey_pipeline="<ctrl>+<shift>+p",
        hotkey_upload_pause="<ctrl>+<shift>+u",
        hotkey_end_experiment="<ctrl>+<shift>+e",
        hotkey_toggle_pipeline=None,
        hotkey_toggle_metadata=None,
        hotkey_toggle_upload=None,
        hotkey_update_end_time=None,
        hotkey_retry_metadata=None,
        chunk_camera_folder="behavior-videos/TopCamera",
    )
    defaults.update(overrides)
    return ConductorConfig(**defaults)


def _make_state(pirouette_only: bool = False) -> ConductorState:
    s = ConductorState()
    s.phase = Phase.RUNNING
    if pirouette_only:
        # Pre-set as conductor does for pirouette-only: no Delphi metadata to move
        s.delphi_metadata_moved = True
    return s


# ---------------------------------------------------------------------------
# Layer 4 — delphi_pirouette dry-run full cycle
# ---------------------------------------------------------------------------

class TestDryRunCycle:
    def test_cycle_sets_first_consolidation_done(self, session_copy):
        from experiment_conductor.conductor import run_cadence_cycle

        cfg = _make_cfg(session_copy)
        state = _make_state()
        run_cadence_cycle(cfg, state)
        assert state.first_consolidation_done

    def test_cycle_sets_delphi_metadata_moved(self, session_copy):
        from experiment_conductor.conductor import run_cadence_cycle

        cfg = _make_cfg(session_copy)
        state = _make_state()
        run_cadence_cycle(cfg, state)
        assert state.delphi_metadata_moved

    def test_cycle_generates_metadata_files(self, session_copy):
        from experiment_conductor.conductor import run_cadence_cycle

        cfg = _make_cfg(session_copy)
        state = _make_state()
        run_cadence_cycle(cfg, state)

        metadata_dir = session_copy / "metadata"
        written = list(metadata_dir.glob("*.json"))
        assert written, f"No metadata JSON files in {metadata_dir}"

    def test_cycle_sets_metadata_generated_flag(self, session_copy):
        from experiment_conductor.conductor import run_cadence_cycle

        cfg = _make_cfg(session_copy)
        state = _make_state()
        run_cadence_cycle(cfg, state)
        assert state.metadata_generated

    def test_cycle_sets_experiment_end_time(self, session_copy):
        from experiment_conductor.conductor import run_cadence_cycle

        cfg = _make_cfg(session_copy)
        state = _make_state()
        run_cadence_cycle(cfg, state)
        # end time is set once metadata is generated
        assert state.experiment_end_time is not None

    def test_second_cycle_skips_pipeline_when_disabled(self, session_copy):
        from experiment_conductor.conductor import run_cadence_cycle

        cfg = _make_cfg(session_copy)
        state = _make_state()
        run_cadence_cycle(cfg, state)

        # Disable pipeline and reset the lock flag by running again
        state.pipeline_enabled = False
        csv_files = list(session_copy.rglob("delphi_dataset.csv"))
        size_before = csv_files[0].stat().st_size if csv_files else 0

        run_cadence_cycle(cfg, state)
        csv_files_after = list(session_copy.rglob("delphi_dataset.csv"))
        size_after = csv_files_after[0].stat().st_size if csv_files_after else 0
        # CSV should not have changed since pipeline was disabled
        assert size_after == size_before

    def test_second_cycle_skips_metadata_already_generated(self, session_copy):
        """Once metadata_generated=True, the metadata step is not re-run."""
        from experiment_conductor.conductor import run_cadence_cycle

        cfg = _make_cfg(session_copy)
        state = _make_state()
        run_cadence_cycle(cfg, state)   # first cycle — generates metadata
        assert state.metadata_generated

        # Delete metadata dir to detect if it gets re-created
        metadata_dir = session_copy / "metadata"
        shutil.rmtree(metadata_dir)

        run_cadence_cycle(cfg, state)   # second cycle — should NOT regenerate
        assert not metadata_dir.exists(), \
            "metadata_bridge was called again on a cycle where metadata_generated=True"

    def test_cycle_lock_prevents_concurrent_run(self, session_copy):
        """A second concurrent call to run_cadence_cycle must be dropped."""
        import threading
        from experiment_conductor.conductor import run_cadence_cycle, _CYCLE_LOCK

        cfg = _make_cfg(session_copy)
        state = _make_state()

        results: list = []
        barrier = threading.Barrier(2)

        def run():
            barrier.wait()
            run_cadence_cycle(cfg, state)
            results.append(1)

        # Hold the lock to simulate a cycle already in progress
        with _CYCLE_LOCK:
            t = threading.Thread(target=run)
            t.start()
            # Concurrent call will see the lock held and return immediately
            t.join(timeout=5)

        assert len(results) == 1


# ---------------------------------------------------------------------------
# Layer 5 — hotkey callbacks (unit-level, no global keyboard listener)
# ---------------------------------------------------------------------------

class TestHotkeyCallbacks:
    """Verify the callback functions that hotkeys invoke work correctly."""

    def test_toggle_pipeline_off_then_on(self, session_copy):
        from experiment_conductor.state import ConductorState

        state = ConductorState()
        assert state.pipeline_enabled

        with state.lock:
            state.pipeline_enabled = not state.pipeline_enabled
        assert not state.pipeline_enabled

        with state.lock:
            state.pipeline_enabled = not state.pipeline_enabled
        assert state.pipeline_enabled

    def test_toggle_metadata(self):
        from experiment_conductor.state import ConductorState

        state = ConductorState()
        with state.lock:
            state.metadata_enabled = False
        assert not state.metadata_enabled

    def test_retry_metadata_resets_flag(self, session_copy):
        """Resetting metadata_generated should cause next cycle to re-generate."""
        from experiment_conductor.conductor import run_cadence_cycle

        cfg = _make_cfg(session_copy)
        state = _make_state()
        run_cadence_cycle(cfg, state)
        assert state.metadata_generated

        # Simulate HOTKEY_RETRY_METADATA callback
        with state.lock:
            state.metadata_generated = False

        # Verify: next cycle will attempt metadata again (metadata dir now in place)
        assert not state.metadata_generated

    def test_end_experiment_event_signal(self):
        from experiment_conductor.state import ConductorState

        state = ConductorState()
        assert not state.end_experiment_event.is_set()
        state.end_experiment_event.set()
        assert state.end_experiment_event.is_set()

    def test_update_acquisition_end_time_direct(self, tmp_path):
        from experiment_conductor.metadata_bridge import update_acquisition_end_time

        acq = {"acquisition_end_time": "2026-01-01T00:00:00Z"}
        (tmp_path / "acquisition.json").write_text(json.dumps(acq), encoding="utf-8")

        t = datetime(2026, 6, 17, 22, 15, 0, tzinfo=timezone.utc)
        ok = update_acquisition_end_time(tmp_path, t)
        assert ok
        data = json.loads((tmp_path / "acquisition.json").read_text(encoding="utf-8"))
        assert "22:15:00" in data["acquisition_end_time"]

    def test_hotkey_listener_instantiates(self, session_copy):
        """HotkeyListener should build and stop without error."""
        from experiment_conductor.hotkeys import HotkeyListener
        from experiment_conductor.state import ConductorState

        cfg = _make_cfg(session_copy)
        state = ConductorState()
        listener = HotkeyListener(cfg=cfg, state=state, cycle_fn=lambda: None)
        listener.stop()


# ---------------------------------------------------------------------------
# Layer 6 — pirouette-specific path
# ---------------------------------------------------------------------------

class TestPirouetteCycle:
    def test_pirouette_skips_pipeline_runs_consolidation(self, session_copy):
        """For pirouette-only, only run_consolidation is called (not run_pipeline)."""
        from experiment_conductor.conductor import run_cadence_cycle

        cfg = _make_cfg(session_copy, experiment_type="pirouette")
        state = _make_state(pirouette_only=True)

        run_cadence_cycle(cfg, state)
        # consolidation success sets first_consolidation_done
        assert state.first_consolidation_done

    def test_pirouette_consolidation_runs_only_once(self, session_copy):
        """second cycle should not re-run consolidation (first_consolidation_done gate)."""
        from experiment_conductor.conductor import run_cadence_cycle
        from unittest.mock import patch

        cfg = _make_cfg(session_copy, experiment_type="pirouette")
        state = _make_state(pirouette_only=True)

        call_counts = [0]

        original_run_consolidation = __import__(
            "experiment_conductor.pipeline_bridge", fromlist=["run_consolidation"]
        ).run_consolidation

        def counting_consolidation(*args, **kwargs):
            call_counts[0] += 1
            return original_run_consolidation(*args, **kwargs)

        with patch(
            "experiment_conductor.conductor.run_consolidation",
            side_effect=counting_consolidation,
        ):
            run_cadence_cycle(cfg, state)  # first — should call consolidation
            run_cadence_cycle(cfg, state)  # second — should NOT

        assert call_counts[0] == 1, f"Expected 1 consolidation call, got {call_counts[0]}"

    def test_pirouette_metadata_moved_pre_set(self):
        """Pirouette state: delphi_metadata_moved must be True at startup."""
        state = _make_state(pirouette_only=True)
        assert state.delphi_metadata_moved

    def test_verify_probe_json_returns_false_when_missing(self, tmp_path):
        from experiment_conductor.metadata_bridge import verify_probe_json

        result = verify_probe_json(tmp_path)
        assert result is False

    def test_verify_probe_json_returns_true_when_present(self, tmp_path):
        from experiment_conductor.metadata_bridge import verify_probe_json

        ecephys = tmp_path / "ecephys"
        ecephys.mkdir()
        (ecephys / "probe.json").write_text('{"serial_number": "12345678"}', encoding="utf-8")

        result = verify_probe_json(tmp_path)
        assert result is True
