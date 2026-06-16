from __future__ import annotations

from datetime import date
from pathlib import Path
import math
import os
import json
import pathlib
from typing import List, Any

from aind_data_schema_models.harp_types import HarpDeviceType
from aind_data_schema_models.modalities import Modality
from aind_data_schema_models.organizations import Organization
from aind_data_schema.components.coordinates import (
    CoordinateSystemLibrary,
    Scale,
)
from aind_data_schema_models.units import FrequencyUnit, SizeUnit
from aind_data_schema_models.coordinates import AnatomicalRelative
from aind_data_schema_models.devices import CameraTarget

from aind_data_schema.components.devices import (
    Camera,
    CameraAssembly,
    DAQChannel,
    Device,
    Enclosure,
    EphysAssembly,
    EphysProbe,
    HarpDevice,
    Lens,
    LightEmittingDiode,
    Manipulator,
    Olfactometer,
    OlfactometerChannel,
    OlfactometerChannelType,
    OpenEphysAcquisitionBoard,
    ProbePort,
    Software,
    Computer,
)

from aind_data_schema.components.connections import Connection
from aind_data_schema.core.instrument import Instrument

# ---------------------------------------
# HELPER FUNCTION FOR ACQUISTION METADATA
# ---------------------------------------


def get_behavior_enclosure(instrument):
    """
    Determine whether the instrument uses a Delphi cage or a Pirouette behavior box.

    Parameters
    ----------
    instrument : Instrument
        AIND Instrument object whose ``components`` list is searched for an ``Enclosure``.

    Returns
    -------
    tuple[Enclosure, str]
        ``(enclosure, kind)`` where *kind* is one of ``"delphi"`` or ``"pirouette"``.

    Raises
    ------
    ValueError
        If no ``Enclosure`` component is found, or the enclosure name is unrecognised.
    """
    enclosures = [c for c in instrument.components if isinstance(c, Enclosure)]

    if not enclosures:
        raise ValueError("No Enclosure found in instrument components")

    # Your rigs have exactly one enclosure
    enclosure = enclosures[0]

    name_lower = enclosure.name.lower()

    if "delphi" in name_lower:
        return enclosure, "delphi"
    elif "pirouette" in name_lower:
        return enclosure, "pirouette"
    else:
        raise ValueError(f"Unknown enclosure type: {enclosure.name}")


# ------------------------------
# HELPER Delphi metadata parsing
# ------------------------------
def parse_delphi_metadata(metadata_path: Path):
    """
    Parse Delphi HardwareSettings and RuleSettings JSONL files.

    Parameters
    ----------
    metadata_path : Path
        Directory containing Delphi metadata files (``*HardwareSettings*.jsonl``
        and ``*RuleSettings*.jsonl``).

    Returns
    -------
    tuple[list, dict]
        ``(delphi_hardware, delphi_rules)`` where *delphi_hardware* is a list of
        hardware-settings records and *delphi_rules* maps odor name to channel index.
    """
    metadatafiles = os.listdir(metadata_path)

    delphi_rules = {}  # initialize once
    delphi_hardware = []  # (optional: same fix for hardware if needed)

    for file in metadatafiles:
        full_path = pathlib.Path.cwd().joinpath(metadata_path, file)

        if "HardwareSettings" in file:
            with open(full_path, "r") as jsonl_file:
                delphi_hardware.extend(json.loads(line) for line in jsonl_file)

        if "RuleSettings" in file:
            full_path = pathlib.Path.cwd().joinpath(metadata_path, file)

            with open(full_path, "r") as jsonl_file:
                for line in jsonl_file:
                    record = json.loads(line)

                    try:
                        state_defs = record["value"]["rule"]["stateDefinitions"]
                    except KeyError:
                        continue  # skip malformed entries safely

                    for odor in state_defs:
                        name = odor.get("name")

                        if not name or name == "DefaultState":
                            continue

                        try:
                            idx = int(math.log2(odor["odorIndex"]))
                        except (KeyError, ValueError):
                            continue

                        # store in dictionary (deduplicates automatically)
                        delphi_rules[name] = idx

    return delphi_hardware, delphi_rules


# -------------------------
# CREATE METADATA OBJECTS
# -------------------------


