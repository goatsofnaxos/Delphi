from aind_data_schema.components.devices import Olfactometer
from metadata_generator.instrument import get_behavior_enclosure, parse_delphi_metadata
from aind_data_schema.components.devices import CameraAssembly, EphysAssembly
from aind_data_schema.components.configs import EphysAssemblyConfig


def get_delphi_odor_channel_indices(instrument):
    """
    Extract odor channel indices from the Delphi Olfactometer in an Instrument.

    Handles both enum and string representations of ``channel_type``.

    Parameters
    ----------
    instrument : Instrument
        AIND Instrument object containing a Delphi Olfactometer component.

    Returns
    -------
    list[int]
        Sorted list of odor channel indices.

    Raises
    ------
    ValueError
        If the Delphi Olfactometer has no ODOR channels.
    """

    odor_channel_indices = []
    found_delphi_olfactometer = False

    for component in instrument.components:
        if not isinstance(component, Olfactometer):
            continue

        # Ensure this is the Delphi controller
        if "delphi" not in component.name.lower():
            continue

        found_delphi_olfactometer = True
        for channel in component.channels:
            channel_type = channel.channel_type

            # Handle enum OR string representation
            if channel_type == "Odor" or str(channel_type).lower() == "odor":
                odor_channel_indices.append(channel.channel_index)

    # No Delphi Olfactometer in the instrument at all — valid for pirouette-only
    # sessions on a delphi_pirouette rig where no Delphi hardware was connected.
    if not found_delphi_olfactometer:
        return []

    if not odor_channel_indices:
        raise ValueError(
            "Delphi Olfactometer was found but has no ODOR channels. "
            "Check instrument construction."
        )

    return sorted(odor_channel_indices)


def get_platform_surface_from_instrument(instrument):
    """
    Determine the platform surface string from the Instrument's enclosure.

    Parameters
    ----------
    instrument : Instrument
        AIND Instrument object.

    Returns
    -------
    str
        Platform surface string in the form ``"<enclosure name>: <internal material>"``.

    Raises
    ------
    ValueError
        If no known enclosure type is found.
    """
    enclosure, enclosure_kind = get_behavior_enclosure(instrument)

    if enclosure_kind in {"pirouette", "delphi"}:
        return f"{enclosure.name}: {enclosure.internal_material}"

    raise ValueError(f"Unsupported enclosure type: {enclosure_kind}")


def get_delphi_odor_names(metadata_path):
    """
    Parse Delphi metadata and return a list of odor names,
    excluding the DefaultState.

    Parameters
    ----------
    metadata_path : Path or str
        Path to the directory containing Delphi metadata.

    Returns
    -------
    list[str]
        List of odor names defined in the Delphi rules,
        excluding 'DefaultState'.
    """
    _, delphi_rules = parse_delphi_metadata(metadata_path)

    odor_names = [name for name in delphi_rules.keys() if name != "DefaultState"]

    return odor_names


def extract_camera_and_ephys_assemblies(instrument):
    """
    Extract camera assemblies and the ephys assembly from an Instrument.

    Parameters
    ----------
    instrument : Instrument
        AIND Instrument object

    Returns
    -------
    tuple[list[CameraAssembly], EphysAssembly]
        (camera_assemblies, ephys_assembly)

    Raises
    ------
    ValueError
        If no EphysAssembly is found.
    """

    cam_assemblies = [c for c in instrument.components if isinstance(c, CameraAssembly)]

    try:
        ephys_assembly = next(c for c in instrument.components if isinstance(c, EphysAssembly))
    except StopIteration:
        raise ValueError("No EphysAssembly found in instrument components")

    return cam_assemblies, ephys_assembly


def get_probe_config_from_acquisition(acquisition):
    """
    Extract the ProbeConfig and its parent EphysAssemblyConfig
    from an Acquisition object.

    Parameters
    ----------
    acquisition : Acquisition
        AIND Acquisition object

    Returns
    -------
    ProbeConfig

    Raises
    ------
    ValueError
        If no EphysAssemblyConfig or ProbeConfig is found,
        or if multiple are found (unexpected).
    """

    ephys_configs = []

    for data_stream in acquisition.data_streams:
        for config in data_stream.configurations or []:
            if isinstance(config, EphysAssemblyConfig):
                ephys_configs.append(config)

    if not ephys_configs:
        raise ValueError("No EphysAssemblyConfig found in acquisition data_streams.")

    if len(ephys_configs) > 1:
        raise ValueError(f"Expected exactly one EphysAssemblyConfig, found {len(ephys_configs)}.")

    ephys_config = ephys_configs[0]

    if not ephys_config.probes:
        raise ValueError("EphysAssemblyConfig contains no ProbeConfig entries.")

    if len(ephys_config.probes) > 1:
        raise ValueError(f"Expected exactly one ProbeConfig, found {len(ephys_config.probes)}.")

    probe_config = ephys_config.probes[0]

    return probe_config
