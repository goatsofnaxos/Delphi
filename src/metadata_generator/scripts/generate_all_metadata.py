"""
Generate all AIND metadata files.

Behavior:
- subject.json is generated on a best-effort basis (network/VPN required).
- If subject.json fails, instrument/procedures/acquisition are still generated.
"""

# ------------------------
# SUBJECT
# ------------------------
from metadata_generator.subject import write_subject_metadata

# ------------------------
# INSTRUMENT
# ------------------------
from metadata_generator.instrument import (
    create_instrument_metadata,
)
from aind_data_schema.core.instrument import Instrument

# ------------------------
# PROCEDURES
# ------------------------
from metadata_generator.procedures import create_procedures_metadata, parse_surgery_notes
from aind_data_schema.core.procedures import Procedures

# ------------------------
# ACQUISITION
# ------------------------
from metadata_generator.acquisition import create_acquisition_metadata
from aind_data_schema.core.acquisition import Acquisition
from metadata_generator.utils import (
    get_delphi_odor_channel_indices,
    get_platform_surface_from_instrument,
    get_delphi_odor_names,
    extract_camera_and_ephys_assemblies,
    get_probe_config_from_acquisition,
)

# ------------------------
# Configs
# ------------------------
from metadata_generator.config import build_config, MetadataGenerationConfig

import traceback
from pathlib import Path as _Path


def _write_utf8(model_obj, output_dir: _Path) -> None:
    """Write an aind-data-schema model to JSON with explicit UTF-8 encoding.

    Replaces write_standard_file which on Windows defaults to cp1252 and
    produces files that downstream UTF-8 readers (e.g. aind_metadata_mapper)
    cannot parse when the content contains characters like em dashes.
    """
    filename = output_dir / model_obj.default_filename()
    filename.write_text(model_obj.model_dump_json(indent=3), encoding="utf-8")


