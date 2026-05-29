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


# -----------------------------
# PACKAGE ROOT TO GET FIRMWARE YAML PATH
# -----------------------------
def get_package_root() -> pathlib.Path:
    module = sys.modules[get_all_registers.__module__]
    return pathlib.Path(module.__file__).resolve().parent.parent


# -----------------------------
# YAML PARSING FOR FIRMWARE VERSION COMPATIBILITY
# -----------------------------
def parse_register_map(yaml_path: pathlib.Path):
    with open(yaml_path, "r") as f:
        config = yaml.safe_load(f)

    registers = config.get("registers", {})
    firmware = config.get("firmwareVersion", None)

    return registers, firmware


def build_readers(register_names, register_map):
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
def load_data(root_path, readers, start_cutoff=None, end_cutoff=None):
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
    """
    Extract constant registers and apply defaults for timing registers if missing.

    Returns:
        Dict mapping register name -> scalar value (seconds)
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
    """
    Build mapping: odor name -> index

    Handles:
    - multiple RuleSettings files
    - multiple definitions per odor
    - non-power-of-two odorIndex values
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

    return df


# -----------------------------
# MAIN DATASET BUILDER
# -----------------------------


def build_dataframe(
    root_path: pathlib.Path,
    firmware_version: str | None = None,
):
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
):
    """
    Main API

    Returns:
        df: pd.DataFrame
        register_values: dict
        odor_map: dict
    """
    df = build_dataframe(
        root_path=data_root_path,
        firmware_version=firmware,
    )
    return df
