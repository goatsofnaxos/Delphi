"""Bridge to the delphi-data processing pipeline.

Calls ``delphi-data pipeline`` as a subprocess so that its internal
``sys.path`` manipulation does not interfere with the conductor's imports.
Also exposes ``consolidate_metadata_files`` directly.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from delphi_data.curation import consolidate_metadata_files

log = logging.getLogger(__name__)


def run_pipeline(
    data_root: Path,
    experiment: str,
    firmware: str,
    subject_id: str | None = None,
    skip_clips: bool = True,
) -> bool:
    """Run the delphi-data full processing pipeline for one session.

    Invokes ``delphi-data pipeline`` as a subprocess. Consolidation is always
    enabled. Clip extraction is skipped by default (large and slow).

    Parameters
    ----------
    data_root : Path
        Run-level session directory.
    experiment : str
        Experiment name (e.g. ``bonhoeffer``).
    firmware : str
        Firmware version string (e.g. ``1.0.0``).
    subject_id : str, optional
        Subject ID passed to the snapshot step.
    skip_clips : bool
        If True, pass ``--skip-clips`` to the pipeline.

    Returns
    -------
    bool
        True if the pipeline exited successfully, False otherwise.
    """
    cmd = [
        sys.executable, "-m", "delphi_data.cli",
        "pipeline",
        "--data-root", str(data_root),
        "--experiment", experiment,
        "--firmware", firmware,
    ]
    if subject_id:
        cmd += ["--subject-id", subject_id]
    if skip_clips:
        cmd.append("--skip-clips")

    log.info("Running delphi-data pipeline: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        log.error("delphi-data pipeline failed (exit %d)", result.returncode)
        return False
    log.info("delphi-data pipeline completed successfully.")
    return True


def move_delphi_metadata(data_root: Path) -> list:
    """Move HardwareSettings/RuleSettings JSONL files to behavior/metadata/.

    Wraps :func:`delphi_data.curation.consolidate_metadata_files`.

    Parameters
    ----------
    data_root : Path
        Run-level session directory.

    Returns
    -------
    list of str
        Absolute paths of files that were moved.
    """
    log.info("Moving Delphi metadata files to behavior/metadata/ ...")
    moved = consolidate_metadata_files(data_root)
    if moved:
        log.info("Moved %d metadata file(s).", len(moved))
    else:
        log.info("No metadata files needed moving.")
    return moved
