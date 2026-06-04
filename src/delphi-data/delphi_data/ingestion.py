from __future__ import annotations

import json
import math
import os
import pathlib
import sys
import warnings
from collections import defaultdict
from typing import Dict

import numpy as np
import pandas as pd
import yaml
from packaging.version import parse as parse_version
from swc.aeon.io import api as aeon_api
from swc.aeon.io import reader

from delphi_data.config import DEFAULT_TIMING_REGISTERS, get_all_registers

# ---------------------------------------------------------------------------
# Module-level Harp readers for commonly accessed registers
# ---------------------------------------------------------------------------

#: Harp reader for the camera trigger event (register 75 – ``CamPinState``).
#: Each row marks a rising edge of the PWM camera-trigger signal.
CAM_TRIGGER_READER: reader.Harp = reader.Harp(
    "DelphiController_75*", columns=["CamPinState"]
)

#: Harp reader factory for the ``Video`` camera frame index.
#: Pass the camera sub-directory to :func:`load_camera_frames`.
CAMERA_VIDEO_READER_FACTORY = reader.Video

# NOTE: There is no fixed FRAME_RATE_READER constant because the register
# name and address for the camera trigger frequency vary across firmware
# versions.  Use :func:`load_fps_from_harp` which resolves the correct
# address from device.yml or the packaged firmware YAMLs at runtime.


# -----------------------------
# PACKAGE ROOT TO GET FIRMWARE YAML PATH
# -----------------------------
def get_package_root() -> pathlib.Path:
    """Return the repository root directory (two levels above the package).

    Returns
    -------
    pathlib.Path
        Absolute path to the project root.
    """
    module = sys.modules[get_all_registers.__module__]
    return pathlib.Path(module.__file__).resolve().parent.parent


# -----------------------------
# YAML PARSING FOR FIRMWARE VERSION COMPATIBILITY
# -----------------------------
def parse_register_map(yaml_path: pathlib.Path) -> tuple:
    """Parse a firmware YAML file and return the register map and firmware version.

    Parameters
    ----------
    yaml_path:
        Path to the firmware ``device.yml`` or packaged YAML file.

    Returns
    -------
    registers : dict
        Mapping of register name to address metadata.
    firmware : str or None
        Firmware version string read from the YAML, or ``None`` if absent.
    """
    with open(yaml_path, "r") as f:
        config = yaml.safe_load(f)

    registers = config.get("registers", {})
    firmware = config.get("firmwareVersion", None)

    return registers, firmware


def build_readers(register_names: list, register_map: dict) -> dict:
    """Construct a dict of Harp reader objects for the requested registers.

    Missing registers (not present in *register_map*) emit a ``RuntimeWarning``
    and are skipped.

    Parameters
    ----------
    register_names:
        Ordered list of register names to build readers for.
    register_map:
        Dict mapping register names to their metadata (must contain
        ``"address"``).

    Returns
    -------
    dict
        Mapping of register name to :class:`swc.aeon.io.reader.Harp` instance.
    """
    readers = {}

    for name in register_names:
        if name not in register_map:
            warnings.warn(
                f"Register '{name}' not found in firmware register map — skipping.",
                RuntimeWarning,
            )
            continue

        address = register_map[name]["address"]

        readers[name] = reader.Harp(
            f"DelphiController_{address}*",
            columns=[name],
        )

    return readers


# -----------------------------
# DATA LOADING
# -----------------------------
def load_data(root_path, readers, start_cutoff=None, end_cutoff=None) -> dict:
    """Load all register streams from *root_path* using pre-built Harp readers.

    Parameters
    ----------
    root_path:
        Root data directory passed to :func:`swc.aeon.io.api.load`.
    readers:
        Dict of register name → Harp reader (output of :func:`build_readers`).
    start_cutoff:
        Optional start time cutoff (currently unused, reserved for future use).
    end_cutoff:
        Optional end time cutoff (currently unused, reserved for future use).

    Returns
    -------
    dict
        Mapping of register name to ``pd.DataFrame``.
    """
    return {
        name: aeon_api.load(
            root_path,
            rdr,
        )
        for name, rdr in readers.items()
    }


