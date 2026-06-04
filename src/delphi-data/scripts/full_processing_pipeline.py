"""Full processing pipeline for a single Delphi behavioral session.

Runs three sequential steps against one run-level session directory:

1. **build-dataset** — ingest raw Harp register streams and write
   ``behavior/delphi_dataset.csv``.
2. **create-clips** — extract poke-triggered MP4 clips from the
   ``behavior-videos/PortCamera/`` chunks.
3. **snapshot** — generate the full suite of experiment-specific behavioral
   figures and QC plots, saving them to ``behavior/results/``.

Each step can be skipped independently.  All defaults are drawn from the
``DELPHI_*`` environment variables / ``.env`` file, and any CLI flag
overrides the corresponding ``.env`` value.

Usage::

    # Run all three steps using .env defaults
    python full_processing_pipeline.py

    # Override data root and experiment on the command line
    python full_processing_pipeline.py --data-root /path/to/run --experiment bonhoeffer

    # Skip clip extraction (e.g. no camera data)
    python full_processing_pipeline.py --skip-clips

    # Only run the snapshot step
    python full_processing_pipeline.py --skip-build --skip-clips

    # Pass extra snapshot parameters
    python full_processing_pipeline.py --tau 300 --camera-fps 100

This script is also available via the installed ``delphi-data`` CLI::

    delphi-data pipeline

Configuration via ``.env``
--------------------------
All pipeline parameters can be set in a ``.env`` file at the project root.
The following variables are read (see ``.env.example`` for the full list):

- ``DELPHI_DATA_ROOT`` — run-level session directory (required if not on CLI)
- ``DELPHI_EXPERIMENT`` — experiment type, e.g. ``bonhoeffer`` (required if not on CLI)
- ``DELPHI_FIRMWARE`` — firmware version for ingestion (auto-detected if unset)
- ``DELPHI_TAU``, ``DELPHI_DT``, ``DELPHI_OVERLAP`` — poke-rate estimation
- ``DELPHI_PRE_DAYS``, ``DELPHI_POST_DAYS`` — windowed analysis windows
- ``DELPHI_N_POKES_DURATION`` — pokes per day for duration comparison
- ``DELPHI_CAMERA_FPS`` — camera frame-rate override for QC plots
- ``DELPHI_WORKERS`` — parallel clip-export threads
- ``DELPHI_NO_DELETE`` — keep source PortCamera files after clip export
- ``DELPHI_NO_CONSOLIDATE`` — skip run-directory consolidation
"""

from __future__ import annotations

import argparse
import importlib
import pathlib
import sys
import time
import traceback

