"""Build a Delphi behavioral dataset CSV from a raw session directory.

The script consolidates multiple run sub-directories (if present), runs the
full Delphi ingestion pipeline, and writes the per-poke event CSV to
``<data_root>/behavior/delphi_dataset.csv``.

Usage::

    python build_dataset.py --data-root /path/to/run_dir --firmware 0.1.0
    python build_dataset.py --data-root /path/to/run_dir --firmware 0.1.0 --no-consolidate
    python build_dataset.py --consolidate-only  # when DELPHI_DATA_ROOT set in .env

All arguments can be defaulted via ``DELPHI_*`` environment variables or a
``.env`` file in the project root.  When ``DELPHI_DATA_ROOT`` and
``DELPHI_FIRMWARE`` are set, the script can be invoked with no arguments::

    python build_dataset.py

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

from delphi_data.curation import (
    collect_run_dirs,
    consolidate_metadata_files,
    consolidate_session_runs,
    find_earliest_run,
)
from delphi_data.ingestion import ingest
from delphi_data.settings import Settings as _Settings

_s = _Settings()


def consolidate_session(
    data_root: pathlib.Path,
    consolidate_runs: bool = True,
) -> pathlib.Path:
    """Consolidate run sub-directories and move metadata files into place.

    Detects timestamp-named run sub-directories inside *data_root*, merges
    them into the earliest one (when *consolidate_runs* is ``True``), descends
    into that run directory, and moves any ``HardwareSettings`` /
    ``RuleSettings`` JSONL files from a top-level ``metadata/`` directory into
    ``behavior/metadata/``.

    Parameters
    ----------
    data_root:
        Path to the session root or an already-resolved run directory.
    consolidate_runs:
        When ``True`` and multiple run sub-directories are detected, merge them
        into the earliest one.  When ``False``, a warning is printed and the
        earliest run directory is still selected.

    Returns
    -------
    pathlib.Path
        The resolved run directory (earliest timestamp sub-directory, or
        *data_root* unchanged when no timestamp sub-directories exist).
    """
    ts_run_dirs = collect_run_dirs(str(data_root))

    if len(ts_run_dirs) > 1:
        print(f"Detected {len(ts_run_dirs)} run directories in session: {data_root}")
        if consolidate_runs:
            print("Consolidating run directories …")
            consolidate_session_runs(str(data_root))
            print("Consolidation complete.")
        else:
            print("Warning: multiple runs detected but consolidation is disabled.")
        ts_run_dirs = collect_run_dirs(str(data_root))
        data_root = pathlib.Path(find_earliest_run(ts_run_dirs))
        print(f"Using run directory: {data_root}")
    elif len(ts_run_dirs) == 1:
        data_root = pathlib.Path(ts_run_dirs[0])
        print(f"Single run detected — using run directory: {data_root}")
    else:
        print(f"Using run directory: {data_root}")

    print("Consolidating metadata files …")
    moved = consolidate_metadata_files(data_root)
    if moved:
        print(f"  Moved {len(moved)} metadata file(s) to behavior/metadata/")
    else:
        print("  No metadata files to move.")

    return data_root


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
        Path to either the session root (containing timestamp-named run
        sub-directories) or a single run directory (containing ``behavior/``
        directly).  When timestamp sub-directories are found, the script
        consolidates them (if ``consolidate_runs`` is True) and descends into
        the earliest run directory automatically.
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
    data_root = consolidate_session(data_root, consolidate_runs=consolidate_runs)

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
        Parsed argument namespace with ``data_root``, ``firmware``,
        ``no_consolidate``, and ``consolidate_only`` attributes.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Build a Delphi behavioral dataset CSV from a raw session directory.\n\n"
            "The output is written to <SESSION_DIR>/behavior/delphi_dataset.csv.\n\n"
            "Pass --consolidate-only to merge run sub-directories without building\n"
            "the dataset CSV (useful for Pirouette sessions)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--data-root",
        default=_s.data_root,
        required=_s.data_root is None,
        help=(
            "Path to the session root or run-level directory.  "
            f"[env: DELPHI_DATA_ROOT, current: {_s.data_root!r}]"
        ),
    )
    parser.add_argument(
        "--firmware",
        default=_s.firmware,
        help=(
            'Firmware version string, e.g. "0.1.0". Required unless --consolidate-only.  '
            f"[env: DELPHI_FIRMWARE, current: {_s.firmware!r}]"
        ),
    )
    parser.add_argument(
        "--no-consolidate", action="store_true", default=_s.no_consolidate,
        help=(
            "Disable automatic consolidation of multiple run sub-directories.  "
            f"[env: DELPHI_NO_CONSOLIDATE, current: {_s.no_consolidate}]"
        ),
    )
    parser.add_argument(
        "--consolidate-only", action="store_true", default=_s.consolidate_only,
        help=(
            "Consolidate run sub-directories and move metadata files, then exit "
            "without building the dataset CSV.  --firmware is not required.  "
            "Overrides --no-consolidate when set.  "
            f"[env: DELPHI_CONSOLIDATE_ONLY, current: {_s.consolidate_only}]"
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()

    if args.consolidate_only:
        consolidate_session(pathlib.Path(args.data_root), consolidate_runs=True)
        print("Done.")
    else:
        if not args.firmware:
            import sys
            print("error: --firmware is required when not using --consolidate-only")
            sys.exit(1)
        build_dataset(
            data_root=pathlib.Path(args.data_root),
            firmware=args.firmware,
            consolidate_runs=not args.no_consolidate,
        )