def extract_constant_registers(
    data: Dict[str, pd.DataFrame],
) -> Dict[str, float]:
    """Extract scalar timing registers and fill missing ones with package defaults.

    Any register whose name ends in ``US`` is converted from microseconds to
    seconds.  Missing timing registers are backfilled from
    :data:`DEFAULT_TIMING_REGISTERS`.

    Parameters
    ----------
    data:
        Dict of register name → DataFrame as returned by :func:`load_data`.
        Only single-row DataFrames are treated as constant registers.

    Returns
    -------
    dict
        Mapping of register name → scalar value in seconds.
    """

    constant_registers: Dict[str, float] = {}

    # -----------------------------
    # EXTRACT EXISTING VALUES
    # -----------------------------
    for name, df in data.items():
        if df.empty or df.shape[1] == 0:
            continue

        if len(df) == 1:
            val = df.iloc[0, 0]

            # convert microseconds → seconds
            if name.endswith("US"):
                val *= 1e-6

            constant_registers[name] = val

    # -----------------------------
    # APPLY DEFAULTS FOR TIMING REGS
    # -----------------------------
    for name, default_val in DEFAULT_TIMING_REGISTERS.items():
        if name not in constant_registers:
            constant_registers[name] = default_val

    return constant_registers


# -----------------------------
# ODOR MAP BUILDER
# -----------------------------


def build_odor_map(metadata_path: pathlib.Path) -> dict:
    """Build a mapping of odor name to zero-based odor index from metadata files.

    Reads all ``RuleSettings`` JSONL files in *metadata_path* and extracts
    ``stateDefinition`` entries.  Only power-of-two ``odorIndex`` values are
    accepted; multi-bit masks are skipped.  Conflicting definitions across
    files are warned about but the last-seen value wins.

    Parameters
    ----------
    metadata_path:
        Directory containing one or more ``RuleSettings`` JSONL files (as
        written by the Delphi control software).

    Returns
    -------
    dict
        ``{odor_name: zero_based_index}`` for every valid odor state found.
    """

    odor_map = {}
    seen_conflicts = defaultdict(set)

    for file in os.listdir(metadata_path):
        if "RuleSettings" not in file:
            continue

        full_path = metadata_path / file

        with open(full_path, "r") as f:
            for line in f:
                if not line.strip():
                    continue  # skip empty lines

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                try:
                    states = record["value"]["rule"]["stateDefinitions"]
                except KeyError:
                    continue

                for state in states:
                    name = state.get("name")

                    if not name or name == "DefaultState":
                        continue

                    odor_val = state.get("odorIndex")
                    if odor_val is None or odor_val <= 0:
                        continue

                    # ensure it's a power of 2
                    if odor_val & (odor_val - 1) != 0:
                        # skip multi-bit masks
                        continue

                    idx = int(math.log2(odor_val))

                    # detect conflicting mappings
                    if name in odor_map and odor_map[name] != idx:
                        seen_conflicts[name].add(idx)
                        seen_conflicts[name].add(odor_map[name])

                    odor_map[name] = idx

    # optional: warn about conflicts
    if seen_conflicts:
        print("⚠️ Odor mapping conflicts detected:")
        for name, vals in seen_conflicts.items():
            print(f"  {name}: {sorted(vals)}")

    print(f"✅ Odor map built with {len(odor_map)} entries.")
    print(odor_map)

    return odor_map


