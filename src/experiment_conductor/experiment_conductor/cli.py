"""Experiment Conductor command-line entry point.

Run with::

    conductor [options]

or::

    python -m experiment_conductor

Verbosity flags::

    conductor           # INFO  — phase transitions, success/failure summaries
    conductor -v        # VERBOSE — step details, file counts, offsets
    conductor -vv       # DEBUG  — per-file operations, subprocess args, raw values

Use ``conductor --help`` for the full option reference.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    """Start the experiment conductor.

    Loads configuration (from ``.env`` and CLI flags), configures logging,
    creates a :class:`~experiment_conductor.session_manager.SessionManager`,
    registers any sessions passed via ``--add-session``, and enters the main
    polling loop.
    """
    from .config import build_config
    from .logging_config import VERBOSE, setup_logging
    from .session_manager import SessionManager

    cfg = build_config()
    setup_logging(cfg.verbosity)

    import logging
    log = logging.getLogger(__name__)

    _level_name = {0: "INFO", 1: "VERBOSE", 2: "DEBUG"}.get(
        min(cfg.verbosity, 2), "DEBUG"
    )

    log.info("=" * 60)
    log.info("  Experiment Conductor")
    log.info("=" * 60)
    log.info("  Experiment type   : %s", cfg.experiment_type)
    log.info("  Acquisition type  : %s", cfg.acquisition_type)
    log.info("  Watch paths       : %d configured", len(cfg.watch_paths))
    for p in cfg.watch_paths:
        log.info("    %s", p)
    log.info("  Poll interval     : %.0f s", cfg.poll_interval_s)
    log.info("  Min session age   : %.1f min", cfg.min_session_age_minutes)
    log.info("  Processing cadence: %d min", cfg.pipeline_cadence_minutes)
    log.info("  Verbosity         : %s (%d)", _level_name, cfg.verbosity)
    log.info("-" * 60)
    log.info(
        "  Pipeline  : %-3s  (build=%-4s  clips=%-4s  snapshot=%s)",
        "ON" if cfg.enable_pipeline else "OFF",
        "skip" if cfg.pipeline_skip_build else "on",
        "skip" if cfg.pipeline_skip_clips else "on",
        "skip" if cfg.pipeline_skip_snapshot else "on",
    )
    log.info("  Metadata  : %s", "ON" if cfg.enable_metadata else "OFF")
    log.info("  Noise floor: %s", "ON" if cfg.enable_noise_floor else "OFF")
    log.info(
        "  Upload    : %-3s  (bucket=%s  dry_run=%s  delete=%s)",
        "ON" if cfg.enable_upload else "OFF",
        cfg.s3_bucket,
        cfg.dry_run,
        cfg.delete_after_upload,
    )
    if cfg.state_file:
        log.info("  State file : %s", cfg.state_file)
    log.info("=" * 60)

    log.log(
        VERBOSE,
        "Batch size=%d  ignore last %d chunk(s)  cadence=%d min",
        cfg.upload_batch_size,
        cfg.num_last_chunks_to_ignore,
        cfg.pipeline_cadence_minutes,
    )

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
            subject_id = p.parent.name
            if manager.add_session(subject_id, p):
                log.info(
                    "Added session from CLI: subject=%s  path=%s", subject_id, p
                )

    if not cfg.watch_paths and not _known2.extra_sessions:
        log.error(
            "No watch paths or sessions configured. "
            "Set CONDUCTOR_WATCH_PATHS in .env or pass --add-session PATH."
        )
        sys.exit(1)

    manager.run()


if __name__ == "__main__":
    main()