# Ensure the package root and scripts directory are importable.
_here = pathlib.Path(__file__).resolve().parent
_pkg_root = _here.parent
for _p in [str(_pkg_root), str(_here)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from delphi_data.settings import settings as _s
from snapshots import REGISTRY as _REGISTRY


# ---------------------------------------------------------------------------
# Individual step functions
# ---------------------------------------------------------------------------


def run_build_dataset(
    data_root: pathlib.Path,
    firmware: str | None,
    consolidate_runs: bool,
) -> bool:
    """Run the dataset ingestion step.

    Calls :func:`build_dataset.build_dataset` to ingest raw Harp streams and
    write ``behavior/delphi_dataset.csv``.  Skipped automatically when the
    output CSV already exists and *firmware* is ``None`` (auto-detect mode).

    Parameters
    ----------
    data_root:
        Run-level session directory.
    firmware:
        Firmware version string passed to the ingestion pipeline.  ``None``
        triggers auto-detection from the session's ``device.yml``.
    consolidate_runs:
        When ``True``, merge multiple run sub-directories into the earliest
        one before ingestion.

    Returns
    -------
    bool
        ``True`` when the step completed successfully, ``False`` on error.
    """
    from build_dataset import build_dataset

    csv_path = data_root / "behavior" / "delphi_dataset.csv"
    if csv_path.exists():
        print(f"  [skip] delphi_dataset.csv already exists: {csv_path}")
        return True

    try:
        build_dataset(
            data_root=data_root,
            firmware=firmware,
            consolidate_runs=consolidate_runs,
        )
        return True
    except Exception:
        print("  [ERROR] build_dataset failed:")
        traceback.print_exc()
        return False


def run_create_clips(
    data_root: pathlib.Path,
    n_workers: int,
    no_delete: bool,
) -> bool:
    """Run the poke-clip extraction step.

    Calls :func:`delphi_data.video_processing.process_session` to extract
    per-poke MP4 clips from ``behavior-videos/PortCamera/`` chunks.  Skipped
    when no ``behavior-videos/PortCamera/`` folder is found.

    Parameters
    ----------
    data_root:
        Run-level session directory.
    n_workers:
        Number of parallel clip-export threads.
    no_delete:
        When ``True``, retain source ``.mp4`` and ``.csv`` chunk files after
        exporting clips.

    Returns
    -------
    bool
        ``True`` when the step completed (or was skipped) successfully,
        ``False`` on error.
    """
    from delphi_data.video_processing import process_session

    port_cam_dir = data_root / "behavior-videos" / "PortCamera"
    if not port_cam_dir.is_dir():
        print("  [skip] No PortCamera folder found — skipping clip extraction.")
        return True

    try:
        process_session(
            session_dir=data_root,
            n_workers=n_workers,
            no_delete=no_delete,
        )
        return True
    except Exception:
        print("  [ERROR] create_clips failed:")
        traceback.print_exc()
        return False


def run_snapshot(
    data_root: pathlib.Path,
    experiment: str,
    subject_id: str | None,
    tau: float,
    dt: float,
    overlap: float,
    pre_days: int,
    post_days: int,
    n_pokes_duration: int,
    camera_fps: float | None,
) -> bool:
    """Run the experiment-specific snapshot step.

    Dynamically imports the snapshot module registered under *experiment* and
    calls its ``run_snapshot`` function.

    Parameters
    ----------
    data_root:
        Run-level session directory.
    experiment:
        Experiment type key (e.g. ``"bonhoeffer"``).  Must be registered in
        :data:`snapshots.REGISTRY`.
    subject_id:
        Subject identifier.  ``None`` triggers path-based inference.
    tau:
        Exponential decay time constant for poke-rate estimation (seconds).
    dt:
        Window length for poke-rate estimation (seconds).
    overlap:
        Fractional window overlap ``[0, 1)``.
    pre_days:
        Baseline days before each odor change.
    post_days:
        Post-change days for windowed analyses.
    n_pokes_duration:
        Pokes per day for the duration-comparison figure.
    camera_fps:
        Camera frame-rate override for QC plots (Hz).  ``None`` triggers
        auto-detection.

    Returns
    -------
    bool
        ``True`` when the step completed successfully, ``False`` on error.
    """
    if experiment not in _REGISTRY:
        print(f"  [ERROR] Unknown experiment '{experiment}'.  "
              f"Available: {', '.join(_REGISTRY)}")
        return False

    mod = importlib.import_module(_REGISTRY[experiment])
    try:
        mod.run_snapshot(
            data_root=data_root,
            subject_id=subject_id,
            tau=tau,
            dt=dt,
            overlap=overlap,
            pre_days=pre_days,
            post_days=post_days,
            n_pokes_duration=n_pokes_duration,
            camera_fps=camera_fps,
        )
        return True
    except Exception:
        print("  [ERROR] snapshot failed:")
        traceback.print_exc()
        return False


# ---------------------------------------------------------------------------
# Master pipeline
# ---------------------------------------------------------------------------


def run_pipeline(
    data_root: str | pathlib.Path,
    experiment: str,
    firmware: str | None = None,
    consolidate_runs: bool = True,
    subject_id: str | None = None,
    tau: float = 600.0,
    dt: float = 60.0,
    overlap: float = 0.5,
    pre_days: int = 3,
    post_days: int = 1,
    n_pokes_duration: int = 25,
    camera_fps: float | None = None,
    n_workers: int = 4,
    no_delete: bool = False,
    skip_build: bool = False,
    skip_clips: bool = False,
    skip_snapshot: bool = False,
) -> dict:
    """Run the full three-step Delphi processing pipeline for one session.

    Steps are executed in order: build -> clips -> snapshot.  A step failure
    is reported but does not abort the subsequent steps unless the output of
    the failed step is required.

    Parameters
    ----------
    data_root:
        Run-level session directory (must contain ``behavior/`` and
        ``behavior-videos/`` sub-directories).
    experiment:
        Experiment type registered in :data:`snapshots.REGISTRY`
        (e.g. ``"bonhoeffer"``).
    firmware:
        Firmware version for ingestion.  ``None`` auto-detects from
        ``device.yml``.
    consolidate_runs:
        When ``True``, merge multiple timestamp run sub-directories before
        ingestion.
    subject_id:
        Subject identifier for figure titles.  ``None`` infers from the path.
    tau:
        Exponential decay time constant (seconds).
    dt:
        Rate-estimator window length (seconds).
    overlap:
        Rate-estimator window overlap ``[0, 1)``.
    pre_days:
        Baseline days before each odor change.
    post_days:
        Post-change days for windowed analyses.
    n_pokes_duration:
        Pokes per day for the duration-comparison figure.
    camera_fps:
        Camera frame-rate override for QC plots (Hz).  ``None`` auto-detects.
    n_workers:
        Parallel clip-export threads.
    no_delete:
        Retain PortCamera source files after clip export.
    skip_build:
        Skip the dataset ingestion step.
    skip_clips:
        Skip the poke-clip extraction step.
    skip_snapshot:
        Skip the snapshot/figure generation step.

    Returns
    -------
    dict
        Status dict with keys ``"build"``, ``"clips"``, ``"snapshot"`` each
        mapped to ``"ok"``, ``"skipped"``, or ``"failed"``.
    """
    data_root = pathlib.Path(data_root)
    status: dict = {}

    print(f"\n{'=' * 65}")
    print(f"  Delphi Full Processing Pipeline")
    print(f"  data_root  : {data_root}")
    print(f"  experiment : {experiment}")
    print(f"{'=' * 65}\n")

    # -----------------------------------------------------------------------
    # Step 1 — Build dataset
    # -----------------------------------------------------------------------
    _step_header("1 / 3", "Build dataset", skip_build)
    if skip_build:
        status["build"] = "skipped"
    else:
        t0 = time.perf_counter()
        ok = run_build_dataset(data_root, firmware, consolidate_runs)
        status["build"] = "ok" if ok else "failed"
        print(f"  -> {status['build'].upper()}  ({time.perf_counter() - t0:.1f} s)\n")

    # -----------------------------------------------------------------------
    # Step 2 — Create poke clips
    # -----------------------------------------------------------------------
    _step_header("2 / 3", "Create poke clips", skip_clips)
    if skip_clips:
        status["clips"] = "skipped"
    else:
        t0 = time.perf_counter()
        ok = run_create_clips(data_root, n_workers, no_delete)
        status["clips"] = "ok" if ok else "failed"
        print(f"  -> {status['clips'].upper()}  ({time.perf_counter() - t0:.1f} s)\n")

    # -----------------------------------------------------------------------
    # Step 3 — Snapshot
    # -----------------------------------------------------------------------
    _step_header("3 / 3", "Generate snapshot", skip_snapshot)
    if skip_snapshot:
        status["snapshot"] = "skipped"
    else:
        t0 = time.perf_counter()
        ok = run_snapshot(
            data_root=data_root,
            experiment=experiment,
            subject_id=subject_id,
            tau=tau,
            dt=dt,
            overlap=overlap,
            pre_days=pre_days,
            post_days=post_days,
            n_pokes_duration=n_pokes_duration,
            camera_fps=camera_fps,
        )
        status["snapshot"] = "ok" if ok else "failed"
        print(f"  -> {status['snapshot'].upper()}  ({time.perf_counter() - t0:.1f} s)\n")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print(f"{'=' * 65}")
    print("  Pipeline complete")
    for step, result in status.items():
        icon = {"ok": "[OK]", "skipped": "[--]", "failed": "[FAIL]"}[result]
        print(f"    {icon}  {step}")
    print(f"{'=' * 65}\n")

    return status


def _step_header(counter: str, name: str, skipped: bool) -> None:
    """Print a formatted step header.

    Parameters
    ----------
    counter:
        Step counter string, e.g. ``"1 / 3"``.
    name:
        Human-readable step name.
    skipped:
        When ``True``, appends ``"[SKIPPED]"`` to the header.
    """
    tag = "  [SKIPPED]" if skipped else ""
    print(f"--- Step {counter}: {name}{tag} {'-' * max(1, 45 - len(name))}--")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the full processing pipeline.

    Parameters
    ----------
    argv:
        Argument list.  ``None`` reads from ``sys.argv[1:]``.

    Returns
    -------
    argparse.Namespace
        Parsed namespace.  All arguments default to the corresponding
        ``DELPHI_*`` setting so that a ``.env`` file can serve as persistent
        configuration.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Full Delphi processing pipeline: build-dataset -> create-clips -> snapshot.\n\n"
            "All defaults are read from DELPHI_* environment variables / .env file.\n"
            "A CLI flag always overrides the .env value."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ---- required / path ----
    parser.add_argument(
        "--data-root",
        default=_s.data_root,
        required=_s.data_root is None,
        help=(
            "Run-level session directory.  "
            f"[env: DELPHI_DATA_ROOT, current: {_s.data_root!r}]"
        ),
    )
    parser.add_argument(
        "--experiment",
        default=_s.experiment,
        choices=list(_REGISTRY.keys()),
        required=_s.experiment is None,
        metavar="EXPERIMENT",
        help=(
            f"Experiment type.  Choices: {', '.join(_REGISTRY)}.  "
            f"[env: DELPHI_EXPERIMENT, current: {_s.experiment!r}]"
        ),
    )

    # ---- ingestion ----
    parser.add_argument(
        "--firmware", default=_s.firmware,
        help=f"Firmware version string.  [env: DELPHI_FIRMWARE, current: {_s.firmware!r}]",
    )
    parser.add_argument(
        "--no-consolidate", action="store_true", default=_s.no_consolidate,
        help=f"Disable run-directory consolidation.  [env: DELPHI_NO_CONSOLIDATE]",
    )

    # ---- snapshot ----
    parser.add_argument(
        "--subject-id", default=None,
        help="Subject identifier (default: inferred from path hierarchy).",
    )
    parser.add_argument(
        "--tau", type=float, default=_s.tau,
        help=f"Decay time constant, seconds.  [env: DELPHI_TAU, current: {_s.tau}]",
    )
    parser.add_argument(
        "--dt", type=float, default=_s.dt,
        help=f"Rate-estimator window, seconds.  [env: DELPHI_DT, current: {_s.dt}]",
    )
    parser.add_argument(
        "--overlap", type=float, default=_s.overlap,
        help=f"Window overlap [0,1).  [env: DELPHI_OVERLAP, current: {_s.overlap}]",
    )
    parser.add_argument(
        "--pre-days", type=int, default=_s.pre_days,
        help=f"Baseline days before odor change.  [env: DELPHI_PRE_DAYS, current: {_s.pre_days}]",
    )
    parser.add_argument(
        "--post-days", type=int, default=_s.post_days,
        help=f"Post-change days.  [env: DELPHI_POST_DAYS, current: {_s.post_days}]",
    )
    parser.add_argument(
        "--n-pokes-duration", type=int, default=_s.n_pokes_duration,
        dest="n_pokes_duration",
        help=f"Pokes/day for duration comparison.  [env: DELPHI_N_POKES_DURATION, current: {_s.n_pokes_duration}]",
    )
    parser.add_argument(
        "--camera-fps", type=float, default=_s.camera_fps, dest="camera_fps",
        help=f"Camera FPS override for QC.  [env: DELPHI_CAMERA_FPS, current: {_s.camera_fps}]",
    )

    # ---- clip extraction ----
    parser.add_argument(
        "--workers", type=int, default=_s.workers,
        help=f"Parallel clip-export threads.  [env: DELPHI_WORKERS, current: {_s.workers}]",
    )
    parser.add_argument(
        "--no-delete", action="store_true", default=_s.no_delete,
        help=f"Keep source PortCamera files.  [env: DELPHI_NO_DELETE]",
    )

    # ---- step toggles ----
    parser.add_argument(
        "--skip-build", action="store_true", default=False,
        help="Skip the build-dataset step.",
    )
    parser.add_argument(
        "--skip-clips", action="store_true", default=False,
        help="Skip the create-clips step.",
    )
    parser.add_argument(
        "--skip-snapshot", action="store_true", default=False,
        help="Skip the snapshot step.",
    )

    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    result = run_pipeline(
        data_root=args.data_root,
        experiment=args.experiment,
        firmware=args.firmware,
        consolidate_runs=not args.no_consolidate,
        subject_id=args.subject_id,
        tau=args.tau,
        dt=args.dt,
        overlap=args.overlap,
        pre_days=args.pre_days,
        post_days=args.post_days,
        n_pokes_duration=args.n_pokes_duration,
        camera_fps=args.camera_fps,
        n_workers=args.workers,
        no_delete=args.no_delete,
        skip_build=args.skip_build,
        skip_clips=args.skip_clips,
        skip_snapshot=args.skip_snapshot,
    )
    # Exit 1 if any step failed
    sys.exit(1 if "failed" in result.values() else 0)