# -----------------------------
# PARSE DATA BASED ON POKE STATE MACHINE
# -----------------------------
def parse_data(
    break_onsets: pd.Index,
    break_offsets: pd.Index,
    poke_times_sec: pd.Index,
    valve_times: pd.Index,
    valve_state: pd.DataFrame,
    odor_map: Dict[str, int],
    constant_registers: Dict[str, float],
    firmware_version: str = "0.1.0",
) -> pd.DataFrame:
    """Build the per-poke event dataframe from raw hardware event streams.

    Each row corresponds to one beam-break onset.  Rows that are not matched
    to a valid poke event have ``poke_registered = False`` and NaN poke
    columns.

    Parameters
    ----------
    break_onsets:
        Sorted array of beam-break onset times (absolute seconds).
    break_offsets:
        Sorted array of beam-break offset times (absolute seconds).
    poke_times_sec:
        Sorted array of confirmed poke times (absolute seconds).
    valve_times:
        Sorted array of valve-state transition times (absolute seconds).
    valve_state:
        DataFrame whose first column contains valve bitmask values aligned to
        *valve_times*.
    odor_map:
        Mapping of odor name → zero-based odor index.
    constant_registers:
        Dict of timing register name → scalar value in seconds (output of
        :func:`extract_constant_registers`).
    firmware_version:
        Firmware version string used for odor-bit-shift correction.

    Returns
    -------
    pd.DataFrame
        Event dataframe with columns including ``poke_onset``, ``poke_offset``,
        ``poke_duration``, ``odor``, ``odor_name``, ``beam_break_onset``,
        ``beam_break_offset``, ``poke_registered``, ``datetime``, and one
        column per constant register.
    """
    df = pd.DataFrame()
    poke_n = 0

    # -----------------------------
    # PRECOMPUTE VALVE ARRAYS
    # -----------------------------
    valve_values = valve_state.iloc[:, 0].values
    valve_times_arr = valve_times.values
    poke_times_arr = poke_times_sec.values

    df["valve_transition_times"] = None
    df["valve_transition_values"] = None
    df["valve_transition_durations"] = None

    # -----------------------------
    # BUILD CORE EVENT DATAFRAME
    # -----------------------------
    for i, onset in enumerate(break_onsets):
        try:
            offset_idx = np.where((break_offsets.values - onset) > 0)[0][0]
            offset = break_offsets[offset_idx]

            poke_idx = np.where((poke_times_arr - onset) > 0)[0][0]
            poke_time = poke_times_arr[poke_idx]

            df.loc[i, "beam_break_onset"] = onset
            df.loc[i, "beam_break_offset"] = offset
            df.loc[i, "beam_break_duration"] = offset - onset

            if onset <= poke_time <= offset:
                poke_n += 1

                # -----------------------------
                # NEAREST VALVE STATE
                # -----------------------------
                valve_idx = np.argmin(np.abs(poke_time - valve_times_arr))
                odor_val = valve_values[valve_idx - 1]

                # -----------------------------
                # STATE MACHINE DURATION (fast version)
                # -----------------------------
                zero_times = valve_times_arr[valve_values == 0]
                idx_zero = np.searchsorted(zero_times, poke_time, side="right")
                if idx_zero < len(zero_times):
                    next_zero_time = zero_times[idx_zero]
                    df.loc[i, "state_machine_duration"] = next_zero_time - poke_time

                # -----------------------------
                # VALVE TRANSITIONS BETWEEN POKES
                # -----------------------------
                if poke_idx + 1 < len(poke_times_arr):
                    next_poke_time = poke_times_arr[poke_idx + 1]
                else:
                    next_poke_time = np.inf

                start_idx = (
                    np.searchsorted(valve_times_arr, poke_time, side="right") - 1
                )  # left shift for current state
                end_idx = np.searchsorted(valve_times_arr, next_poke_time, side="left")

                transition_times = valve_times_arr[start_idx:end_idx]
                transition_values = valve_values[start_idx:end_idx]

                # durations
                if len(transition_times) > 1:
                    durations = np.diff(transition_times)
                else:
                    durations = np.array([])

                if len(transition_times) > 0:
                    last_duration = next_poke_time - transition_times[-1]
                    durations = np.append(durations, last_duration)

                df.at[i, "valve_transition_times"] = transition_times.tolist()
                df.at[i, "valve_transition_values"] = transition_values.tolist()
                df.at[i, "valve_transition_durations"] = durations.tolist()

                # -----------------------------
                # FIRMWARE ODOR BIT CORRECTION
                # -----------------------------
                if parse_version(firmware_version) < parse_version("1.0.0"):
                    odor_val = odor_val >> 2
                else:
                    odor_val = odor_val >> 4

                # -----------------------------
                # STORE FEATURES
                # -----------------------------
                df.loc[i, "poke_number"] = poke_n
                df.loc[i, "poke_onset"] = poke_time
                df.loc[i, "poke_offset"] = offset
                df.loc[i, "poke_to_beam_offset_duration"] = offset - poke_time
                df.loc[i, "odor"] = format(odor_val, "016b")
                df.loc[i, "poke_registered"] = True

                try:
                    if odor_val > 0:
                        odor_idx = int(np.log2(odor_val))
                        names = [
                            name for name, idx in odor_map.items() if idx == odor_idx
                        ]
                        df.loc[i, "odor_name"] = names[0] if names else None
                    else:
                        df.loc[i, "odor_name"] = None
                except Exception:
                    df.loc[i, "odor_name"] = None

            else:
                df.loc[i, "poke_registered"] = False

        except IndexError:
            df.loc[i, "poke_registered"] = False

    # -----------------------------
    # APPEND CONSTANT REGISTERS
    # -----------------------------
    for name, value in constant_registers.items():
        df[name] = value

    # -----------------------------
    # ADD DATETIME COLUMN
    # -----------------------------
    df["datetime"] = aeon_api.to_datetime(df["beam_break_onset"])

    return df


