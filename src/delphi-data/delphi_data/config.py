# -----------------------------
# FIRMWARE REGISTER SETS WITH INHERITANCE
# -----------------------------
CORE_REGISTERS = [
    "ValveState",
    "PokeState",
    "RawPokeState",
    "QueuedOdorMask",
    "MinOdorDeliveryTimeUS",
    "MaxOdorDeliveryTimeUS",
    "MinimumPokeTimeUS",
]

FIRMWARE_CONFIG = {
    "0.1.0": {
        "parent": None,
        "registers": [
            "VacuumCloseTimeUS",
            "VacuumSetupTimeUS",
            "OdorTransitionTimeUS",
            "FinalValveEnergizedTimeUS",
        ],
    },
    "0.2.0": {
        "parent": "0.1.0",
        "registers": [
            "OdorDwellTimeUS",
        ],
    },
    "0.3.0": {
        "parent": "0.2.0",
        "registers": [
            # no additions
        ],
    },
}

VIDEO_CONFIG = {
    "0.1.0": {
        "registers": [
            "CamPinState",
            "FrameRate",
        ],
    },
    "0.2.0": {
        "parent": "0.1.0",
        "registers": [],
    },
    "0.3.0": {
        "parent": None,
        "registers": [
            "Cam0PinState",
            "Cam0FrameRate",
            "Cam1PinState",
            "Cam1FrameRate",
        ],
    },
}

# -----------------------------
# FIRMWARE REGISTER RESOLUTION
# -----------------------------
def resolve_firmware_registers(firmware: str, config: dict) -> list:
    """Resolve the full register list for a firmware version, following parent inheritance.

    Parameters
    ----------
    firmware:
        Firmware version string (e.g. ``"0.2.0"``).
    config:
        Firmware configuration dict mapping version strings to ``{"parent": ...,
        "registers": [...]}`` entries (e.g. :data:`FIRMWARE_CONFIG`).

    Returns
    -------
    list of str
        Ordered register names with parent registers prepended.

    Raises
    ------
    ValueError
        If a cyclic inheritance chain is detected or an unknown version is
        encountered.
    """
    resolved = []
    visited = set()

    while firmware:
        if firmware in visited:
            raise ValueError(f"Cyclic firmware inheritance detected: {firmware}")

        visited.add(firmware)

        if firmware not in config:
            raise ValueError(f"Unknown firmware version: {firmware}")

        entry = config[firmware]

        # prepend so parents come first
        resolved = entry.get("registers", []) + resolved

        firmware = entry.get("parent")

    return resolved


# -----------------------------
# GET ALL REGISTERS FOR FIRMWARE VERSION
# -----------------------------
def get_all_registers(firmware: str) -> tuple:
    """Return all register names for a given firmware version.

    Combines :data:`CORE_REGISTERS` with the firmware-specific and
    video-specific registers resolved via :func:`resolve_firmware_registers`.

    Parameters
    ----------
    firmware:
        Firmware version string (e.g. ``"0.2.0"``).

    Returns
    -------
    all_regs : list of str
        Ordered, deduplicated list of core + firmware register names.
    video_regs : list of str
        Video-specific register names for the firmware version.
    """
    core = CORE_REGISTERS

    fw_regs = resolve_firmware_registers(firmware, FIRMWARE_CONFIG)
    video_regs = resolve_firmware_registers(firmware, VIDEO_CONFIG)

    # preserve order, avoid duplicates
    seen = set()
    all_regs = []

    for r in core + fw_regs:
        if r not in seen:
            seen.add(r)
            all_regs.append(r)

    return all_regs, video_regs