def _normalize_probe_model(name: str) -> str:
    """Map probe annotation names (old and new formats) to AIND EphysProbe enum values.

    Handles both the old parenthesis format (e.g. "Neuropixels 2.0 (multishank)")
    and the new dash format (e.g. "Neuropixels 2.0 - multishank"), with
    case-insensitive matching. Falls back to "Custom" if no match is found.
    """
    n = name.lower().replace("-", " ").replace("(", " ").replace(")", " ")
    if "2.0" in n and "multi" in n:
        return "Neuropixels 2.0 (Multi Shank)"
    elif "2.0" in n and "single" in n:
        return "Neuropixels 2.0 (Single Shank)"
    elif "2.0" in n and "quad" in n:
        return "Neuropixels 2.0 (Quad Base)"
    elif "uhd" in n and "switch" in n:
        return "Neuropixels UHD (Switchable)"
    elif "uhd" in n and "fixed" in n:
        return "Neuropixels UHD (Fixed)"
    elif "uhd" in n:
        return "Neuropixels UHD (Fixed)"
    elif "opto" in n:
        return "Neuropixels Opto (Demonstrator)"
    elif "1.0" in n:
        return "Neuropixels 1.0"
    else:
        return "Custom"


def create_instrument_metadata(
    *,
    current_experiment: str,
    experiment_room: str,
    instrument_id: str,
    dataset_root: Path,
    metadata_path: Path,
    probe_id: str,
    probe_serial_number: str | None,
    delphi_computer_id: str,
) -> Instrument:
    """
    Create an AIND Instrument metadata object.

    Parameters
    ----------
    current_experiment : str
        One of {"delphi", "delphi_pirouette", "pirouette"}
    experiment_room : str
        Physical room identifier
    instrument_id : str
        Instrument name / ID
    dataset_root : Path
        Dataset root directory
    metadata_path : Path
        Path to behavior metadata directory
    probe_id : str
        Probe name (e.g. "Probe B")
    probe_serial_number : str | None
        Neuropixels probe serial number
    delphi_computer_id : str
        Hostname for Delphi computer

    Returns
    -------
    Instrument
        Fully populated and validated Instrument object
    """

    today = date.today()
    components: List[Any] = []
    connections: List[Connection] = []
    modalities: List[Modality] = []

    # -------------------------
    # PIROUETTE CONFIG
    # -------------------------
    if "pirouette" in current_experiment:
        rig_json_path = metadata_path / "AindBehaviorPirouetteRig.json"
        with rig_json_path.open("r", encoding="utf-8") as f:
            rig_json = json.load(f)

        computer = Computer(name=rig_json["rig_name"])

        cameras = rig_json["camera_controller"]["cameras"]
        camera_names = list(cameras.keys())

        # ---- Probe info
        probe_json_path = dataset_root / "ecephys" / "probe.json"
        with probe_json_path.open("r", encoding="utf-8") as f:
            probe_json = json.load(f)

        probe_model = _normalize_probe_model(
            probe_json["probes"][0]["annotations"]["name"]
        )

        # ---- HARP
        digitial_out = DAQChannel(channel_name="OUT_1", channel_type="Digital Output")
        clock_input = DAQChannel(channel_name="CLK_IN", channel_type="Digital Input")
        analog_expansion_input = DAQChannel(
            channel_name="Expansion",
            channel_type="Analog Input",
        )
        output_expander_channels = [digitial_out, clock_input, analog_expansion_input]

        harp_expander = HarpDevice(
            name="Harp Output Expander",
            harp_device_type=HarpDeviceType.OUTPUTEXPANDER,
            core_version="1.2",
            channels=output_expander_channels,
            is_clock_generator=False,
        )

        harp_sync = HarpDevice(
            name="Harp White Rabbit",
            harp_device_type=HarpDeviceType.CLOCKSYNCHRONIZER,
            core_version="1.1",
            is_clock_generator=True,
        )

        # ---- EPHYS
        port = ProbePort(index=1, probes=[probe_id])
        headstage = Device(name="ONIX Headstage Neuropixels 2.0e")

        probe = EphysProbe(
            name=probe_id,
            serial_number=probe_serial_number,
            probe_model=probe_model,
            headstage=headstage,
        )

        onix_board = OpenEphysAcquisitionBoard(
            name="Onix Breakout Board",
            firmware_version="1.6",
            ports=[port],
        )

        ephys_assembly = EphysAssembly(
            name="Chronic_ephys_assembly",
            manipulator=Manipulator(
                name="Manipulator scientifica",
                manufacturer=Organization.OTHER,
                notes="Used only during implantation",
            ),
            probes=[probe],
        )
        # ---- Commutator and encoder
        commutator = Device(
            name="Coaxial Commutator",
            manufacturer=Organization.OEPS,
        )

        mag_encoder = Device(
            name="Magnetic Encoder",
            serial_number="U1 AS504BA",
            manufacturer=Organization.AIND,
            notes="encodes the relative rotation of a ring magnet (K&J Magnetics: 1/4 inch x 1/8 inch x 1/4 inch, ID: N42 R424DIA, DIA Magnetized) glued onto the coaxial tether",
        )

        # ---- Cameras
        lens = Lens(
            name="Camera lens",
            manufacturer=Organization.TAMRON,
            notes="Tamron 12VG412ASIR lens.",
        )

        cam_assemblies = []
        for cam_name in camera_names:
            cam = Camera(
                name=cam_name,
                detector_type="Camera",
                data_interface="USB",
                manufacturer=Organization.FLIR,
                frame_rate=rig_json["camera_controller"]["frame_rate"],
                frame_rate_unit=FrequencyUnit.HZ,
                sensor_width=1440,
                sensor_height=1080,
                chroma="Monochrome",
                gain=cameras[cam_name]["gain"],
                recording_software=Software(
                    name="Spinnaker SDK",
                    version="1.29.0.5",
                ),
                notes="Blackfly S BFS-U3-04S2M-CS",
            )

            rel_pos = AnatomicalRelative.SUPERIOR if "Top" in cam_name else AnatomicalRelative.LEFT

            cam_assemblies.append(
                CameraAssembly(
                    name=f"{cam_name}_assembly",
                    target=CameraTarget.BODY,
                    relative_position=[rel_pos],
                    camera=cam,
                    lens=lens,
                )
            )

            connections += [
                Connection(
                    source_device="Harp Output Expander",
                    source_port="OUT_1",
                    target_device=cam_name,
                ),
                Connection(
                    source_device=cam_name,
                    target_device=computer.name,
                ),
            ]

        # ---- Connections
        # inputs into harp output expander
        connections.append(
            Connection(
                source_device="Magnetic Encoder",
                target_device="Harp Output Expander",
                target_port="Expansion",
            )
        )

        connections.append(
            Connection(
                source_device="Harp White Rabbit",
                target_device="Harp Output Expander",
                target_port="CLK_IN",
            )
        )

        # Ephys connections
        connections.append(
            Connection(
                source_device="Onix Breakout Board",
                target_device=computer.name,
            )
        )

        # Commutator connections
        connections.append(
            Connection(
                source_device="Coaxial Commutator",
                target_device="Onix Breakout Board",
            )
        )

        connections.append(
            Connection(
                source_device="Coaxial Commutator",
                target_device=computer.name,
            )
        )

        # magnetic encoder connections
        connections.append(
            Connection(
                source_device="Magnetic Encoder",
                target_device="Harp Output Expander",
                target_port="Expansion",
            )
        )

        #

        """Pirouette Box"""
        beh_box = Enclosure(
            name="Pirouette Behavior Box",
            size=Scale(scale=[8.0, 15.0, 32.0]),
            size_unit=SizeUnit.IN,
            internal_material="Bedding and nesting material",
            external_material="Acrylic",
            grounded=True,
            laser_interlock=False,
            air_filtration=True,
        )

        """IR Illumination of the Behavior Box"""
        # Lens for defracting light
        IR_lens = Lens(
            name="IR Convex Lens",
            manufacturer=Organization.THORLABS,
        )

        # IR light
        IR_illumination = LightEmittingDiode(
            name="IR Light Source",
            manufacturer=Organization.THORLABS,
            wavelength=810,
            wavelength_unit=SizeUnit.NM,
        )

        # Compile instrument components
        pirouette_components = [
            computer,
            harp_expander,
            harp_sync,
            ephys_assembly,
            commutator,
            mag_encoder,
            beh_box,
            IR_illumination,
            IR_lens,
            onix_board,
        ]
        for component in pirouette_components:
            components.append(component)
        for cam_assembly in cam_assemblies:
            components.append(cam_assembly)

        modalities += [Modality.ECEPHYS, Modality.BEHAVIOR_VIDEOS]

    # -------------------------
    # DELPHI INSTRUMENT
    # -------------------------
    if "delphi" in current_experiment:
        # parse delphi metadata
        delphi_hardware, delphi_rules = parse_delphi_metadata(metadata_path)

        """Computer"""
        if "pirouette" not in current_experiment:
            delphi_computer = Computer(name=delphi_computer_id)
            components.append(delphi_computer)
        else:
            delphi_computer = Computer(name=rig_json["rig_name"])  # Same as pirouette computer

        """Delphi Controller"""
        # channels
        odor_channels = [
            OlfactometerChannel(
                channel_index=idx,
                channel_type=OlfactometerChannelType.ODOR,
                flow_unit="mL/min",
            )
            for name, idx in sorted(delphi_rules.items(), key=lambda x: x[1])
        ]

        # delphi controller device
        harp_delphi_controller = Olfactometer(
            manufacturer=Organization.AIND,
            name="Delphi Controller",
            harp_device_type=HarpDeviceType.OLFACTOMETER,
            core_version="1.0",
            channels=odor_channels,
            is_clock_generator=False,
            notes="whoami=1409 and flow rate is actually set to 75 mL/min",
        )
        components.append(harp_delphi_controller)

        connections.append(
            Connection(
                source_device="Delphi Controller",
                target_device=delphi_computer.name,
            )
        )

        """Poke Port"""
        poke_port = Device(
            name="Poke Port",
            manufacturer=Organization.AIND,
            notes="https://github.com/AllenNeuralDynamics/harp.peripheral.poke-port",
        )
        components.append(poke_port)

        connections.append(
            Connection(
                source_device="Poke Port",
                target_device="Delphi Controller",
                target_port=f"POKE0_Pin {delphi_hardware[0]['value']['delphiController']['pokePin']}",
            )
        )

        # Pirouette doesn't use cameras from delphi workflow, but delphi only does
        if "pirouette" not in current_experiment:
            """Cameras"""
            # camera lens
            lens = Lens(
                name="Camera lens",
                manufacturer=Organization.COMPUTAR,
                notes="Computar M3Z1228C-MP lens.",
            )

            delphi_cameras = delphi_hardware[0]["value"]["cameraSettings"]
            cam_assemblies = []
            for camera in [delphi_cameras]:
                cam = Camera(
                    name=camera["cameraName"],
                    detector_type="Camera",
                    data_interface="USB",
                    manufacturer=Organization.FLIR,
                    frame_rate=cam["frameRate"],
                    frame_rate_unit=FrequencyUnit.HZ,
                    sensor_width=1440,
                    sensor_height=1080,
                    chroma="Monochrome",
                    recording_software=Software(
                        name="Spinnaker SDK",
                        version="1.29.0.5",
                    ),
                    notes="Model: Blackfly S BFS-U3-04S2M-CS",
                )
                # camera connection
                connections.append(
                    Connection(
                        source_device="Delphi Controller",
                        source_port="CAM0",
                        target_device=camera["cameraName"],
                    )
                )

                connections.append(
                    Connection(
                        source_device=camera["cameraName"],
                        target_device=delphi_computer.name,
                    )
                )

                cam_assemblies.append(
                    CameraAssembly(
                        name=f"{camera['cameraName']}_assembly",
                        target=CameraTarget.BODY,
                        relative_position=[rel_pos],
                        camera=camera["cameraName"],
                        lens=lens,
                    )
                )

            """Delphi Cage"""
            delphi_cage = Enclosure(
                name="Delphi Behavior Cage",
                size=Scale(scale=[14.5, 7.0, 7.0]),
                size_unit=SizeUnit.IN,
                internal_material="Bedding and nesting material and a hut",
                external_material="Acrylic",
                grounded=False,
                laser_interlock=False,
                air_filtration=True,
                notes="https://www.allentowninc.com/rodent-housing/nexgen/",
            )
            components.append(delphi_cage)

            # modalities
            modalities.append(Modality.BEHAVIOR)
            if Modality.BEHAVIOR_VIDEOS not in modalities:
                modalities.append(Modality.BEHAVIOR_VIDEOS)
            # modalities.append(Modality.BEHAVIOR) # Requires lickspout which is not apart of the Delphi setup

    # -------------------------
    # INSTRUMENT OBJECT
    # -------------------------
    instrument = Instrument(
        location=experiment_room,
        instrument_id=instrument_id,
        modification_date=today,
        modalities=modalities,
        coordinate_system=CoordinateSystemLibrary.ARENA_RBT,
        components=components,
        connections=connections,
    )

    return instrument