# -----------------------------
# MAIN DATASET BUILDER
# -----------------------------


def build_dataframe(
    root_path: pathlib.Path,
    firmware_version: str | None = None,
) -> pd.DataFrame:
    """Orchestrate the full ingestion pipeline for a single session directory.

    Locates the firmware YAML (from the session's ``device.yml`` or from the
    packaged resources), builds Harp readers, loads all register streams, and
    calls :func:`parse_data` to produce the per-poke event dataframe.

    Parameters
    ----------
    root_path:
        Session root directory containing ``behavior/DelphiController/`` and
        ``behavior/metadata/`` sub-directories.
    firmware_version:
        Override firmware version (e.g. ``"0.2.0"``).  When ``None``, the
        version is auto-detected from ``device.yml`` inside the session.

    Returns
    -------
    pd.DataFrame
        Per-poke event dataframe (see :func:`parse_data` for column details).

    Raises
    ------
    RuntimeError
        If ``device.yml`` is not found, if the detected firmware version is
        ``"0.0"``, or if the packaged firmware YAML is not found.
    """
    # -----------------------------
    # DETERMINE YAML SOURCE
    # -----------------------------
    if firmware_version is None:
        # default to device.yml inside session
        yaml_path = root_path / "behavior" / "DelphiController" / "device.yml"

        if not yaml_path.exists():
            raise RuntimeError("device.yml not found and no firmware_version provided")

        print(f"Using device.yml from session: {yaml_path}")

        register_map, detected_fw = parse_register_map(yaml_path)

        # -----------------------------
        # INVALID FIRMWARE CHECK
        # -----------------------------
        if detected_fw == "0.0":
            raise RuntimeError(
                "Detected firmware version 0.0 in device.yml. "
                "Please explicitly provide a valid firmware_version."
            )

        firmware_version = detected_fw

    else:
        # -----------------------------
        # USE PACKAGED FIRMWARE YAML
        # -----------------------------
        delphi_package_root = get_package_root()

        yaml_path = (
            delphi_package_root
            / "resources"
            / "delphi_controller_firmware_versions"
            / f"delphi_controller_{firmware_version}.yml"
        )

        if not yaml_path.exists():
            raise RuntimeError(f"Firmware YAML not found: {yaml_path}")

        print(f"Using packaged firmware YAML: {yaml_path}")

        register_map, _ = parse_register_map(yaml_path)

    # -----------------------------
    # GET REGISTER NAMES
    # -----------------------------
    register_names, _ = get_all_registers(firmware=firmware_version)

    # -----------------------------
    # BUILD READERS
    # -----------------------------
    readers = build_readers(register_names, register_map)

    # -----------------------------
    # LOAD DATA
    # -----------------------------
    data = load_data(root_path, readers)

    # -----------------------------
    # CONTINUE PIPELINE (unchanged)
    # -----------------------------
    constant_registers = extract_constant_registers(data)

    valve_state = data["ValveState"]
    poke_times = data["PokeState"]
    beam_breaks = data["RawPokeState"]

    metadata_path = root_path / "behavior" / "metadata"
    odor_map = build_odor_map(metadata_path)

    break_onsets = aeon_api.to_seconds(
        beam_breaks[beam_breaks["RawPokeState"] == 1].index
    )
    break_offsets = aeon_api.to_seconds(
        beam_breaks[beam_breaks["RawPokeState"] == 0].index
    )
    valve_times = aeon_api.to_seconds(valve_state.index)
    poke_times_sec = aeon_api.to_seconds(poke_times[poke_times["PokeState"] == 1].index)

    df = parse_data(
        break_onsets=break_onsets,
        break_offsets=break_offsets,
        poke_times_sec=poke_times_sec,
        valve_times=valve_times,
        valve_state=valve_state,
        odor_map=odor_map,
        constant_registers=constant_registers,
        firmware_version=firmware_version,
    )

    return df


