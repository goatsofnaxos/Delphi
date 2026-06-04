"""Command-line interface for the ``delphi-data`` package.

Installed as the ``delphi-data`` entry point (see ``pyproject.toml``)::

    delphi-data <command> [options]

Commands
--------
pipeline
    Run the full three-step pipeline: build-dataset -> create-clips -> snapshot.

build-dataset
    Ingest raw hardware data from a session directory and write
    ``behavior/delphi_dataset.csv``.

snapshot
    Generate a visual snapshot (poke-rate, IPI, duration, daily counts)
    from an existing ``delphi_dataset.csv``.

consolidate
    Merge multiple run sub-directories inside a session into the
    earliest run directory.

create-clips
    Extract short video clips centred on poke events.

Run ``delphi-data <command> --help`` for per-command options.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import textwrap


# ---------------------------------------------------------------------------
# Sub-command handlers
# ---------------------------------------------------------------------------


def _cmd_pipeline(args: argparse.Namespace) -> None:
    """Run the full three-step processing pipeline.

    Parameters
    ----------
    args:
        Parsed namespace from the ``pipeline`` sub-parser.
    """
    from full_processing_pipeline import run_pipeline  # lazy import

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
    import sys
    sys.exit(1 if "failed" in result.values() else 0)


def _cmd_build_dataset(args: argparse.Namespace) -> None:
    """Run the build-dataset pipeline.

    Parameters
    ----------
    args:
        Parsed namespace from the ``build-dataset`` sub-parser.
    """
    from scripts.build_dataset import build_dataset  # lazy import

    build_dataset(
        session_dir=pathlib.Path(args.data_root),
        firmware=args.firmware,
        consolidate_runs=not args.no_consolidate,
    )


def _cmd_snapshot(args: argparse.Namespace) -> None:
    """Run the experiment-specific snapshot pipeline.

    Imports the snapshot module registered under ``args.experiment`` and calls
    its ``run_snapshot`` function.

    Parameters
    ----------
    args:
        Parsed namespace from the ``snapshot`` sub-parser.
    """
    import importlib
    from snapshots import REGISTRY  # lazy import

    module_path = REGISTRY[args.experiment]
    mod = importlib.import_module(module_path)

    mod.run_snapshot(
        data_root=args.data_root,
        subject_id=args.subject_id,
        tau=args.tau,
        dt=args.dt,
        overlap=args.overlap,
        pre_days=args.pre_days,
        post_days=args.post_days,
        n_pokes_duration=args.n_pokes_duration,
        camera_fps=getattr(args, "camera_fps", None),
    )


def _cmd_consolidate(args: argparse.Namespace) -> None:
    """Run the session consolidation pipeline.

    Parameters
    ----------
    args:
        Parsed namespace from the ``consolidate`` sub-parser.
    """
    from delphi_data.curation import consolidate_session_runs

    consolidate_session_runs(args.data_root)
    print("Consolidation complete.")


def _cmd_create_clips(args: argparse.Namespace) -> None:
    """Run the poke-clip extraction pipeline.

    Parameters
    ----------
    args:
        Parsed namespace from the ``create-clips`` sub-parser.
    """
    from delphi_data.video_processing import main as vp_main  # lazy import

    # Reconstruct argv so video_processing.main can parse it with its own parser.
    argv = [args.root]
    if args.output:
        argv += ["--output", args.output]
    if args.workers != 4:
        argv += ["--workers", str(args.workers)]
    if args.no_delete:
        argv.append("--no-delete")
    if args.no_delete_corrupted:
        argv.append("--no-delete-corrupted")
    vp_main(argv)


# ---------------------------------------------------------------------------
# Argument parser construction
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Argument defaults are populated from the :mod:`delphi_data.settings`
    module, which reads ``DELPHI_*`` variables from the environment and any
    ``.env`` file found in the directory tree.  An explicit CLI flag always
    overrides the ``.env`` value.

    Returns
    -------
    argparse.ArgumentParser
        Configured parser with all sub-commands registered.
    """
    from delphi_data.settings import settings as _s

    parser = argparse.ArgumentParser(
        prog="delphi-data",
        description=textwrap.dedent("""\
            Delphi data ingestion, analysis, and visualization toolkit.

            Run 'delphi-data <command> --help' for per-command options.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version="delphi-data 0.1.0"
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers.required = True

    # ------------------------------------------------------------------
    # pipeline
    # ------------------------------------------------------------------
    p_pipe = subparsers.add_parser(
        "pipeline",
        help="Run the full pipeline: build-dataset -> create-clips -> snapshot.",
        description=textwrap.dedent(f"""\
            Full three-step processing pipeline for one Delphi session.

            Steps (each can be skipped independently):
              1. build-dataset  -- ingest Harp streams -> behavior/delphi_dataset.csv
              2. create-clips   -- extract poke-triggered MP4 clips
              3. snapshot       -- generate behavioral figures + QC plots

            All defaults are read from DELPHI_* environment / .env file.
            A CLI flag always overrides the .env value.

            Active .env defaults:
              DELPHI_DATA_ROOT    = {_s.data_root!r}
              DELPHI_EXPERIMENT   = {_s.experiment!r}
              DELPHI_FIRMWARE     = {_s.firmware!r}
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # -- path / experiment --
    p_pipe.add_argument(
        "--data-root", default=_s.data_root, required=_s.data_root is None,
        help=f"Run-level session directory.  [env: DELPHI_DATA_ROOT, current: {_s.data_root!r}]",
    )
    p_pipe.add_argument(
        "--experiment",
        choices=list(_REGISTRY.keys()) if _REGISTRY else ["bonhoeffer"],
        default=_s.experiment, required=_s.experiment is None,
        metavar="EXPERIMENT",
        help=(
            f"Experiment type.  Choices: {', '.join(_REGISTRY or ['bonhoeffer'])}.  "
            f"[env: DELPHI_EXPERIMENT, current: {_s.experiment!r}]"
        ),
    )
    # -- ingestion --
    p_pipe.add_argument(
        "--firmware", default=_s.firmware,
        help=f"Firmware version string.  [env: DELPHI_FIRMWARE, current: {_s.firmware!r}]",
    )
    p_pipe.add_argument(
        "--no-consolidate", action="store_true", default=_s.no_consolidate,
        help="Disable run-directory consolidation.  [env: DELPHI_NO_CONSOLIDATE]",
    )
    # -- snapshot --
    p_pipe.add_argument("--subject-id", default=None,
                        help="Subject identifier (default: inferred from path).")
    p_pipe.add_argument("--tau", type=float, default=_s.tau,
                        help=f"Decay time constant, seconds.  [env: DELPHI_TAU, current: {_s.tau}]")
    p_pipe.add_argument("--dt", type=float, default=_s.dt,
                        help=f"Rate-estimator window, seconds.  [env: DELPHI_DT, current: {_s.dt}]")
    p_pipe.add_argument("--overlap", type=float, default=_s.overlap,
                        help=f"Window overlap [0,1).  [env: DELPHI_OVERLAP, current: {_s.overlap}]")
    p_pipe.add_argument("--pre-days", type=int, default=_s.pre_days,
                        help=f"Baseline days.  [env: DELPHI_PRE_DAYS, current: {_s.pre_days}]")
    p_pipe.add_argument("--post-days", type=int, default=_s.post_days,
                        help=f"Post-change days.  [env: DELPHI_POST_DAYS, current: {_s.post_days}]")
    p_pipe.add_argument("--n-pokes-duration", type=int, default=_s.n_pokes_duration,
                        dest="n_pokes_duration",
                        help=f"Pokes/day for duration comparison.  [env: DELPHI_N_POKES_DURATION, current: {_s.n_pokes_duration}]")
    p_pipe.add_argument("--camera-fps", type=float, default=_s.camera_fps, dest="camera_fps",
                        help=f"Camera FPS override.  [env: DELPHI_CAMERA_FPS, current: {_s.camera_fps}]")
    # -- clips --
    p_pipe.add_argument("--workers", type=int, default=_s.workers,
                        help=f"Parallel clip-export threads.  [env: DELPHI_WORKERS, current: {_s.workers}]")
    p_pipe.add_argument("--no-delete", action="store_true", default=_s.no_delete,
                        help="Keep source PortCamera files.  [env: DELPHI_NO_DELETE]")
    # -- step toggles --
    p_pipe.add_argument("--skip-build", action="store_true", default=False,
                        help="Skip the build-dataset step.")
    p_pipe.add_argument("--skip-clips", action="store_true", default=False,
                        help="Skip the create-clips step.")
    p_pipe.add_argument("--skip-snapshot", action="store_true", default=False,
                        help="Skip the snapshot step.")
    p_pipe.set_defaults(func=_cmd_pipeline)

    # ------------------------------------------------------------------
    # build-dataset
    # ------------------------------------------------------------------
    p_build = subparsers.add_parser(
        "build-dataset",
        help="Ingest raw session data and write behavior/delphi_dataset.csv.",
        description=textwrap.dedent("""\
            Ingest raw hardware data from a session directory.

            Reads Harp register streams, parses the poke-state machine, and
            writes a per-poke event CSV to <SESSION_DIR>/behavior/delphi_dataset.csv.

            Defaults can be set via DELPHI_DATA_ROOT, DELPHI_FIRMWARE, and
            DELPHI_NO_CONSOLIDATE in a .env file or shell environment.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_build.add_argument(
        "--data-root",
        default=_s.data_root,
        required=_s.data_root is None,
        help=(
            "Path to the run-level session directory.  "
            f"[env: DELPHI_DATA_ROOT, current: {_s.data_root!r}]"
        ),
    )
    p_build.add_argument(
        "--firmware",
        default=_s.firmware,
        required=_s.firmware is None,
        help=(
            'Firmware version string, e.g. "0.1.0".  '
            f"[env: DELPHI_FIRMWARE, current: {_s.firmware!r}]"
        ),
    )
    p_build.add_argument(
        "--no-consolidate",
        action="store_true",
        default=_s.no_consolidate,
        help=(
            "Disable automatic consolidation of multiple run sub-directories.  "
            f"[env: DELPHI_NO_CONSOLIDATE, current: {_s.no_consolidate}]"
        ),
    )
    p_build.set_defaults(func=_cmd_build_dataset)

    # ------------------------------------------------------------------
    # snapshot
    # ------------------------------------------------------------------
    try:
        from snapshots import REGISTRY as _REGISTRY
        _experiment_choices = list(_REGISTRY.keys())
    except ImportError:
        _experiment_choices = ["bonhoeffer"]

    p_snap = subparsers.add_parser(
        "snapshot",
        help="Generate an experiment-specific visualization snapshot.",
        description=textwrap.dedent(f"""\
            Generate a behavioral snapshot for a single Delphi session.

            Expects <DATA_ROOT>/behavior/delphi_dataset.csv and writes all
            figures to <DATA_ROOT>/behavior/results/.

            Defaults can be set in a .env file using DELPHI_* variables.
            A CLI flag always overrides the .env value.

            Active .env defaults:
              DELPHI_DATA_ROOT          = {_s.data_root!r}
              DELPHI_TAU                = {_s.tau}
              DELPHI_DT                 = {_s.dt}
              DELPHI_OVERLAP            = {_s.overlap}
              DELPHI_PRE_DAYS           = {_s.pre_days}
              DELPHI_POST_DAYS          = {_s.post_days}
              DELPHI_N_POKES_DURATION   = {_s.n_pokes_duration}
              DELPHI_CAMERA_FPS         = {_s.camera_fps}

            Available experiments:
              bonhoeffer  – Bonhoeffer olfactory learning paradigm
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_snap.add_argument(
        "--experiment",
        choices=_experiment_choices,
        default=_s.experiment,
        required=_s.experiment is None,
        metavar="EXPERIMENT",
        help=(
            f"Experiment type.  Choices: {', '.join(_experiment_choices)}.  "
            f"[env: DELPHI_EXPERIMENT, current: {_s.experiment!r}]"
        ),
    )
    p_snap.add_argument(
        "--data-root",
        default=_s.data_root,
        required=_s.data_root is None,
        help=(
            "Run-level session directory (contains behavior/delphi_dataset.csv).  "
            f"[env: DELPHI_DATA_ROOT, current: {_s.data_root!r}]"
        ),
    )
    p_snap.add_argument(
        "--subject-id", default=None,
        help="Subject identifier (default: inferred from path hierarchy).",
    )
    p_snap.add_argument(
        "--tau", type=float, default=_s.tau,
        help=f"Exponential decay time constant, seconds.  [env: DELPHI_TAU, current: {_s.tau}]",
    )
    p_snap.add_argument(
        "--dt", type=float, default=_s.dt,
        help=f"Rate-estimator window length, seconds.  [env: DELPHI_DT, current: {_s.dt}]",
    )
    p_snap.add_argument(
        "--overlap", type=float, default=_s.overlap,
        help=f"Fractional window overlap [0, 1).  [env: DELPHI_OVERLAP, current: {_s.overlap}]",
    )
    p_snap.add_argument(
        "--pre-days", type=int, default=_s.pre_days,
        help=f"Baseline days before each odor change.  [env: DELPHI_PRE_DAYS, current: {_s.pre_days}]",
    )
    p_snap.add_argument(
        "--post-days", type=int, default=_s.post_days,
        help=f"Post-change days for windowed analyses.  [env: DELPHI_POST_DAYS, current: {_s.post_days}]",
    )
    p_snap.add_argument(
        "--n-pokes-duration", type=int, default=_s.n_pokes_duration,
        dest="n_pokes_duration",
        help=(
            f"Pokes per day for the duration-comparison figure.  "
            f"[env: DELPHI_N_POKES_DURATION, current: {_s.n_pokes_duration}]"
        ),
    )
    p_snap.add_argument(
        "--camera-fps", type=float, default=_s.camera_fps,
        dest="camera_fps",
        help=(
            "Override camera frame rate for QC plots (Hz).  Leave unset for "
            "auto-detect (Harp register -> rig config -> HardwareSettings -> default).  "
            f"[env: DELPHI_CAMERA_FPS, current: {_s.camera_fps}]"
        ),
    )
    p_snap.set_defaults(func=_cmd_snapshot)

    # ------------------------------------------------------------------
    # consolidate
    # ------------------------------------------------------------------
    p_cons = subparsers.add_parser(
        "consolidate",
        help="Merge multiple run sub-directories into the earliest one.",
        description=textwrap.dedent("""\
            Consolidate multiple timestamp-named run sub-directories inside
            SESSION_DIR into the earliest run directory.

            Uses fast same-filesystem renames where possible; cross-filesystem
            moves are verified with SHA-256 checksums.

            Default data root can be set via DELPHI_DATA_ROOT in .env.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_cons.add_argument(
        "--data-root",
        default=_s.data_root,
        required=_s.data_root is None,
        help=(
            "Path to the session directory containing run sub-directories.  "
            f"[env: DELPHI_DATA_ROOT, current: {_s.data_root!r}]"
        ),
    )
    p_cons.set_defaults(func=_cmd_consolidate)

    # ------------------------------------------------------------------
    # create-clips
    # ------------------------------------------------------------------
    p_clips = subparsers.add_parser(
        "create-clips",
        help="Extract poke-triggered video clips from session data (requires [video]).",
        description=textwrap.dedent(f"""\
            Extract poke-triggered MP4 clips from Delphi session directories.

            Searches ROOT for sessions (containing behavior/, behavior-videos/,
            metadata/) and exports one clip per poke event per PortCamera chunk.

            Requires: pip install delphi-data[video]
            System:   ffmpeg and ffprobe must be on PATH.

            Active .env defaults:
              DELPHI_WORKERS   = {_s.workers}
              DELPHI_NO_DELETE = {_s.no_delete}
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_clips.add_argument(
        "root",
        help="Root directory to search for sessions.",
    )
    p_clips.add_argument(
        "--output", default=None,
        help=(
            "Output base directory.  Clips go to <output>/<session>/behavior-videos/PokeClips/.  "
            "Defaults to writing inside each session directory."
        ),
    )
    p_clips.add_argument(
        "--workers", type=int, default=_s.workers,
        help=f"Parallel clip-export threads per chunk.  [env: DELPHI_WORKERS, current: {_s.workers}]",
    )
    p_clips.add_argument(
        "--no-delete", action="store_true", default=_s.no_delete,
        help=(
            "Do not delete PortCamera source files after exporting clips.  "
            f"[env: DELPHI_NO_DELETE, current: {_s.no_delete}]"
        ),
    )
    p_clips.add_argument(
        "--no-delete-corrupted", action="store_true",
        help="Keep source chunk even when some clips failed to export.",
    )
    p_clips.set_defaults(func=_cmd_create_clips)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv=None) -> None:
    """Entry point for the ``delphi-data`` CLI.

    Parses arguments and dispatches to the appropriate sub-command handler.
    Exits with code 1 on error.

    Parameters
    ----------
    argv:
        Argument list.  ``None`` reads from ``sys.argv[1:]``.
    """
    # Make scripts/ importable when CLI is invoked as an installed entry point.
    _pkg_root = pathlib.Path(__file__).resolve().parent.parent
    _scripts_dir = _pkg_root / "scripts"
    for _p in [str(_pkg_root), str(_scripts_dir)]:
        if _p not in sys.path:
            sys.path.insert(0, _p)

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
