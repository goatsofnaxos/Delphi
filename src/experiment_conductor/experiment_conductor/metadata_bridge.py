"""Bridge to the metadata-generator package.

Mirrors the logic of ``metadata_generator``'s individual builder modules but
driven by the conductor's own configuration rather than a separate CLI.  Each
JSON file is generated in an independent try/except block so a failure in one
step does not prevent the others from being written.

Metadata location
-----------------
All four AIND JSON files are written to ``<run_dir>/metadata/``.  The
conductor always passes the run-level directory (the earliest run sub-directory
after consolidation) rather than the session root.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compatibility shim for aind-data-schema-models < 5.7.3
# ---------------------------------------------------------------------------
# ``aind_data_schema`` ≥ 2.x evaluates ``Organization.DETECTOR_MANUFACTURERS``
# as a class-body type annotation on the ``Detector`` model at *import time*.
# Older releases of ``aind_data_schema_models`` don't define that attribute, so
# importing anything from ``aind_data_schema`` raises:
#
#   AttributeError: type object 'Organization' has no attribute 'DETECTOR_MANUFACTURERS'
#
# We patch the attribute onto the class here, before any ``aind_data_schema``
# module is imported, so both old and new library versions work.
# The canonical member list matches aind-data-schema-models 5.7.3.
_DETECTOR_ORG_ATTRS: tuple[str, ...] = (
    "AILIPU", "ALLIED", "BASLER", "DODOTRONIC", "EDMUND_OPTICS",
    "HAMAMATSU", "SPINNAKER", "FLIR", "OXFORD_INSTRUMENTS",
    "TELEDYNE_VISION_SOLUTIONS", "THE_IMAGING_SOURCE", "THORLABS",
    "UNKNOWN", "VIEWORKS", "OTHER",
)


def _ensure_detector_manufacturers_compat() -> None:
    """Patch ``Organization.DETECTOR_MANUFACTURERS`` if missing (pre-5.7.3 installs)."""
    try:
        from aind_data_schema_models.organizations import Organization  # type: ignore[import]
    except ImportError:
        return

    if hasattr(Organization, "DETECTOR_MANUFACTURERS"):
        return  # >=5.7.3 already defines it — nothing to do

    members = [getattr(Organization, a) for a in _DETECTOR_ORG_ATTRS if hasattr(Organization, a)]
    if not members:
        log.warning(
            "aind-data-schema-models compat: no detector org members found — cannot patch "
            "Organization.DETECTOR_MANUFACTURERS. Upgrade to aind-data-schema-models>=5.7.3."
        )
        return

    try:
        from aind_data_schema_models.utils import one_of_instance  # type: ignore[import]
        Organization.DETECTOR_MANUFACTURERS = one_of_instance(members)
    except (ImportError, Exception):
        # Fallback: plain Union — Pydantic can validate instances without a discriminator
        import typing
        Organization.DETECTOR_MANUFACTURERS = typing.Union[tuple(type(m) for m in members)]  # type: ignore[assignment,misc]

    log.debug(
        "aind-data-schema-models compat: patched Organization.DETECTOR_MANUFACTURERS "
        "with %d member types.", len(members)
    )


_REQUIRED_FILES = (
    "subject.json",
    "instrument.json",
    "acquisition.json",
    "procedures.json",
)


def check_metadata_present(run_dir: Path) -> bool:
    """Return *True* if all four AIND metadata JSON files exist.

    Checks the ``metadata/`` sub-directory of *run_dir*.  This is the
    canonical location where :func:`generate_metadata` writes its output.

    Parameters
    ----------
    run_dir : Path
        Run-level session directory (the earliest run sub-directory after
        consolidation).

    Returns
    -------
    bool
        *True* only if every required file is present.
    """
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
    """Generate AIND-compliant metadata files to ``metadata_output_path``.

    Calls :mod:`metadata_generator` functions to produce ``subject.json``,
    ``instrument.json``, ``acquisition.json``, and ``procedures.json``.
    Each file is wrapped in its own try/except; a failure in one does not
    prevent the others from being written.

    Parameters
    ----------
    experiment_type : str
        One of ``"delphi"``, ``"pirouette"``, ``"delphi_pirouette"``.
    subject_id : str
        Numeric AIND subject identifier.
    protocol_id : str
        AIND protocol ID string.
    instrument_id : str
        Rig/instrument identifier.
    experiment_room : str
        Physical room identifier.
    acquisition_type : str
        Acquisition type string (e.g. ``"ChronicRecording"``).
    delphi_computer_id : str
        Hostname of the Delphi acquisition computer.
    surgeons : list of str
        Surgeon names.
    experimenters : list of str
        Experimenter names.
    data_root : Path
        Run-level session directory.  Passed to instrument/acquisition builders
        to locate JSONL metadata files in ``behavior/metadata/``.
    surgery_notes_base : Path, optional
        Base directory for surgery notes.  Subject subfolder appended
        automatically: ``surgery_notes_base / subject_id / …``.
    metadata_output_path : Path
        Directory where JSON files will be written (``run_dir / "metadata"``).

    Returns
    -------
    bool
        *True* if all four files were generated without errors.
    """
    # Patch Organization.DETECTOR_MANUFACTURERS before any aind_data_schema import
    _ensure_detector_manufacturers_compat()

    from metadata_generator.subject import write_subject_metadata
    from metadata_generator.instrument import create_instrument_metadata
    from metadata_generator.procedures import create_procedures_metadata, parse_surgery_notes
    from metadata_generator.acquisition import create_acquisition_metadata
    from metadata_generator.utils import (
        extract_camera_and_ephys_assemblies,
        get_delphi_odor_channel_indices,
        get_delphi_odor_names,
        get_platform_surface_from_instrument,
        get_probe_config_from_acquisition,
    )
    from aind_data_schema.core.instrument import Instrument
    from aind_data_schema.core.acquisition import Acquisition
    from aind_data_schema.core.procedures import Procedures

    metadata_output_path.mkdir(parents=True, exist_ok=True)
    behavior_metadata_path = data_root / "behavior" / "metadata"
    any_error = False

    # ── Resolve surgery notes path and probe ID ───────────────────────────────
    surgery_notes_path: Optional[Path] = None
    if surgery_notes_base and subject_id:
        surgery_notes_path = (
            surgery_notes_base / subject_id
            / f"{subject_id}_craniotomy-implantation.docx"
        )

    probe_id = "Probe B"
    if surgery_notes_path and surgery_notes_path.exists():
        try:
            _, _, _, probe_sn = parse_surgery_notes(surgery_notes_path)
            if probe_sn:
                probe_id = probe_sn
                log.info("Probe ID from surgery notes: %s", probe_id)
        except Exception as exc:
            log.warning("Could not extract probe ID from surgery notes: %s", exc)
    else:
        log.warning(
            "Surgery notes not found at expected path — using default probe_id '%s'.",
            probe_id,
        )

    probe_serial_number = probe_id

    # ── subject.json ──────────────────────────────────────────────────────────
    log.info("Generating subject.json ...")
    try:
        write_subject_metadata(
            subject_id=subject_id,
            output_directory=metadata_output_path,
            allow_fallback=True,
        )
        log.info("subject.json generated.")
    except Exception as exc:
        log.error("Error generating subject.json: %s", exc, exc_info=True)
        any_error = True

    # ── instrument.json ───────────────────────────────────────────────────────
    log.info("Generating instrument.json ...")
    cam_assemblies = None
    ephys_assembly = None
    platform_surface = None
    odor_names = None
    odor_channels = None
    try:
        instrument = create_instrument_metadata(
            current_experiment=experiment_type,
            experiment_room=experiment_room,
            instrument_id=instrument_id,
            dataset_root=data_root,
            metadata_path=behavior_metadata_path,
            probe_id=probe_id,
            probe_serial_number=probe_serial_number,
            delphi_computer_id=delphi_computer_id,
        )
        Instrument.model_validate_json(instrument.model_dump_json()).write_standard_file(
            output_directory=metadata_output_path
        )
        log.info("instrument.json generated.")

        cam_assemblies, ephys_assembly = extract_camera_and_ephys_assemblies(instrument)
        platform_surface = get_platform_surface_from_instrument(instrument)
        if "delphi" in experiment_type:
            odor_names = get_delphi_odor_names(behavior_metadata_path)
            odor_channels = get_delphi_odor_channel_indices(instrument)
            log.info("Odor channels: %s  names: %s", odor_channels, odor_names)
    except Exception as exc:
        log.error("Error generating instrument.json: %s", exc, exc_info=True)
        any_error = True

    # ── acquisition.json ──────────────────────────────────────────────────────
    log.info("Generating acquisition.json ...")
    probe_config = None
    try:
        acquisition = create_acquisition_metadata(
            current_experiment=experiment_type,
            acquisition_type=acquisition_type,
            instrument_id=instrument_id,
            protocol_id=protocol_id,
            subject=subject_id,
            experimenters=experimenters,
            dataset_root=data_root,
            metadata_path=behavior_metadata_path,
            ephys_assembly=ephys_assembly,
            probe_id=probe_id,
            cam_assemblies=cam_assemblies,
            platform_surface=platform_surface,
            odor_names=odor_names if "delphi" in experiment_type else None,
            odor_channels=odor_channels if "delphi" in experiment_type else None,
        )
        Acquisition.model_validate_json(acquisition.model_dump_json()).write_standard_file(
            output_directory=metadata_output_path
        )
        log.info("acquisition.json generated.")
        probe_config = get_probe_config_from_acquisition(acquisition)
    except Exception as exc:
        log.error("Error generating acquisition.json: %s", exc, exc_info=True)
        any_error = True

    # ── procedures.json ───────────────────────────────────────────────────────
    log.info("Generating procedures.json ...")
    try:
        if surgery_notes_path and surgery_notes_path.exists() and probe_config is not None:
            probe_device = ephys_assembly.probes[0] if ephys_assembly else None
            procedures = create_procedures_metadata(
                current_experiment=experiment_type,
                subject_id=subject_id,
                protocol_id=protocol_id,
                surgeons=surgeons,
                surgery_notes_path=surgery_notes_path,
                probe_device=probe_device,
                probe_config=probe_config,
            )
        else:
            log.warning(
                "Surgery notes missing or acquisition failed — "
                "writing minimal procedures.json."
            )
            procedures = Procedures(subject_id=subject_id)
        Procedures.model_validate_json(procedures.model_dump_json()).write_standard_file(
            output_directory=metadata_output_path
        )
        log.info("procedures.json generated.")
    except Exception as exc:
        log.error("Error generating procedures.json: %s", exc, exc_info=True)
        any_error = True

    if any_error:
        log.warning("Metadata generation completed with errors — see log above.")
    else:
        log.info("All AIND metadata written to %s.", metadata_output_path)
    return not any_error


def update_acquisition_end_time(
    metadata_output_path: Path,
    end_time: datetime,
) -> bool:
    """Patch ``acquisition_end_time`` in an existing ``acquisition.json``.

    Reads the file, updates the field, and writes it back in-place.

    Parameters
    ----------
    metadata_output_path : Path
        Directory containing ``acquisition.json``.
    end_time : datetime
        New UTC end time to write.

    Returns
    -------
    bool
        *True* on success.
    """
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
    """Check that ``probe.json`` exists in ``data_root/ecephys/``.

    Parameters
    ----------
    data_root : Path
        Run-level session directory.

    Returns
    -------
    bool
        *True* if the file exists.
    """
    probe_path = data_root / "ecephys" / "probe.json"
    if probe_path.exists():
        log.info("probe.json found at %s.", probe_path)
        return True
    log.warning(
        "probe.json NOT found at %s — Pirouette upload requires this file.",
        probe_path,
    )
    return False