# -----------------------------
# PUBLIC API
# -----------------------------
def ingest(
    data_root_path: pathlib.Path,
    firmware: str,
) -> pd.DataFrame:
    """Ingest a single Delphi session and return the per-poke event dataframe.

    Thin public wrapper around :func:`build_dataframe`.

    Parameters
    ----------
    data_root_path:
        Run-level session directory containing ``behavior/DelphiController/``
        and ``behavior/metadata/`` sub-directories.
    firmware:
        Firmware version string (e.g. ``"0.1.0"``).  Pass ``None`` to
        auto-detect from the session's ``device.yml``.

    Returns
    -------
    pd.DataFrame
        Per-poke event dataframe (see :func:`parse_data` for column details).
    """
    df = build_dataframe(
        root_path=data_root_path,
        firmware_version=firmware,
    )
    return df


def load_fps_from_harp(behavior_dir: pathlib.Path) -> float | None:
    """Read the configured camera frame rate from the appropriate Harp register.

    The register name and address that holds the camera trigger frequency vary
    across firmware versions.  This function resolves the correct address by
    parsing the register map from:

    1. ``behavior/DelphiController/device.yml`` — session-specific firmware
       definition written by the device at startup (preferred).
    2. Packaged firmware YAMLs in
       ``resources/delphi_controller_firmware_versions/`` — used as a fallback
       when ``device.yml`` is absent or reports an invalid firmware version.

    All registers whose names contain ``"FrameRate"`` are tried in the order
    they appear in the register map; the first non-zero value is returned.

    Known register addresses by firmware version:

    =========  =====================  ============
    Firmware   Register               Address
    =========  =====================  ============
    0.1.0      FrameRate              76
    0.2.0      FrameRate              77
    0.3.0      Cam0FrameRate          76
    0.3.0      Cam1FrameRate          80
    1.0.0      Cam0FrameRate          73
    1.0.0      Cam1FrameRate          77
    =========  =====================  ============

    Parameters
    ----------
    behavior_dir:
        ``behavior/`` directory of the run, containing a ``DelphiController/``
        sub-directory with Harp binary register files.

    Returns
    -------
    float or None
        Configured frame rate in Hz (first non-zero value found across all
        FrameRate-named registers), or ``None`` when no register files are
        present or all are empty.
    """
    delphi_dir = behavior_dir / "DelphiController"

    # --- Resolve register map from device.yml or packaged firmware YAML ---
    register_map: dict = {}

    device_yml = delphi_dir / "device.yml"
    if device_yml.exists():
        try:
            regs, detected_fw = parse_register_map(device_yml)
            if detected_fw and detected_fw != "0.0":
                register_map = regs
            else:
                # device.yml firmware version invalid — try packaged YAMLs
                raise ValueError(f"Invalid firmware version in device.yml: {detected_fw}")
        except Exception:
            pass

    if not register_map:
        # Fall back to packaged firmware YAMLs: try each version and use the
        # first one that produces a non-empty register map.
        pkg_root = get_package_root()
        fw_dir = pkg_root / "resources" / "delphi_controller_firmware_versions"
        for yml_path in sorted(fw_dir.glob("delphi_controller_*.yml")):
            try:
                regs, _ = parse_register_map(yml_path)
                if regs:
                    register_map = regs
                    break
            except Exception:
                continue

    if not register_map:
        return None

    # --- Find all FrameRate-named registers and try loading each ---
    for reg_name, reg_info in register_map.items():
        if "FrameRate" not in reg_name:
            continue
        address = reg_info.get("address")
        if address is None:
            continue
        try:
            rdr = reader.Harp(f"DelphiController_{address}*", columns=[reg_name])
            fr_data = aeon_api.load(delphi_dir, rdr)
            if fr_data is not None and not fr_data.empty:
                fps = float(fr_data[reg_name].iloc[0])
                if fps > 0:
                    return fps
        except Exception:
            continue

    return None


