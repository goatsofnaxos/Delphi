"""Shared fixtures for experiment_conductor tests."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Real test dataset paths
# ---------------------------------------------------------------------------
TEST_SESSION_ROOT = Path(
    r"C:\Users\brandon.pratt\Desktop\data\test_data\842456\2026-03-20T20-23-05_test"
)
TEST_RUN_DIR = TEST_SESSION_ROOT / "2026-03-20T20-23-37"
TEST_SUBJECT_ID = "842456"


def _ignore_large_media(directory, contents):
    """shutil.ignore_patterns callback: skip large video files to avoid disk exhaustion."""
    return {f for f in contents if f.lower().endswith((".mp4", ".avi"))}


@pytest.fixture()
def session_copy(tmp_path: Path) -> Path:
    """Return a writable copy of the test session under tmp_path.

    Large MP4/AVI video files are excluded — they are not needed for pipeline
    or metadata tests and would exhaust the tmp disk.

    Tests that modify the directory (pipeline, consolidate, metadata) must use
    this fixture so the canonical test dataset is never altered.
    """
    dst = tmp_path / "842456" / "2026-03-20T20-23-05_test"
    shutil.copytree(TEST_SESSION_ROOT, dst, ignore=_ignore_large_media)
    return dst


@pytest.fixture()
def base_cfg(tmp_path: Path, monkeypatch):
    """Minimal ConductorConfig pointing at the test session copy.

    sys.argv is patched to ``['conductor']`` so _parse_cli() doesn't pick up
    pytest arguments; env vars for required fields are set via monkeypatch.
    """
    from experiment_conductor.config import build_config

    session_dst = tmp_path / "842456" / "2026-03-20T20-23-05_test"
    shutil.copytree(TEST_SESSION_ROOT, session_dst, ignore=_ignore_large_media)

    monkeypatch.setattr(sys, "argv", ["conductor"])
    monkeypatch.chdir(tmp_path)  # no real .env picked up
    monkeypatch.setenv("SUBJECT_ID", TEST_SUBJECT_ID)
    monkeypatch.setenv("DATA_ROOT", str(session_dst))
    monkeypatch.setenv("EXPERIMENT_TYPE", "delphi_pirouette")
    monkeypatch.setenv("DELPHI_EXPERIMENT", "bonhoeffer")
    monkeypatch.setenv("DELPHI_FIRMWARE", "0.1.0")
    monkeypatch.setenv("INSTRUMENT_ID", "delphi-rig-0")
    monkeypatch.setenv("EXPERIMENT_ROOM", "447")
    monkeypatch.setenv("ENABLE_UPLOAD", "false")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("PIPELINE_CADENCE_MINUTES", "60")

    return build_config()
