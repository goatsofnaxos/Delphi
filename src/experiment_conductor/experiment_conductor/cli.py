"""Experiment Conductor command-line entry point.

Run with::

    conductor [options]

or::

    python -m experiment_conductor

Use ``conductor --help`` for the full option reference.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def main() -> None:
    """Start the experiment conductor.

    Loads configuration (from ``.env`` and CLI flags), creates a
    :class:`~experiment_conductor.session_manager.SessionManager`, registers
    any sessions passed via ``--add-session``, and enters the main polling
    loop.
    """
    # Import here so the --help string reflects the argparse output before
    # any heavy imports happen.
    from .config import build_config
    from .session_manager import SessionManager

    cfg = build_config()

    # Configure logging (build_config already parsed --log-level)
    import argparse

    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("--log-level", default="INFO")
    _pre.add_argument("--env-file", default=".env")
    _known, _ = _pre.parse_known_args()

    logging.basicConfig(
        level=_known.log_level.upper(),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )

    log = logging.getLogger(__name__)

    log.info("=== Experiment Conductor ===")
    log.info("  Experiment type : %s", cfg.experiment_type)
    log.info("  Watch paths     : %d configured", len(cfg.watch_paths))
    for p in cfg.watch_paths:
        log.info("    %s", p)
    log.info("  Cadence         : %d min", cfg.pipeline_cadence_minutes)
    log.info("  S3 bucket       : %s", cfg.s3_bucket)
    log.info("  Dry run         : %s", cfg.dry_run)
    log.info(
        "  Pipeline        : %s  (build=%s, clips=%s, snapshot=%s)",
        "ON" if cfg.enable_pipeline else "OFF",
        "skip" if cfg.pipeline_skip_build else "on",
        "skip" if cfg.pipeline_skip_clips else "on",
        "skip" if cfg.pipeline_skip_snapshot else "on",
    )
    log.info("  Metadata        : %s", "ON" if cfg.enable_metadata else "OFF")
    log.info("  Noise floor     : %s", "ON" if cfg.enable_noise_floor else "OFF")
    log.info("  Upload          : %s", "ON" if cfg.enable_upload else "OFF")
    if cfg.state_file:
        log.info("  State file      : %s", cfg.state_file)

    manager = SessionManager(cfg)

    # Pre-register any sessions passed on the command line
    import argparse as _ap

    _pre2 = _ap.ArgumentParser(add_help=False)
    _pre2.add_argument("--add-session", action="append", dest="extra_sessions")
    _known2, _ = _pre2.parse_known_args()

    if _known2.extra_sessions:
        for path_str in _known2.extra_sessions:
            p = Path(path_str).resolve()
            if not p.is_dir():
                log.warning("--add-session: directory does not exist: %s", p)
                continue
            # Infer subject_id from the parent directory name
            subject_id = p.parent.name
            if manager.add_session(subject_id, p):
                log.info("Added session from CLI: %s  (subject=%s)", p, subject_id)

    if not cfg.watch_paths and not _known2.extra_sessions:
        log.error(
            "No watch paths or sessions configured. "
            "Set CONDUCTOR_WATCH_PATHS in .env or pass --add-session PATH."
        )
        sys.exit(1)

    manager.run()


if __name__ == "__main__":
    main()