def load_fps_from_rig_config(behavior_dir: pathlib.Path) -> dict:
    """Read per-camera frame rates from ``AindBehaviorPirouetteRig.json``.

    The rig configuration file is written by the acquisition software before
    each session and is more reliable than ``HardwareSettings*.jsonl`` because
    it reflects the values actually passed to the camera controller.

    Frame rate resolution within the file:

    1. ``camera_controller.cameras.<name>.video_writer.frame_rate`` — highest
       fidelity, per-camera.
    2. ``camera_controller.frame_rate`` — global fallback when a camera does
       not carry its own ``video_writer.frame_rate``.

    Parameters
    ----------
    behavior_dir:
        ``behavior/`` directory of the run, containing a ``metadata/``
        sub-directory with ``AindBehaviorPirouetteRig.json``.

    Returns
    -------
    dict
        Mapping of camera name (str) → configured frame rate (float, Hz).
        The special key ``"_all"`` is returned when only the global rate is
        found and no per-camera rates are present.  Returns an empty dict
        when the file is absent or contains no frame rate information.
    """
    import json as _json

    rig_path = behavior_dir / "metadata" / "AindBehaviorPirouetteRig.json"
    if not rig_path.exists():
        return {}

    try:
        cfg = _json.loads(rig_path.read_text())
    except Exception:
        return {}

    cam_ctrl = cfg.get("camera_controller", {})
    global_fps = cam_ctrl.get("frame_rate")
    cameras = cam_ctrl.get("cameras", {})

    rates: dict = {}
    for cam_name, cam_cfg in cameras.items():
        vw = cam_cfg.get("video_writer", {})
        fps = vw.get("frame_rate")
        if fps is not None and float(fps) > 0:
            rates[cam_name] = float(fps)

    if rates:
        return rates

    # Fall back to the global controller frame rate
    if global_fps is not None and float(global_fps) > 0:
        return {"_all": float(global_fps)}

    return {}


def load_fps_from_hardware_settings(behavior_dir: pathlib.Path) -> dict:
    """Read per-camera frame rates from ``HardwareSettings*.jsonl`` metadata files.

    This is a **software fallback** only.  The value written here reflects what
    the acquisition software *intended* to configure, which may differ from the
    rate actually programmed into the hardware register.  Prefer
    :func:`load_fps_from_harp` (the hardware ground truth) when available.

    Parameters
    ----------
    behavior_dir:
        ``behavior/`` directory of the run, containing a ``metadata/``
        sub-directory with ``HardwareSettings*.jsonl`` files.

    Returns
    -------
    dict
        Mapping of camera name (str) → intended frame rate (float, Hz).
        ``cameraSettings`` may be a single dict or a list when multiple cameras
        are configured.  Returns an empty dict when no files or no
        ``cameraSettings`` entries are found.
    """
    import json as _json

    meta_dir = behavior_dir / "metadata"
    rates: dict = {}
    if not meta_dir.is_dir():
        return rates

    for hw_file in sorted(meta_dir.glob("HardwareSettings*.jsonl")):
        try:
            for line in hw_file.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    rec = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                cam_settings = rec.get("value", {}).get("cameraSettings")
                if cam_settings is None:
                    continue
                if isinstance(cam_settings, list):
                    for cs in cam_settings:
                        name = cs.get("cameraName", "_unknown")
                        fps = cs.get("frameRate")
                        if fps and float(fps) > 0:
                            rates[name] = float(fps)
                elif isinstance(cam_settings, dict):
                    name = cam_settings.get("cameraName", "_unknown")
                    fps = cam_settings.get("frameRate")
                    if fps and float(fps) > 0:
                        rates[name] = float(fps)
        except Exception:
            continue

    return rates


