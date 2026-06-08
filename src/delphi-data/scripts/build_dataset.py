"""Build a Delphi behavioral dataset CSV from a raw session directory.

The script consolidates multiple run sub-directories (if present), runs the
full Delphi ingestion pipeline, and writes the per-poke event CSV to
``<data_root>/behavior/delphi_dataset.csv``.

Usage::

    python build_dataset.py --data-root /path/to/run_dir --firmware 0.1.0
    python build_dataset.py --data-root /path/to/run_dir --firmware 0.1.0 --no-consolidate

This script is also available via the installed ``delphi-data`` CLI::

    delphi-data build-dataset --data-root /path/to/run_dir --firmware 0.1.0
"""

from __future__ import annotations

import argparse
import pathlib
import sys

# Ensure the package root is importable when running the script directly.
_script_dir = pathlib.Path(__file__).resolve().parent
_pkg_root = _script_dir.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

from delphi_data.curation import consolidate_metadata_files, consolidate_session_runs
from delphi_data.ingestion import ingest


def build_dataset(
    data_root: pathlib.Path,
    firmware: str,
    consolidate_runs: bool = True,
    append: bool = False,
) -> "pd.DataFrame":
    """Build a Delphi dataset and save the per-poke CSV to the behavior folder.

    Optionally consolidates multiple run sub-directories into the earliest one
    before running ingestion.  When *append* is ``True`` and a CSV already
    exists, the newly ingested rows are merged with the existing file and
    deduplicated on ``beam_break_onset`` (which is unique per beam-break event).

    Parameters
    ----------
    data_root:
        Path to the run-level session directory (contains ``behavior/`` and
        timestamp-named run sub-directories).
    firmware:
        Firmware version string, e.g. ``"0.1.0"``.  Pass ``None`` to
        auto-detect from ``device.yml`` inside the session.
    consolidate_runs:
        When ``True`` and multiple run sub-directories are detected, merge them
        into the earliest run directory using
        :func:`~delphi_data.curation.consolidate_session_runs`.
    append:
        When ``True`` and ``behavior/delphi_dataset.csv`` already exists,
        ingest the full session and append new rows to the existing CSV,
        keeping the first occurrence of each ``beam_break_onset`` value.
        When ``False`` (default), the existing CSV is overwritten.

    Returns
    -------
    pd.DataFrame
        The per-poke event dataframe that was saved to CSV.
    """
    run_dirs = [p for p in data_root.iterdir() if p.is_dir()]
    multiple_runs_detected = len(run_dirs) > 1

    if multiple_runs_detected:
        print(f"Detected {len(run_dirs)} run directories in session: {data_root}")
    else:
        print(f"Single run detected in session: {data_root}")

    if multiple_runs_detected and consolidate_runs:
        print("Consolidating run directories …")
        consolidate_session_runs(str(data_root))
        print("Consolidation complete.")
    elif multiple_runs_detected and not consolidate_runs:
        print("Warning: multiple runs detected but consolidation is disabled.")

    # Move any HardwareSettings / RuleSettings files that landed in the
    # top-level metadata/ directory instead of behavior/metadata/.
    print("Consolidating metadata files …")
    moved = consolidate_metadata_files(data_root)
    if moved:
        print(f"  Moved {len(moved)} metadata file(s) to behavior/metadata/")
    else:
        print("  No metadata files to move.")

    df = ingest(
        data_root_path=data_root,
        firmware=firmware,
    )

    behavior_dir = data_root / "behavior"
    behavior_dir.mkdir(exist_ok=True)

    output_path = behavior_dir / "delphi_dataset.csv"

    # --- Append mode: merge with existing CSV and deduplicate ---
    if append and output_path.exists():
        import pandas as pd
        existing = pd.read_csv(output_path)
        n_existing = len(existing)
        combined = pd.concat([existing, df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["beam_break_onset"], keep="first")
        combined = combined.sort_values("beam_break_onset").reset_index(drop=True)
        n_new = len(combined) - n_existing
        print(f"Appending {n_new} new row(s) to existing {n_existing}-row dataset "
              f"(deduplicated on beam_break_onset).")
        df = combined

    df.to_csv(output_path, index=False)
    print(f"Dataset saved to: {output_path}")

    return df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments for the build-dataset script.

    Parameters
    ----------
    argv:
        Argument list.  ``None`` reads from ``sys.argv[1:]``.

    Returns
    -------
    argparse.Namespace
        Parsed argument namespace with ``session_dir``, ``firmware``, and
        ``no_consolidate`` attributes.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Build a Delphi behavioral dataset CSV from a raw session directory.\n\n"
            "The output is written to <SESSION_DIR>/behavior/delphi_dataset.csv."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--data-root", required=True,
        help="Path to the run-level session directory.",
    )
    parser.add_argument(
        "--firmware", required=True,
        help='Firmware version string, e.g. "0.1.0".',
    )
    parser.add_argument(
        "--no-consolidate", action="store_true", default=False,
        help="Disable automatic consolidation of multiple run sub-directories.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    build_dataset(
        session_dir=pathlib.Path(args.data_root),
        firmware=args.firmware,
        consolidate_runs=not args.no_consolidate,
    )
