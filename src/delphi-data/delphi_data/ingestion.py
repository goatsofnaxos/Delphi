from __future__ import annotations

import math
import pathlib

import numpy as np
import pandas as pd
import yaml
from swc.aeon.io import api as aeon_api
from swc.aeon.io import reader

# -----------------------------
# DEFAULT REGISTERS
# -----------------------------
DEFAULT_POKE_REGISTERS = [
    "ValveState",
    "PokeState",
    "RawPokeState",
]

DEFAULT_VIDEO_REGISTERS = [
    "CamPinState",
    "FrameRate",
]


# -----------------------------
# YAML PARSING
# -----------------------------
def parse_register_map(yaml_path: pathlib.Path) -> dict:
    with open(yaml_path, "r") as f:
        config = yaml.safe_load(f)
    return config.get("registers", {})


def build_readers(register_names, register_map):
    readers = {}

    for name in register_names:
        if name not in register_map:
            raise KeyError(f"{name} not found in YAML")

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
            start=start_cutoff,
            stop=end_cutoff,
        )
        for name, rdr in readers.items()
    }


def extract_register_values(data):
    values = {}

    for name, df in data.items():
        if not df.empty and df.shape[1] > 0:
            val = df.iloc[0].values[0]

            # auto convert microseconds → seconds
            if name.endswith("US"):
                val *= 1e-6

            values[name] = val

    return values


# -----------------------------
# ODOR MAP BUILDER
# -----------------------------
def build_odor_map(metadata_path: pathlib.Path) -> dict:
    import json
    import os

    odor_map = {}

    for file in os.listdir(metadata_path):
        if "RuleSettings" not in file:
            continue

        full_path = metadata_path / file

        with open(full_path, "r") as f:
            for line in f:
                record = json.loads(line)

                try:
                    states = record["value"]["rule"]["stateDefinitions"]
                except KeyError:
                    continue

                for state in states:
                    name = state.get("name")

                    if not name or name == "DefaultState":
                        continue

                    try:
                        idx = int(math.log2(state["odorIndex"]))
                    except Exception:
                        continue

                    odor_map[name] = idx

    return odor_map


# -----------------------------
# MAIN DATAFRAME BUILDER
# -----------------------------
def build_delphi_dataframe(
    root_path: pathlib.Path,
    yaml_path: pathlib.Path,
    metadata_path: pathlib.Path,
    register_names=None,
    start_cutoff=None,
    end_cutoff=None,
):
    # enforce default
    register_names = register_names or DEFAULT_REGISTERS

    if start_cutoff and end_cutoff and start_cutoff >= end_cutoff:
        raise ValueError("start_cutoff must be before end_cutoff")

    # YAML + readers
    register_map = parse_register_map(yaml_path)
    readers = build_readers(register_names, register_map)

    # Load AEON data
    data = load_data(
        root_path,
        readers,
        start_cutoff=start_cutoff,
        end_cutoff=end_cutoff,
    )

    register_vals = extract_register_values(data)

    # Extract streams
    valve_state = data["ValveState"]
    poke_times = data["PokeState"]
    beam_breaks = data["RawPokeState"]

    # Build odor map
    odor_map = build_odor_map(metadata_path)

    # Convert timestamps
    break_onsets = aeon_api.to_seconds(
        beam_breaks[beam_breaks["RawPokeState"] == 1].index
    )
    break_offsets = aeon_api.to_seconds(
        beam_breaks[beam_breaks["RawPokeState"] == 0].index
    )
    valve_times = aeon_api.to_seconds(valve_state.index)
    poke_times_sec = aeon_api.to_seconds(poke_times[poke_times["PokeState"] == 1].index)

    df = pd.DataFrame()
    poke_n = 0

    for i, onset in enumerate(break_onsets):
        try:
            offset_idx = np.where((break_offsets.values - onset) > 0)[0][0]
            offset = break_offsets[offset_idx]

            poke_idx = np.where((poke_times_sec.values - onset) > 0)[0][0]
            poke_time = poke_times_sec.values[poke_idx]

            df.loc[i, "beam_break_onset"] = onset
            df.loc[i, "beam_break_offset"] = offset
            df.loc[i, "beam_break_duration"] = offset - onset

            if onset <= poke_time <= offset:
                poke_n += 1

                valve_idx = np.argmin(np.abs(poke_time - valve_times.values))

                odor_val = valve_state.iloc[valve_idx - 1].values[0]
                odor_binary = format(odor_val, "016b")

                df.loc[i, "poke_number"] = poke_n
                df.loc[i, "poke_onset"] = poke_time
                df.loc[i, "poke_offset"] = offset
                df.loc[i, "poke_duration"] = offset - poke_time
                df.loc[i, "odor"] = odor_binary
                df.loc[i, "poke_registered"] = True

                # map odor index → name
                try:
                    odor_idx = int(math.log2(odor_val))
                    names = [name for name, idx in odor_map.items() if idx == odor_idx]
                    df.loc[i, "odor_name"] = names[0] if names else None
                except Exception:
                    df.loc[i, "odor_name"] = None

            else:
                df.loc[i, "poke_registered"] = False

        except IndexError:
            df.loc[i, "poke_registered"] = False

    return df, register_vals, odor_map


# -----------------------------
# PUBLIC API
# -----------------------------
def ingest(
    root_path: pathlib.Path,
    yaml_path: pathlib.Path,
    metadata_path: pathlib.Path,
    register_names=None,
    start_cutoff=None,
    end_cutoff=None,
):
    """
    Main API

    Returns:
        df: pd.DataFrame
        register_values: dict
        odor_map: dict
    """
    return build_delphi_dataframe(
        root_path=root_path,
        yaml_path=yaml_path,
        metadata_path=metadata_path,
        register_names=register_names or DEFAULT_REGISTERS,
        start_cutoff=start_cutoff,
        end_cutoff=end_cutoff,
    )