def load_configured_frame_rates(behavior_dir: pathlib.Path) -> dict:
    """Resolve camera frame rates through the full source priority chain.

    Sources are checked in priority order, stopping at the first that returns
    a non-empty result:

    1. **Harp ``FrameRate`` register** (``DelphiController_76*.bin``) —
       hardware ground truth; applies to all cameras as ``{"_all": fps}``.
    2. **``AindBehaviorPirouetteRig.json``** — rig configuration written by
       the acquisition software; provides per-camera rates from
       ``camera_controller.cameras.<name>.video_writer.frame_rate``.
    3. **``HardwareSettings*.jsonl``** — software fallback; may not reflect
       the rate actually programmed into the hardware.

    When all sources are exhausted the caller should fall back to
    :data:`delphi_data.quality_control.DEFAULT_CAMERA_FPS`.

    Parameters
    ----------
    behavior_dir:
        ``behavior/`` directory of the run.

    Returns
    -------
    dict
        Non-empty dict of camera name → fps on success, or ``{}`` when no
        source is available.  ``"_all"`` is used as the key when a single
        rate applies to every camera.
    """
    # 1. Hardware ground truth
    harp_fps = load_fps_from_harp(behavior_dir)
    if harp_fps is not None:
        return {"_all": harp_fps}

    # 2. Rig configuration file
    rig_rates = load_fps_from_rig_config(behavior_dir)
    if rig_rates:
        return rig_rates

    # 3. HardwareSettings JSONL (software fallback)
    return load_fps_from_hardware_settings(behavior_dir)


def load_camera_frames(
    camera_dir: pathlib.Path,
    camera_name: str,
    start: "pd.Timestamp | None" = None,
    end: "pd.Timestamp | None" = None,
) -> "pd.DataFrame | None":
    """Load the frame-index CSV for one camera using the Aeon Video reader.

    The Aeon ``Video`` reader parses files matching
    ``<camera_name>_YYYY-MM-DDTHH-MM-SS.csv`` and returns a DataFrame whose
    index contains Harp timestamps (converted to ``pd.DatetimeIndex``) and
    whose columns include ``_path`` (source video file path) and ``_frame``
    (zero-based frame index within that file).

    Parameters
    ----------
    camera_dir:
        Directory containing the camera's ``*.csv`` frame-index files
        (e.g. ``behavior-videos/PortCamera/``).
    camera_name:
        Camera name prefix used to match index files (e.g. ``"PortCamera"``).
    start:
        Optional window start timestamp.  ``None`` loads from the beginning.
    end:
        Optional window end timestamp.  ``None`` loads to the end.

    Returns
    -------
    pd.DataFrame or None
        Frame-index DataFrame, or ``None`` if no data are found or an error
        occurs.
    """
    try:
        kwargs: dict = {}
        if start is not None:
            kwargs["start"] = start
        if end is not None:
            kwargs["end"] = end
        data = aeon_api.load(
            camera_dir,
            CAMERA_VIDEO_READER_FACTORY(f"{camera_name}_*"),
            **kwargs,
        )
        if data is None or data.empty:
            return None
        return data
    except Exception:
        return None
