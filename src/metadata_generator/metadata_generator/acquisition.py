from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os

import pathlib

from aind_data_schema.components.identifiers import Software, Code
from aind_data_schema.core.acquisition import (
    Acquisition,
    AcquisitionSubjectDetails,
    DataStream,
    StimulusEpoch,
)
from aind_data_schema.components.configs import (
    ManipulatorConfig,
    EphysAssemblyConfig,
    ProbeConfig,
    OlfactometerConfig,
    OlfactometerChannelInfo,
)
from aind_data_schema.components.coordinates import (
    Translation,
    AtlasCoordinate,
    AtlasLibrary,
    CoordinateSystemLibrary,
)
from aind_data_schema_models.brain_atlas import CCFv3
from aind_data_schema_models.stimulus_modality import StimulusModality
from aind_data_schema_models.modalities import Modality


# =============================================================================
# Helpers (faithful to notebook semantics)
# =============================================================================
def _creation_timestamp(p: Path) -> float | None:
    """
    Return file creation timestamp in seconds since epoch.
    Windows: creation time
    macOS: birth time
    Linux: None (caller must fallback)
    """
    st = p.stat()
    if hasattr(st, "st_birthtime"):
        return st.st_birthtime
    if os.name == "nt":
        return st.st_ctime
    return None


def most_recent_created_file(
    dir_path: Path,
    include_subdirs: bool = False,
) -> tuple[Path | None, datetime | None]:
    """
    Find the most recently created file in a directory.
    Falls back to modification time where creation time is unavailable.
    """
    root = pathlib.Path(dir_path)
    iterator = root.rglob("*") if include_subdirs else root.glob("*")

    latest_path: Path | None = None
    latest_ts: float | None = None

    for p in iterator:
        if not p.is_file():
            continue

        cts = _creation_timestamp(p)
        ts = cts if cts is not None else p.stat().st_mtime

        if latest_ts is None or ts > latest_ts:
            latest_ts = ts
            latest_path = p

    if latest_path is None or latest_ts is None:
        return None, None

    return latest_path, datetime.fromtimestamp(latest_ts, tz=timezone.utc)


# =============================================================================
# Main acquisition builder
# =============================================================================


