#!/usr/bin/env python3
"""
load_pokeTable.py

Walk a root directory, find session folders (containing behavior/DelphiController),
and for each session save the poke DataFrame as behavior/poke_table.csv.

Usage:
    python load_pokeTable.py /input_path
    python load_pokeTable.py /input_path --output /output_path

    where {rel} is the session path relative to /input_path,
    e.g. data_mouse3/2026-05-07_12-00-00

    Without --output: writes to /input_path/{rel}/behavior/poke_table.csv
    With --output:    writes to /output_path/{rel}/behavior/poke_table.csv
"""

# ── imports ────────────────────────────────────────────────────────────────────

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from delphi.functions import build_delphi_df
import swc.aeon.io.api as aeon_api

# ── constants ──────────────────────────────────────────────────────────────────

TABLE_NAME = "poke_table"

# ── discovery ──────────────────────────────────────────────────────────────────

def find_session_dirs(root):
    """Yield session dirs that contain behavior/DelphiController."""
    for delphi_dir in sorted(root.rglob("behavior/DelphiController")):
        yield delphi_dir.parent.parent

# ── processing ─────────────────────────────────────────────────────────────────

def process_session(session_dir, out_dir):
    delphi_folder = session_dir / "behavior" / "DelphiController"
    out_path      = out_dir / "behavior" / f"{TABLE_NAME}.csv"

    # if a table already exists, read it and find the latest beam_break_onset so
    # build_delphi_df only processes new data beyond that point (incremental update)
    existing, cutoff = None, None
    if out_path.exists():
        existing = pd.read_csv(out_path)
        if not existing.empty and "beam_break_onset" in existing.columns:
            cutoff = aeon_api.to_datetime(existing["beam_break_onset"].max())

    try:
        new_df = build_delphi_df(root_path=delphi_folder, cutoff=cutoff)
    except Exception as e:
        print(f"  [error] {session_dir}: {e}")
        return

    # empty result means either no pokes exist, or we're already up to date
    if new_df.empty:
        print(f"  [up to date] {out_path}" if existing is not None else f"  [empty] no pokes in {session_dir.name}")
        return

    # append new rows to existing table, or write fresh if none existed
    df = pd.concat([existing, new_df], ignore_index=True) if existing is not None else new_df
    print(f"  [updated] +{len(new_df)} rows → {out_path}" if existing is not None else f"  [saved] {len(df)} rows → {out_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Save poke DataFrames from Delphi session data.")
    parser.add_argument("root",     help="Root directory to search for sessions")
    parser.add_argument("--output", help="Output base directory (default: write alongside source data)")
    args = parser.parse_args()

    root        = Path(args.root).resolve()
    output_base = Path(args.output).resolve() if args.output else None

    session_dirs = list(find_session_dirs(root))
    print(f"Found {len(session_dirs)} session(s) under {root}")

    for session_dir in session_dirs:
        rel     = session_dir.relative_to(root)
        out_dir = (output_base / rel) if output_base is not None else session_dir
        print(f"\nSession: {rel}")
        process_session(session_dir, out_dir)

    print("\nAll done.")


if __name__ == "__main__":
    main()
