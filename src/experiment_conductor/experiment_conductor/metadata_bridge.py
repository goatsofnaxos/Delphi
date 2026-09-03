"""Bridge to the metadata-generator package.

Runs ``generate_all_metadata.py`` in the metadata-generator's own isolated
venv so that its ``aind-data-schema`` dependency (which caps pydantic at
<2.12) never conflicts with the conductor's ``delphi-data``/``swc-aeon``
dependency chain (which requires pydantic >=2.12).

Setup (one-time, on every machine):
    cd src/metadata_generator
    uv sync

The bridge locates the venv automatically relative to this file's position
in the repository (``../metadata_generator/.venv``).  Override with the
``CONDUCTOR_METADATA_PYTHON`` environment variable if the layout differs.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_REQUIRED_FILES = (
    "subject.json",
    "instrument.json",
    "acquisition.json",
    "procedures.json",
)

# ── Path resolution ───────────────────────────────────────────────────────────
# This file: src/experiment_conductor/experiment_conductor/metadata_bridge.py
#   parents[0] → src/experiment_conductor/experiment_conductor/
#   parents[1] → src/experiment_conductor/
#   parents[2] → src/
_SRC_DIR = Path(__file__).parents[2]
_MG_DIR = _SRC_DIR / "metadata_generator"
_MG_SCRIPT = _MG_DIR / "scripts" / "generate_all_metadata.py"


def _metadata_python() -> Path:
    """Return the Python executable for the metadata-generator venv.

    Checks ``CONDUCTOR_METADATA_PYTHON`` first, then falls back to the
    conventional ``src/metadata_generator/.venv`` location.

    Raises
    ------
    FileNotFoundError
        If neither source yields a usable executable.
    """
    override = os.getenv("CONDUCTOR_METADATA_PYTHON")
    if override:
        p = Path(override)
        if p.exists():
            return p
        raise FileNotFoundError(
            f"CONDUCTOR_METADATA_PYTHON points to a non-existent path: {p}"
        )

    _bin = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    candidate = _MG_DIR / ".venv" / _bin
    if candidate.exists():
        return candidate

    raise FileNotFoundError(
        f"metadata_generator venv Python not found at {candidate}.\n"
        "Run once to create it:\n"
        f"    cd {_MG_DIR}\n"
        "    uv sync"
    )


# ── Public API ────────────────────────────────────────────────────────────────

def check_metadata_present(run_dir: Path) -> bool:
    """Return *True* if all four AIND metadata JSON files exist."""
    metadata_dir = run_dir / "metadata"
    return all((metadata_dir / f).exists() for f in _REQUIRED_FILES)


def generate_metadata(
    *,
    experiment_type: str,
    subject_id: str,
    protocol_id: str,
    instrument_id: str,
    experiment_room: str,
    acquisition_type: str,
    delphi_computer_id: str,
    surgeons: list,
    experimenters: list,
    data_root: Path,
    surgery_notes_base: Optional[Path],
    metadata_output_path: Path,
) -> bool:
    """Generate AIND metadata by running generate_all_metadata.py in a subprocess.

    Uses the metadata-generator's own isolated venv so that its
    ``aind-data-schema`` dependency never conflicts with the conductor's
    environment.  All parameters are forwarded as environment variables
    matching what ``metadata_generator.config.build_config()`` reads.

    Returns
    -------
    bool
        *True* if the subprocess exited with code 0.
    """
    try:
        python = _metadata_python()
    except FileNotFoundError as exc:
        log.error("Cannot locate metadata-generator Python: %s", exc)
        return False

    if not _MG_SCRIPT.exists():
        log.error("generate_all_metadata.py not found at %s", _MG_SCRIPT)
        return False

    # Build the environment passed to the subprocess.
    # Inherit the current process environment (e.g. network credentials,
    # PATH) then overlay only the metadata-specific vars.
    env = os.environ.copy()
    env.update({
        "SUBJECT_ID": str(subject_id),
        "PROTOCOL_ID": str(protocol_id),
        "CURRENT_EXPERIMENT": str(experiment_type),
        "DATASET_ROOT": str(data_root),
        "METADATA_OUTPUT_PATH": str(metadata_output_path),
        "INSTRUMENT_ID": str(instrument_id),
        "EXPERIMENT_ROOM": str(experiment_room),
        "ACQUISITION_TYPE": str(acquisition_type),
        "DELPHI_COMPUTER_ID": str(delphi_computer_id),
        "EXPERIMENTERS": ",".join(experimenters),
        "SURGEONS": ",".join(surgeons),
        "GENERATE_SUBJECT": "true",
        "GENERATE_INSTRUMENT": "true",
        "GENERATE_PROCEDURES": "true",
        "GENERATE_ACQUISITION": "true",
    })

    # SURGERY_NOTES_PATH: the script appends /<subject_id>/<subject_id>_...docx
    if surgery_notes_base:
        env["SURGERY_NOTES_PATH"] = str(surgery_notes_base)

    log.info(
        "Running metadata generation subprocess — experiment=%s  subject=%s",
        experiment_type,
        subject_id,
    )
    log.debug("Metadata Python: %s", python)
    log.debug("Metadata script: %s", _MG_SCRIPT)

    result = subprocess.run(
        [str(python), str(_MG_SCRIPT)],
        env=env,
        capture_output=False,  # stream stdout/stderr to conductor log
    )

    if result.returncode == 0:
        log.info("Metadata generation completed successfully.")
        return True
    else:
        log.error(
            "Metadata generation subprocess exited with code %d.", result.returncode
        )
        return False


def update_acquisition_end_time(
    metadata_output_path: Path,
    end_time: datetime,
) -> bool:
    """Patch ``acquisition_end_time`` in an existing ``acquisition.json``."""
    acq_path = metadata_output_path / "acquisition.json"
    if not acq_path.exists():
        log.error("acquisition.json not found at %s.", acq_path)
        return False
    try:
        with acq_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data["acquisition_end_time"] = (
            end_time.isoformat(timespec="microseconds").replace("+00:00", "Z")
        )
        with acq_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log.info("Updated acquisition_end_time → %s.", end_time.isoformat())
        return True
    except Exception as exc:
        log.error("Failed to update acquisition end time: %s", exc, exc_info=True)
        return False


def verify_probe_json(data_root: Path) -> bool:
    """Check that ``probe.json`` exists in ``data_root/ecephys/``."""
    probe_path = data_root / "ecephys" / "probe.json"
    if probe_path.exists():
        log.info("probe.json found at %s.", probe_path)
        return True
    log.warning(
        "probe.json NOT found at %s — Pirouette upload requires this file.",
        probe_path,
    )
    return False