def create_acquisition_metadata(
    *,
    current_experiment: str,
    acquisition_type: str,
    instrument_id: str,
    protocol_id: str,
    subject: str,
    experimenters: list[str],
    dataset_root: Path,
    metadata_path: Path,
    # ---- objects from instrument.py ----
    ephys_assembly,  # EphysAssembly
    probe_id: str,
    cam_assemblies: list,  # List[CameraAssembly]
    platform_surface: str,
    # ---- Delphi-specific inputs ----
    odor_names: list[str] | None = None,
    odor_channels: list | None = None,
    delphi_controller_name: str = "Delphi Controller",
) -> Acquisition:
    """
    Create Acquisition metadata EXACTLY matching the original notebook behavior.
    """

    # -------------------------------------------------------------------------
    # Universal software (as in notebook)
    # -------------------------------------------------------------------------
    spinview = Software(
        name="Spinnaker SDK",
        version="1.29.0.5",
    )

    bonsai = Software(
        name="Bonsai",
        version="2.9",
    )

    # -------------------------------------------------------------------------
    # Determine session timing start and end times
    # -------------------------------------------------------------------------
    if "pirouette" in current_experiment:
        session_json_path = metadata_path / "AindBehaviorSessionModel.json"
        with session_json_path.open("r", encoding="utf-8") as f:
            session_json = json.load(f)

        creation_time = os.path.getctime(session_json_path)
        expt_start_time = datetime.fromtimestamp(creation_time, tz=timezone.utc)

        try:
            int(session_json["subject"])
            subject_id = session_json["subject"]
        except Exception:
            subject_id = subject

        experimenters = session_json["experimenter"]

    """DELPHI ONLY REQUIRES MANUAL INPUT OF SESSION METADATA UNTIL REFACTOR"""
    if ("delphi" in current_experiment) and ("pirouette" not in current_experiment):
        # Manual input of session metadata
        subject_id = subject

        # Use metadata file creation time to mark the start of the experiment
        metadatafiles = os.listdir(metadata_path)
        for file in metadatafiles:
            if "HardwareSettings" in file:
                delphi_metadata_path = pathlib.Path.cwd().joinpath(metadata_path, file)
                # Open the JSONL file
                with open(delphi_metadata_path, "r") as jsonl_file:
                    # Read and parse each line into a list of dictionaries
                    delphi_hardware = [json.loads(line) for line in jsonl_file]
            try:
                subject_id = next(
                    str(item.get("subject")) for item in delphi_hardware if "subject" in item
                )
            except Exception:
                subject_id = subject

        creation_time = os.path.getctime(delphi_metadata_path)
        expt_start_time = datetime.fromtimestamp(creation_time, tz=timezone.utc)

    # END Time
    _, expt_end_time = most_recent_created_file(
        dataset_root.joinpath("behavior-videos"), include_subdirs=True
    )

    # -------------------------------------------------------------------------
    # Storage objects (NOTEBOOK ORDER MATTERS)
    # -------------------------------------------------------------------------
    stim_epochs: list[StimulusEpoch] = []
    acq_code: list[Code] = []
    device_configs: list = []
    active_devices: list[str] = []
    modality_list: list[Modality] = []

    # -------------------------------------------------------------------------
    # PIROUETTE ACQUISITION
    # -------------------------------------------------------------------------
    if "pirouette" in current_experiment:
        # Open Ephys GUI
        open_ephys_gui = Software(
            name="Open Ephys GUI",
            version="1.0.1 - https://open-ephys.org/gui/",
        )

        # Pirouette Bonsai Acquisition Code
        pirouette_bonsai_acq = Code(
            name="Pirouette Bonsai Acquisition",
            url="https://github.com/AllenNeuralDynamics/Aind.Behavior.Pirouette/src/WrappedPirouette.bonsai",
            core_dependency=bonsai,
        )

        # LifeAlert
        lifealert = Code(
            name="LifeAlert",
            url="https://github.com/AllenNeuralDynamics/lifealert",
            language="Python",
        )

        # Ephys Assembly
        probe_config = ProbeConfig(
            primary_targeted_structure=CCFv3.PIR,
            device_name=probe_id,
            atlas_coordinate=AtlasCoordinate(
                coordinate_system=AtlasLibrary.CCFv3_10um,
                translation=[6854, 5759, 2114],
            ),
            coordinate_system=CoordinateSystemLibrary.BREGMA_ARI,
            transform=[],
            notes=(
                "Probe is chronically implanted so manipulator is only used during implantation."
            ),
        )
        ephys_assembly_config = EphysAssemblyConfig(
            device_name=ephys_assembly.name,
            manipulator=ManipulatorConfig(
                device_name="scientifica manipulator",
                coordinate_system=CoordinateSystemLibrary.BREGMA_ARI,
                local_axis_positions=Translation(
                    translation=[0, 0, 3971],  # dynamically map from procedures
                ),
            ),
            probes=[probe_config],
        )
        device_configs.append(ephys_assembly_config)

        stim_epochs.append(
            StimulusEpoch(
                stimulus_name="Pirouette Behavior",
                stimulus_modalities=[
                    StimulusModality.FREE_MOVING,
                ],
                stimulus_start_time=expt_start_time,
                stimulus_end_time=expt_end_time,
                code=pirouette_bonsai_acq,
                active_devices=[
                    ephys_assembly.name,
                    "Onix Breakout Board",
                    "Coaxial Commutator",
                    "Harp White Rabbit",
                    "Harp Output Expander",
                    "Magnetic Encoder",
                ],
            )
        )

        acq_code.extend([pirouette_bonsai_acq, lifealert])

        active_devices.extend(
            [
                ephys_assembly.name,
                "Onix Breakout Board",
                "Coaxial Commutator",
                "Harp White Rabbit",
                "Harp Output Expander",
                "Magnetic Encoder",
            ]
        )

        modality_list.extend(
            [
                Modality.ECEPHYS,
                Modality.BEHAVIOR_VIDEOS,
                Modality.BEHAVIOR,
            ]
        )

    # -------------------------------------------------------------------------
    # DELPHI ACQUISITION
    # -------------------------------------------------------------------------
    if "delphi" in current_experiment:
        # Delphi Bonsai Acquisition Software
        delphi_bonsai_acq = Code(
            name="Delphi Bonsai Acquisition",
            url="https://github.com/goatsofnaxos/Delphi/src/DelphiMain.bonsai",
            core_dependency=bonsai,
        )

        # Olfactometer Config
        odor_channel_info = []
        for i, odor in enumerate(odor_names):
            odor_channel_info.append(
                OlfactometerChannelInfo(
                    channel_index=odor_channels[i],
                    odorant=odor,
                    dilution=0.0,
                )
            )

        delphi_controller_config = OlfactometerConfig(
            device_name="Delphi Controller",
            channel_configs=odor_channel_info,
        )
        delphi_stim = StimulusEpoch(
            stimulus_name="Odor Delivery",
            stimulus_modalities=[
                StimulusModality.OLFACTORY,
                StimulusModality.FREE_MOVING,
            ],
            stimulus_start_time=expt_start_time,
            stimulus_end_time=expt_end_time,
            configurations=[delphi_controller_config],
            code=delphi_bonsai_acq,
            active_devices=["Delphi Controller"],
        )

        stim_epochs.append(delphi_stim)
        acq_code.append(delphi_bonsai_acq)
        active_devices.append(delphi_controller_name)

        if Modality.BEHAVIOR_VIDEOS not in modality_list:
            modality_list.append(Modality.BEHAVIOR_VIDEOS)
        if Modality.BEHAVIOR not in modality_list:
            modality_list.append(Modality.BEHAVIOR)

    # -------------------------------------------------------------------------
    # Cameras (ALWAYS appended, no inference)
    # -------------------------------------------------------------------------
    for cam_assembly in cam_assemblies:
        active_devices.append(cam_assembly.camera.name)

    # -------------------------------------------------------------------------
    # DataStream (exact semantics)
    # -------------------------------------------------------------------------
    data_streams = DataStream(
        stream_start_time=expt_start_time,
        stream_end_time=expt_end_time,
        modalities=modality_list,
        code=acq_code,
        notes="Poking behavior to receieve odors is also considered a BEHAVIOR modality.",
        active_devices=active_devices,
        configurations=device_configs,
    )

    # -------------------------------------------------------------------------
    # Subject details (exact wording)
    # -------------------------------------------------------------------------
    subject_details = AcquisitionSubjectDetails(mouse_platform_name=platform_surface)

    # -------------------------------------------------------------------------
    # Final Acquisition object
    # -------------------------------------------------------------------------
    acquisition = Acquisition(
        experimenters=experimenters,
        subject_id=str(subject_id),
        instrument_id=instrument_id,
        protocol_id=[protocol_id],
        acquisition_type=acquisition_type,
        acquisition_start_tz="UTC",
        acquisition_start_time=expt_start_time,
        acquisition_end_time=expt_end_time,
        coordinate_system=CoordinateSystemLibrary.BREGMA_ARI,
        data_streams=[data_streams],
        stimulus_epochs=stim_epochs,
        subject_details=subject_details,
    )
    return acquisition
