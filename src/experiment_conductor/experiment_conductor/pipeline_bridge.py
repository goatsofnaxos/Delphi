"""Bridge to the delphi-data processing pipeline.

Calls ``delphi-data pipeline`` and ``delphi-data consolidate`` as subprocesses
so that the package's internal ``sys.path`` manipulation does not interfere
with the conductor's imports.  Consolidation and metadata-file relocation are
also exposed as direct Python calls via :mod:`delphi_data.curation`.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

from delphi_data.curation import consolidate_metadata_files

log = logging.getLogger(__name__)

# Matches YYYY-MM-DDTHH-MM-SS (Bonsai timestamp format)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")


def has_delphi_controller_file(data_root: Path) -> bool:
    """Return *True* if Delphi controller Harp data exists under *data_root*.

    Checks for the ``behavior/DelphiController/`` directory that Bonsai
    creates when writing Harp register data from the Delphi controller.  The
    directory contains ``*.bin`` files (one per Harp register).  Its presence
    indicates that a ``delphi-data pipeline`` run is meaningful.

    The search walks up to two levels deep so it works whether *data_root* is
    the session root (containing a run sub-directory) or an already-resolved
    run directory.

    Parameters
    ----------
    data_root : Path
        Session root or run-level directory to search.

    Returns
    -------
    bool
    """
    # Check directly inside run dir: behavior/DelphiController/
    direct = data_root / "behavior" / "DelphiController"
    if direct.is_dir():
        log.debug("Found DelphiController dir: %s", direct)
        return True
    # Check one level deeper (session root contains run sub-directory)
    for child in data_root.iterdir():
        if child.is_dir() and _TIMESTAMP_RE.match(child.name):
            candidate = child / "behavior" / "DelphiController"
            if candidate.is_dir():
                log.debug("Found DelphiController dir: %s", candidate)
                return True
    return False


def resolve_run_dir(session_root: Path) -> Path:
    """Return the earliest run sub-directory inside *session_root*.

    After consolidation there is exactly one timestamp-named sub-directory;
    before consolidation the earliest one is the canonical run dir.  If no
    timestamp-named children exist, *session_root* is returned unchanged —
    it is already a run directory.

    Parameters
    ----------
    session_root : Path
        Session root directory (e.g. ``…/842456/2026-03-20T20-23-05``).

    Returns
    -------
    Path
        Earliest run sub-directory, or *session_root* if none exist.
    """
    from delphi_data.curation import collect_run_dirs, find_earliest_run

    run_dirs = collect_run_dirs(str(session_root))
    if run_dirs:
        return Path(find_earliest_run(run_dirs))
    return session_root


def run_pipeline(
    data_root: Path,
    experiment: str,
    firmware: str,
    subject_id: str | None = None,
    skip_build: bool = False,
    skip_clips: bool = True,
    skip_snapshot: bool = False,
    append: bool = True,
) -> bool:
    """Run the delphi-data full processing pipeline for one session.

    Invokes ``delphi-data pipeline`` as a subprocess.  Consolidation is
    handled separately by :func:`run_consolidation` before this is called.

    Parameters
    ----------
    data_root : Path
        Run-level session directory (the earliest run dir, not the session root).
    experiment : str
        Experiment name for the snapshot step (e.g. ``bonhoeffer``).
    firmware : str
        Firmware version string for Harp ingestion (e.g. ``1.0.0``).
    subject_id : str, optional
        Subject ID passed to the snapshot step.
    skip_build : bool
        If *True*, pass ``--skip-build`` to omit the build-dataset step.
    skip_clips : bool
        If *True*, pass ``--skip-clips`` to skip poke-clip extraction.
        Defaults to *True* because clip extraction is slow.
    skip_snapshot : bool
        If *True*, pass ``--skip-snapshot`` to skip figure generation.
    append : bool
        If *True*, pass ``--append`` to merge new Harp data into the existing
        CSV rather than overwriting it.  Default *True*.

    Returns
    -------
    bool
        *True* if the pipeline exited successfully.
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
    if append:
        cmd.append("--append")
    if skip_build:
        cmd.append("--skip-build")
    if skip_clips:
        cmd.append("--skip-clips")
    if skip_snapshot:
        cmd.append("--skip-snapshot")

    log.info("Running delphi-data pipeline: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        log.error("delphi-data pipeline failed (exit %d).", result.returncode)
        return False
    log.info("delphi-data pipeline completed successfully.")
    return True


def run_consolidation(data_root: Path) -> bool:
    """Merge multiple run sub-directories into the earliest one.

    Invokes ``delphi-data consolidate`` as a subprocess.  Used for
    pirouette-only experiments that have no Delphi controller data, or as a
    first step before running the full pipeline.

    Parameters
    ----------
    data_root : Path
        Session root directory (the directory that *contains* the
        run-timestamp sub-directories).

    Returns
    -------
    bool
        *True* if consolidation succeeded.
    """
    cmd = [
        sys.executable, "-m", "delphi_data.cli",
        "consolidate",
        "--data-root", str(data_root),
    ]
    log.info("Running delphi-data consolidate: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        log.error("delphi-data consolidate failed (exit %d).", result.returncode)
        return False
    log.info("delphi-data consolidate completed successfully.")
    return True


def move_delphi_metadata(data_root: Path) -> list:
    """Move ``HardwareSettings`` / ``RuleSettings`` JSONL files to ``behavior/metadata/``.

    Wraps :func:`delphi_data.curation.consolidate_metadata_files`.  The JSONL
    files must be in ``behavior/metadata/`` for the metadata generator to find
    them when building ``instrument.json`` and ``acquisition.json``.

    Parameters
    ----------
    data_root : Path
        Run-level session directory (the earliest run dir after consolidation).

    Returns
    -------
    list of str
        Absolute paths of the files that were moved.
    """
    log.info("Moving Delphi metadata JSONL files to behavior/metadata/ ...")
    moved = consolidate_metadata_files(data_root)
    if moved:
        log.info("Moved %d metadata file(s).", len(moved))
    else:
        log.debug("No metadata files needed moving.")
    return moved
