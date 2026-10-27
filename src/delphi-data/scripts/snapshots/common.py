"""Universal/common behavioral snapshot — works for any experiment type.

Generates a baseline set of figures that are experiment-agnostic:

- Summary statistics table
- Poke raster over a 24-hour layout (rows = days, columns = time of day)
- Poke rate across the dataset (datetime on x-axis)
- Cumulative poke count (datetime on x-axis)
- Poke-rate time series (exponential-decay smoothed, x-axis in days)
- Inter-poke interval distribution
- Poke duration by odor
- QC plots

This snapshot runs automatically when ``--experiment`` is omitted from
``data_snapshot.py`` / ``delphi-data snapshot``.

Usage (standalone)::

    python common.py --data-root /path/to/run_dir

Usage via the router (explicit)::

    delphi-data snapshot --experiment common --data-root /path/to/run_dir

Usage via the router (implicit — no experiment flag)::

    delphi-data snapshot --data-root /path/to/run_dir
"""

from __future__ import annotations

import argparse
import pathlib
import sys

_here = pathlib.Path(__file__).resolve()
_scripts_dir = _here.parent.parent
_pkg_root = _scripts_dir.parent
for _p in [str(_pkg_root), str(_scripts_dir)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from snapshots._common import run_common_snapshot


def run_snapshot(
    data_root: str | pathlib.Path,
    subject_id: str | None = None,
    tau: float = 600.0,
    dt: float = 60.0,
    overlap: float = 0.5,
    camera_fps: float | None = None,
) -> None:
    """Generate the universal common snapshot figures.

    Parameters
    ----------
    data_root:
        Run-level session directory containing ``behavior/delphi_dataset.csv``.
    subject_id:
        Subject identifier.  Inferred from the path hierarchy when ``None``.
    tau:
        Exponential decay time constant for the rate estimator (seconds).
    dt:
        Window length for the rate estimator (seconds).
    overlap:
        Fractional window overlap ``[0, 1)``.
    camera_fps:
        Override camera frame rate (Hz) for QC plots.  ``None`` to auto-detect.
    """
    run_common_snapshot(
        data_root=pathlib.Path(data_root),
        subject_id=subject_id,
        tau=tau,
        dt=dt,
        overlap=overlap,
        camera_fps_override=camera_fps,
    )


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate universal common snapshot figures.\n\n"
            "Expects 'behavior/delphi_dataset.csv' inside DATA_ROOT and writes "
            "all figures to 'behavior/results/'."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    try:
        from delphi_data.settings import settings as _s
        data_root_default = _s.data_root
        tau_default = _s.tau
        dt_default = _s.dt
        overlap_default = _s.overlap
        camera_fps_default = _s.camera_fps
    except Exception:
        data_root_default = None
        tau_default = 600.0
        dt_default = 60.0
        overlap_default = 0.5
        camera_fps_default = None

    parser.add_argument(
        "--data-root", default=data_root_default, required=data_root_default is None,
        help="Run-level session directory.",
    )
    parser.add_argument("--subject-id", default=None,
                        help="Subject ID (default: inferred from path).")
    parser.add_argument("--tau", type=float, default=tau_default)
    parser.add_argument("--dt", type=float, default=dt_default)
    parser.add_argument("--overlap", type=float, default=overlap_default)
    parser.add_argument(
        "--camera-fps", type=float, default=camera_fps_default, dest="camera_fps",
        help="Override camera frame rate for QC plots (Hz).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    run_snapshot(
        data_root=args.data_root,
        subject_id=args.subject_id,
        tau=args.tau,
        dt=args.dt,
        overlap=args.overlap,
        camera_fps=args.camera_fps,
    )
