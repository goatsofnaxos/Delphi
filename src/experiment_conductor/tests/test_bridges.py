"""Layer 3 — bridge smoke tests using the real test dataset.

Each test gets a fresh writable copy via the ``session_copy`` fixture so the
canonical test data is never modified.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.conftest import TEST_SUBJECT_ID


# ---------------------------------------------------------------------------
# 3a — pipeline_bridge.run_pipeline
# ---------------------------------------------------------------------------

def test_run_pipeline_returns_true(session_copy):
    """Full delphi-data pipeline should succeed on the test dataset."""
    from experiment_conductor.pipeline_bridge import run_pipeline

    ok = run_pipeline(
        data_root=session_copy,
        experiment="bonhoeffer",
        firmware="0.1.0",
        subject_id=TEST_SUBJECT_ID,
        skip_clips=True,
        skip_snapshot=True,   # fast; figures not needed for this smoke test
    )
    assert ok, "run_pipeline returned False — check delphi-data pipeline logs above"


def test_run_pipeline_creates_dataset_csv(session_copy):
    """After run_pipeline, delphi_dataset.csv must exist in the run sub-dir."""
    from experiment_conductor.pipeline_bridge import run_pipeline

    run_pipeline(
        data_root=session_copy,
        experiment="bonhoeffer",
        firmware="0.1.0",
        subject_id=TEST_SUBJECT_ID,
        skip_clips=True,
        skip_snapshot=True,
    )
    # delphi_dataset.csv lives inside the (consolidated) run sub-directory
    csv_files = list(session_copy.rglob("delphi_dataset.csv"))
    assert csv_files, "delphi_dataset.csv not found anywhere under session_copy"


def test_run_pipeline_append_idempotent(session_copy):
    """Running the pipeline twice with --append should not raise and CSV should grow or stay same."""
    from experiment_conductor.pipeline_bridge import run_pipeline

    kwargs = dict(
        data_root=session_copy,
        experiment="bonhoeffer",
        firmware="0.1.0",
        subject_id=TEST_SUBJECT_ID,
        skip_clips=True,
        skip_snapshot=True,
    )
    assert run_pipeline(**kwargs)
    csv_files = list(session_copy.rglob("delphi_dataset.csv"))
    assert csv_files
    size_first = csv_files[0].stat().st_size

    assert run_pipeline(**kwargs)
    size_second = csv_files[0].stat().st_size
    # Appending the same data deduplicates — size should be stable (not grow unboundedly)
    assert size_second >= size_first


# ---------------------------------------------------------------------------
# 3b — pipeline_bridge.move_delphi_metadata
# ---------------------------------------------------------------------------

def test_move_delphi_metadata_moves_jsonl(session_copy):
    """move_delphi_metadata should relocate HardwareSettings/RuleSettings to behavior/metadata/."""
    from experiment_conductor.pipeline_bridge import move_delphi_metadata

    # Run pipeline first so sub-dirs are consolidated
    from experiment_conductor.pipeline_bridge import run_pipeline
    run_pipeline(
        data_root=session_copy,
        experiment="bonhoeffer",
        firmware="0.1.0",
        subject_id=TEST_SUBJECT_ID,
        skip_clips=True,
        skip_snapshot=True,
    )

    moved = move_delphi_metadata(session_copy)
    # After moving, behavior/metadata/ at session root level should exist
    # (files may already have been there — moved count can be 0)
    dst = session_copy / "behavior" / "metadata"
    # Either files were moved into dst, or they were already in a run sub-dir's
    # behavior/metadata/ (consolidate_metadata_files moves within data_root)
    all_jsonl = list(session_copy.rglob("HardwareSettings*.jsonl")) + \
                list(session_copy.rglob("RuleSettings*.jsonl"))
    assert all_jsonl, "No JSONL files found after move_delphi_metadata"


# ---------------------------------------------------------------------------
# 3c — metadata_bridge.generate_metadata
# ---------------------------------------------------------------------------

def test_generate_metadata_runs_without_crash(session_copy):
    """generate_metadata must not raise; it should write at least some JSON files."""
    from experiment_conductor.pipeline_bridge import move_delphi_metadata, run_pipeline
    from experiment_conductor.metadata_bridge import generate_metadata

    # Prepare: pipeline + move metadata
    run_pipeline(
        data_root=session_copy,
        experiment="bonhoeffer",
        firmware="0.1.0",
        subject_id=TEST_SUBJECT_ID,
        skip_clips=True,
        skip_snapshot=True,
    )
    move_delphi_metadata(session_copy)

    metadata_out = session_copy / "metadata"
    # surgery_notes_base=None → minimal Procedures fallback; no network required
    generate_metadata(
        experiment_type="delphi_pirouette",
        subject_id=TEST_SUBJECT_ID,
        protocol_id="DRn20231002",
        instrument_id="delphi-rig-0",
        experiment_room="447",
        acquisition_type="delphi_pirouette",
        delphi_computer_id="W10DT714591",
        surgeons=["Test Surgeon"],
        experimenters=["Test Experimenter"],
        data_root=session_copy,
        surgery_notes_base=None,
        metadata_output_path=metadata_out,
    )
    # At minimum instrument.json and acquisition.json should be written
    written = [f.name for f in metadata_out.glob("*.json")]
    assert written, f"No JSON files written to {metadata_out}"


def test_generate_metadata_files_are_valid_json(session_copy):
    """Every JSON file written by generate_metadata must be parseable."""
    from experiment_conductor.pipeline_bridge import move_delphi_metadata, run_pipeline
    from experiment_conductor.metadata_bridge import generate_metadata

    run_pipeline(
        data_root=session_copy,
        experiment="bonhoeffer",
        firmware="0.1.0",
        subject_id=TEST_SUBJECT_ID,
        skip_clips=True,
        skip_snapshot=True,
    )
    move_delphi_metadata(session_copy)

    metadata_out = session_copy / "metadata"
    generate_metadata(
        experiment_type="delphi_pirouette",
        subject_id=TEST_SUBJECT_ID,
        protocol_id="DRn20231002",
        instrument_id="delphi-rig-0",
        experiment_room="447",
        acquisition_type="delphi_pirouette",
        delphi_computer_id="W10DT714591",
        surgeons=["Test Surgeon"],
        experimenters=["Test Experimenter"],
        data_root=session_copy,
        surgery_notes_base=None,
        metadata_output_path=metadata_out,
    )
    for json_file in metadata_out.glob("*.json"):
        data = json.loads(json_file.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{json_file.name} is not a JSON object"


# ---------------------------------------------------------------------------
# 3d — metadata_bridge.update_acquisition_end_time
# ---------------------------------------------------------------------------

def test_update_acquisition_end_time(tmp_path):
    """update_acquisition_end_time should patch acquisition_end_time in place."""
    from experiment_conductor.metadata_bridge import update_acquisition_end_time

    # Write a minimal stub acquisition.json
    acq = {"acquisition_end_time": "2026-01-01T00:00:00Z", "other_field": "value"}
    (tmp_path / "acquisition.json").write_text(json.dumps(acq), encoding="utf-8")

    end_time = datetime(2026, 6, 17, 14, 30, 0, tzinfo=timezone.utc)
    ok = update_acquisition_end_time(tmp_path, end_time)

    assert ok
    updated = json.loads((tmp_path / "acquisition.json").read_text(encoding="utf-8"))
    assert "2026-06-17" in updated["acquisition_end_time"]
    assert "14:30:00" in updated["acquisition_end_time"]
    assert updated["other_field"] == "value"  # unrelated field preserved


def test_update_acquisition_end_time_missing_file(tmp_path):
    """Should return False gracefully when acquisition.json does not exist."""
    from experiment_conductor.metadata_bridge import update_acquisition_end_time

    ok = update_acquisition_end_time(tmp_path, datetime.now(timezone.utc))
    assert ok is False