def main():
    # ============================================================
    # CONFIGURATION (ENV + CLI)
    # ============================================================
    config = build_config()

    # Core identifiers
    SUBJECT_ID = config.subject_id
    PROTOCOL_ID = config.protocol_id
    CURRENT_EXPERIMENT = config.current_experiment

    # Paths
    DATASET_ROOT = config.dataset_root
    METADATA_PATH = DATASET_ROOT / "behavior" / "metadata"
    METADATA_OUTPUT_PATH = config.metadata_output_path
    SURGERY_NOTES_PATH = config.surgery_notes_path
    _, _, _, PROBE_ID = parse_surgery_notes(SURGERY_NOTES_PATH)
    PROBE_SERIAL_NUMBER = PROBE_ID

    # Instrument / acquisition
    INSTRUMENT_ID = config.instrument_id
    EXPERIMENT_ROOM = config.experiment_room
    ACQUISITION_TYPE = config.acquisition_type
    DELPHI_COMPUTER_ID = config.delphi_computer_id

    # People
    EXPERIMENTERS = config.experimenters
    SURGEONS = config.surgeons

    # Ensure output directory exists
    METADATA_OUTPUT_PATH.mkdir(parents=True, exist_ok=True)

    # Metadata generation toggles
    METADATA_FILES_TO_GENERATE = MetadataGenerationConfig(
        generate_subject=config.generate_subject,
        generate_instrument=config.generate_instrument,
        generate_procedures=config.generate_procedures,
        generate_acquisition=config.generate_acquisition,
    )

    # # ============================================================
    # # SUBJECT METADATA
    # # ============================================================
    if METADATA_FILES_TO_GENERATE.generate_subject:
        print("Generating subject.json...")
        try:
            subject_obj = write_subject_metadata(
                subject_id=SUBJECT_ID,
                output_directory=METADATA_OUTPUT_PATH,
                allow_fallback=False,  # allow fallback to minimal subject if fetch fails
            )
            print("✅ subject.json generated.")
        except Exception as e:
            print(f"Error generating subject.json: {e}")
            subject_obj = None
            pass  # Proceed with generating other metadata files even if subject.json generation fails
    else:
        print("Skipping subject.json generation.")

    # ============================================================
    # INSTRUMENT METADATA
    # ============================================================
    if METADATA_FILES_TO_GENERATE.generate_instrument:
        print(METADATA_PATH)
        print(CURRENT_EXPERIMENT)
        print("Generating instrument.json...")
        try:
            instrument = create_instrument_metadata(
                current_experiment=CURRENT_EXPERIMENT,
                experiment_room=EXPERIMENT_ROOM,
                instrument_id=INSTRUMENT_ID,
                dataset_root=DATASET_ROOT,
                metadata_path=METADATA_PATH,
                probe_id=PROBE_ID,
                probe_serial_number=PROBE_SERIAL_NUMBER,
                delphi_computer_id=DELPHI_COMPUTER_ID,
            )
            serialized = instrument.model_dump_json()
            deserialized = Instrument.model_validate_json(serialized)
            _write_utf8(deserialized, METADATA_OUTPUT_PATH)
            print("✅ instrument.json generated.")

            # Pull objects needed downstream
            cam_assemblies, ephys_assembly = extract_camera_and_ephys_assemblies(instrument)

            # Enclosure details for subject details
            platform_surface = get_platform_surface_from_instrument(instrument)

            if "delphi" in CURRENT_EXPERIMENT:
                odor_names = get_delphi_odor_names(METADATA_PATH)
                odor_channels = get_delphi_odor_channel_indices(instrument)
                print(f"Identified odor channels: {odor_channels} with odor names: {odor_names}")

        except Exception as e:
            print(f"Error generating instrument.json: {e}")
            traceback.print_exc()
            cam_assemblies, ephys_assembly, platform_surface, odor_names, odor_channels = (
                None,
                None,
                None,
                None,
                None,
            )
            pass  # Proceed with generating other metadata files even if instrument.json generation fails
    else:
        print("Skipping instrument.json generation.")

    # # ============================================================
    # # ACQUISITION METADATA
    # # ============================================================
    if METADATA_FILES_TO_GENERATE.generate_acquisition:
        print("Generating acquisition.json...")
        try:
            acquisition = create_acquisition_metadata(
                current_experiment=CURRENT_EXPERIMENT,
                acquisition_type=ACQUISITION_TYPE,
                instrument_id=INSTRUMENT_ID,
                protocol_id=PROTOCOL_ID,
                subject=SUBJECT_ID,
                experimenters=EXPERIMENTERS,
                metadata_path=METADATA_PATH,
                dataset_root=DATASET_ROOT,
                ephys_assembly=ephys_assembly,
                probe_id=PROBE_ID,
                cam_assemblies=cam_assemblies,
                platform_surface=platform_surface,
                odor_names=odor_names if "delphi" in CURRENT_EXPERIMENT else None,
                odor_channels=odor_channels if "delphi" in CURRENT_EXPERIMENT else None,
            )
            serialized = acquisition.model_dump_json()
            deserialized = Acquisition.model_validate_json(serialized)
            _write_utf8(deserialized, METADATA_OUTPUT_PATH)
            print("✅ acquisition.json generated.")

            # Probe config for procedures metadata
            probe_config = get_probe_config_from_acquisition(acquisition)
        except Exception as e:
            print(f"Error generating acquisition.json: {e}")
            probe_config = None
            pass  # Proceed with generating other metadata files even if acquisition.json generation fails
    else:
        print("Skipping acquisition.json generation.")

    # ============================================================
    # PROCEDURES METADATA
    # ============================================================
    if METADATA_FILES_TO_GENERATE.generate_procedures:
        print("Generating procedures.json...")
        try:
            procedures = create_procedures_metadata(
                current_experiment=CURRENT_EXPERIMENT,
                subject_id=SUBJECT_ID,
                protocol_id=PROTOCOL_ID,
                surgeons=SURGEONS,
                surgery_notes_path=SURGERY_NOTES_PATH,
                probe_device=ephys_assembly.probes[0],
                probe_config=probe_config,
            )
            serialized = procedures.model_dump_json()
            deserialized = Procedures.model_validate_json(serialized)
            _write_utf8(deserialized, METADATA_OUTPUT_PATH)
            print("✅ procedures.json generated.")
        except Exception as e:
            print(f"Error generating procedures.json: {e}")
            pass  # Proceed with generating other metadata files even if procedures.json generation fails
    else:
        print("Skipping procedures.json generation.")

    # # ============================================================
    # # SUMMARY
    # # ============================================================
    print("\nMetadata generation complete.")
    print(f"Output directory: {METADATA_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
