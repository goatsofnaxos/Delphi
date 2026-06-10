"""Bridge to the metadata-generator package.

Builds a PipelineConfig from the conductor config and calls the per-modality
generation functions. Also handles updating the acquisition end time.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


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
    ``procedures.json``, ``instrument.json``, and ``acquisition.json``.

    Parameters
    ----------
    experiment_type : str
        One of ``delphi``, ``pirouette``, ``delphi_pirouette``.
    subject_id : str
        Mouse subject ID.
    protocol_id : str
        AIND protocol ID.
    instrument_id : str
        Instrument identifier.
    experiment_room : str
        Physical room identifier.
    acquisition_type : str
        Acquisition type string.
    delphi_computer_id : str
        Hostname of the Delphi acquisition computer.
    surgeons : list of str
        Surgeon names.
    experimenters : list of str
        Experimenter names.
    data_root : Path
        Run-level session directory.
    surgery_notes_base : Path, optional
        Base directory for surgery notes.
    metadata_output_path : Path
        Directory where metadata JSON files will be written.

    Returns
    -------
    bool
        True on success, False if an exception occurred.
    """
    try:
        from metadata_generator.config import PipelineConfig
        from metadata_generator.subject import write_subject_metadata
        from metadata_generator.instrument import create_instrument_metadata
        from metadata_generator.procedures import create_procedures_metadata
        from metadata_generator.acquisition import create_acquisition_metadata
        from metadata_generator.utils import (
            extract_camera_and_ephys_assemblies,
            get_delphi_odor_channel_indices,
            get_delphi_odor_names,
            get_platform_surface_from_instrument,
            get_probe_config_from_acquisition,
        )

        metadata_output_path.mkdir(parents=True, exist_ok=True)
        behavior_metadata_path = data_root / "behavior" / "metadata"

        # Surgery notes path
        if surgery_notes_base and subject_id:
            surgery_notes_path = (
                surgery_notes_base / subject_id
                / f"{subject_id}_craniotomy-implantation.docx"
            )
        else:
            surgery_notes_path = None

        log.info("Generating subject metadata ...")
        subject = write_subject_metadata(
            subject_id=subject_id,
            output_directory=metadata_output_path,
            allow_fallback=True,
        )

        log.info("Generating instrument metadata ...")
        instrument = create_instrument_metadata(
            current_experiment=experiment_type,
            experiment_room=experiment_room,
            instrument_id=instrument_id,
            dataset_root=data_root,
            metadata_path=behavior_metadata_path,
            probe_id="Probe B",
            probe_serial_number=None,
            delphi_computer_id=delphi_computer_id,
        )
        instrument.write_standard_file(metadata_output_path)

        # Procedures (requires surgery notes for pirouette)
        if "pirouette" in experiment_type and surgery_notes_path and surgery_notes_path.exists():
            cam_assemblies, ephys_assembly = extract_camera_and_ephys_assemblies(instrument)
            # Minimal probe config for procedures
            from aind_data_schema.components.configs import ProbeConfig, EphysAssemblyConfig
            probe_config = ProbeConfig(device_name="Probe B", transform=[])
            procedures = create_procedures_metadata(
                current_experiment=experiment_type,
                subject_id=subject_id,
                protocol_id=protocol_id,
                surgeons=surgeons,
                surgery_notes_path=surgery_notes_path,
                probe_device=ephys_assembly,
                probe_config=probe_config,
            )
        else:
            from aind_data_schema.core.procedures import Procedures
            procedures = Procedures(subject_id=subject_id)
        procedures.write_standard_file(metadata_output_path)

        log.info("Generating acquisition metadata ...")
        cam_assemblies, ephys_assembly = extract_camera_and_ephys_assemblies(instrument)
        odor_names = None
        odor_channels = None
        if "delphi" in experiment_type:
            try:
                odor_names = get_delphi_odor_names(behavior_metadata_path)
                odor_channels = get_delphi_odor_channel_indices(instrument)
            except Exception as exc:
                log.warning("Could not read Delphi odor info: %s", exc)
        platform_surface = get_platform_surface_from_instrument(instrument)
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
            probe_id="Probe B",
            cam_assemblies=cam_assemblies,
            platform_surface=platform_surface,
            odor_names=odor_names,
            odor_channels=odor_channels,
        )
        acquisition.write_standard_file(metadata_output_path)

        log.info("AIND metadata written to %s", metadata_output_path)
        return True

    except Exception as exc:
        log.error("Metadata generation failed: %s", exc, exc_info=True)
        return False


def update_acquisition_end_time(
    metadata_output_path: Path,
    end_time: datetime,
) -> bool:
    """Update ``acquisition_end_time`` in an existing ``acquisition.json``.

    Reads the JSON, updates the end time field, and writes the file back.

    Parameters
    ----------
    metadata_output_path : Path
        Directory containing ``acquisition.json``.
    end_time : datetime
        The actual UTC experiment end time.

    Returns
    -------
    bool
        True on success, False if the file does not exist or update failed.
    """
    acq_path = metadata_output_path / "acquisition.json"
    if not acq_path.exists():
        log.error("acquisition.json not found at %s", acq_path)
        return False
    try:
        with acq_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data["acquisition_end_time"] = end_time.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        with acq_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log.info("Updated acquisition_end_time to %s", end_time.isoformat())
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
        True if the file exists, False otherwise.
    """
    probe_path = data_root / "ecephys" / "probe.json"
    if probe_path.exists():
        log.info("probe.json found at %s", probe_path)
        return True
    log.warning("probe.json NOT found at %s — Pirouette upload requires this file.", probe_path)
    return False
