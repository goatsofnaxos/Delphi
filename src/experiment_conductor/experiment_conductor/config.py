"""Configuration for the experiment conductor.

All settings are loaded from a ``.env`` file (and/or shell environment) and
may be overridden on the command line.  CLI flags always win over env-file
values.

Environment variable reference
-------------------------------
See ``.env.example`` for the full list with descriptions.  The key groups are:

* ``CONDUCTOR_WATCH_PATHS``     — comma-separated paths to monitor
* ``CONDUCTOR_EXPERIMENT_TYPE`` — ``delphi`` | ``pirouette`` | ``delphi_pirouette``
* ``CONDUCTOR_PROTOCOL_ID``, ``INSTRUMENT_ID``, etc.  — metadata fields
* ``CONDUCTOR_ENABLE_PIPELINE``, ``CONDUCTOR_ENABLE_METADATA``, ``CONDUCTOR_ENABLE_UPLOAD``
* ``CONDUCTOR_DRY_RUN``         — submit no real upload requests
* ``CONDUCTOR_STATE_FILE``      — path for persisting session state across restarts
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).lower() in {"1", "true", "yes", "y"}


def _list(v: str | None) -> List[str]:
    if not v:
        return []
    return [s.strip() for s in v.split(",") if s.strip()]


def _int(v: str | None, default: int) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _float(v: str | None, default: float) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class ConductorConfig:
    """Merged configuration for the experiment conductor.

    All attributes correspond to an environment variable with the prefix
    ``CONDUCTOR_`` (or the legacy name documented in ``.env.example``).

    Parameters
    ----------
    watch_paths : list of Path
        Root directories to scan for new acquisition sessions.  Each watch
        path is expected to contain ``<subject_id>/<session_timestamp>/``
        sub-directories.
    experiment_type : str
        One of ``delphi``, ``pirouette``, ``delphi_pirouette``.
    acquisition_type : str
        Acquisition type string passed to the AIND metadata schema
        (e.g. ``"ChronicRecording"``).
    protocol_id : str
        AIND protocol ID string.
    instrument_id : str
        Rig/instrument identifier.
    experiment_room : str
        Physical room identifier.
    delphi_computer_id : str
        Hostname of the Delphi acquisition computer.
    surgeons : list of str
        Surgeon names for procedures metadata.
    experimenters : list of str
        Experimenter names for acquisition metadata.
    surgery_notes_base : Path or None
        Base directory for surgery notes DOCX files.  Subject subfolder is
        appended automatically: ``surgery_notes_base / subject_id / ...``.
    delphi_experiment : str
        Experiment name for the delphi-data snapshot step (e.g. ``bonhoeffer``).
    delphi_firmware : str
        Firmware version string for delphi-data ingestion (e.g. ``0.1.0``).
    enable_pipeline : bool
        If *False*, the delphi-data build/snapshot step is skipped.
    pipeline_cadence_minutes : int
        How often (in minutes) to run a full processing cycle per session.
    pipeline_skip_build : bool
        Pass ``--skip-build`` to ``delphi-data pipeline``.
    pipeline_skip_clips : bool
        Pass ``--skip-clips`` to ``delphi-data pipeline`` (default True).
    pipeline_skip_snapshot : bool
        Pass ``--skip-snapshot`` to ``delphi-data pipeline``.
    enable_metadata : bool
        If *False*, AIND metadata generation is skipped.
    enable_noise_floor : bool
        If *True*, estimate the ephys noise floor each cycle.
    noise_floor_n_seconds : float
        Duration (seconds) of data to read for noise-floor estimation.
    noise_floor_max_channels : int or None
        Limit the noise-floor estimate to the first N channels.
    enable_upload : bool
        If *False*, the S3 upload step is skipped.
    s3_bucket : str
        S3 bucket for upload (default ``"aind-open-data"``).
    contact_email : str
        Email address for upload job notifications.
    project_name : str
        AIND project name for the upload job.
    upload_batch_size : int
        Number of chunks submitted per upload batch (default 2).
    num_last_chunks_to_ignore : int
        Most-recent chunks to skip when uploading (avoids in-progress data).
    dry_run : bool
        If *True*, print upload requests without submitting them.
    delete_after_upload : bool
        If *True*, remove large local files after confirming S3 upload.
    keep_local_patterns : list of str
        Glob patterns (relative to the run directory) that are always kept
        locally even when ``delete_after_upload`` is enabled.
    poll_interval_s : float
        How often (in seconds) the watcher scans for new session directories.
    min_session_age_minutes : float
        A newly discovered session is not processed until it is at least this
        many minutes old (gives the acquisition computer time to write data).
    max_consecutive_errors : int
        A session is marked ``ERROR`` after this many consecutive failures.
    error_backoff_minutes : float
        After an error the session is skipped for this many minutes before
        being retried.
    state_file : Path or None
        If set, session states are persisted to this JSON file so work
        survives conductor restarts.
    chunk_camera_folder : str
        Path relative to the run directory used to count chunk directories
        when gating the initial upload start job.
    """

    # ── Watch paths ───────────────────────────────────────────────────────────
    watch_paths: List[Path]

    # ── Session identity ──────────────────────────────────────────────────────
    experiment_type: str
    acquisition_type: str
    protocol_id: str
    instrument_id: str
    experiment_room: str
    delphi_computer_id: str
    surgeons: List[str]
    experimenters: List[str]
    surgery_notes_base: Optional[Path]

    # ── Pipeline ──────────────────────────────────────────────────────────────
    delphi_experiment: str
    delphi_firmware: str
    enable_pipeline: bool
    pipeline_cadence_minutes: int
    pipeline_skip_build: bool
    pipeline_skip_clips: bool
    pipeline_skip_snapshot: bool

    # ── Metadata ──────────────────────────────────────────────────────────────
    enable_metadata: bool

    # ── Noise floor ───────────────────────────────────────────────────────────
    enable_noise_floor: bool
    noise_floor_n_seconds: float
    noise_floor_max_channels: Optional[int]

    # ── Upload ────────────────────────────────────────────────────────────────
    enable_upload: bool
    s3_bucket: str
    contact_email: str
    project_name: str
    upload_batch_size: int
    num_last_chunks_to_ignore: int
    dry_run: bool
    delete_after_upload: bool
    keep_local_patterns: List[str]

    # ── Polling / error handling ───────────────────────────────────────────────
    poll_interval_s: float
    min_session_age_minutes: float
    max_consecutive_errors: int
    error_backoff_minutes: float

    # ── State persistence ─────────────────────────────────────────────────────
    state_file: Optional[Path]

    # ── Misc ──────────────────────────────────────────────────────────────────
    chunk_camera_folder: str


# ── Builder ───────────────────────────────────────────────────────────────────

def _parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="conductor",
        description=(
            "Experiment Conductor — watches a shared network drive for new "
            "acquisition sessions and orchestrates metadata generation, "
            "delphi-data processing, noise-floor estimation, and S3 upload."
        ),
    )
    p.add_argument(
        "--env-file", default=".env", metavar="PATH",
        help="Path to .env file (default: .env in cwd).",
    )
    p.add_argument(
        "--watch-paths", default=None, metavar="PATH[,PATH…]",
        help=(
            "Comma-separated list of root directories to monitor for new "
            "sessions.  Overrides CONDUCTOR_WATCH_PATHS."
        ),
    )
    p.add_argument(
        "--add-session", action="append", dest="extra_sessions",
        metavar="PATH",
        help=(
            "Immediately register a specific session directory for "
            "processing.  May be repeated.  The parent directory's name is "
            "used as the subject ID."
        ),
    )
    p.add_argument("--experiment-type", default=None)
    p.add_argument("--protocol-id", default=None)
    p.add_argument("--instrument-id", default=None)
    p.add_argument("--experiment-room", default=None)
    p.add_argument("--delphi-computer-id", default=None)
    p.add_argument("--surgeons", default=None, metavar="NAME[,NAME…]")
    p.add_argument("--experimenters", default=None, metavar="NAME[,NAME…]")
    p.add_argument("--surgery-notes-base", default=None, type=Path)
    p.add_argument("--delphi-experiment", default=None)
    p.add_argument("--delphi-firmware", default=None)
    p.add_argument(
        "--pipeline-cadence-minutes", default=None, type=int, metavar="N",
    )
    p.add_argument("--s3-bucket", default=None)
    p.add_argument("--contact-email", default=None)
    p.add_argument("--project-name", default=None)
    p.add_argument(
        "--upload-batch-size", default=None, type=int, metavar="N",
    )
    p.add_argument(
        "--dry-run", action="store_true", default=None,
        help="Print upload requests without submitting them.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    p.add_argument(
        "--state-file", default=None, type=Path, metavar="PATH",
        help="Persist session states to this JSON file across restarts.",
    )
    return p.parse_args()


def build_config() -> ConductorConfig:
    """Load ``.env`` then overlay CLI arguments to produce a :class:`ConductorConfig`.

    Priority (highest to lowest): CLI flags > environment variables > ``.env``
    file > hard-coded defaults.

    Returns
    -------
    ConductorConfig

    Raises
    ------
    ValueError
        If ``CONDUCTOR_WATCH_PATHS`` is not set and ``--watch-paths`` is not
        provided (at least one watch path is required).
    """
    args = _parse_cli()
    load_dotenv(args.env_file, override=False)

    def _g(env_key: str, cli_val=None, default=None):
        return cli_val if cli_val is not None else os.getenv(env_key, default)

    # ── Watch paths ────────────────────────────────────────────────────────────
    watch_raw = _g("CONDUCTOR_WATCH_PATHS", args.watch_paths, "")
    watch_paths = [Path(p) for p in _list(watch_raw)] if watch_raw else []

    # ── Surgery notes base ─────────────────────────────────────────────────────
    surgery_raw = _g(
        "CONDUCTOR_SURGERY_NOTES_BASE",
        args.surgery_notes_base,
        r"\\allen\aind\scratch\chronos\surgeryNotes",
    )
    surgery_notes_base = Path(surgery_raw) if surgery_raw else None

    # ── State file ─────────────────────────────────────────────────────────────
    state_raw = _g("CONDUCTOR_STATE_FILE", args.state_file)
    state_file = Path(state_raw) if state_raw else None

    # ── Noise floor max channels (0 means None) ────────────────────────────────
    nf_max_raw = _g("CONDUCTOR_NOISE_FLOOR_MAX_CHANNELS", None, "0")
    noise_floor_max_channels: Optional[int] = None
    try:
        nf_max = int(nf_max_raw)  # type: ignore[arg-type]
        if nf_max > 0:
            noise_floor_max_channels = nf_max
    except (TypeError, ValueError):
        pass

    return ConductorConfig(
        watch_paths=watch_paths,
        experiment_type=_g("CONDUCTOR_EXPERIMENT_TYPE", args.experiment_type, "delphi"),
        acquisition_type=_g("CONDUCTOR_ACQUISITION_TYPE", None, "ChronicRecording"),
        protocol_id=_g("CONDUCTOR_PROTOCOL_ID", args.protocol_id, ""),
        instrument_id=_g("CONDUCTOR_INSTRUMENT_ID", args.instrument_id, ""),
        experiment_room=_g("CONDUCTOR_EXPERIMENT_ROOM", args.experiment_room, ""),
        delphi_computer_id=_g("CONDUCTOR_DELPHI_COMPUTER_ID", args.delphi_computer_id, ""),
        surgeons=_list(_g("CONDUCTOR_SURGEONS", args.surgeons)),
        experimenters=_list(_g("CONDUCTOR_EXPERIMENTERS", args.experimenters)),
        surgery_notes_base=surgery_notes_base,
        delphi_experiment=_g("CONDUCTOR_DELPHI_EXPERIMENT", args.delphi_experiment, "bonhoeffer"),
        delphi_firmware=_g("CONDUCTOR_DELPHI_FIRMWARE", args.delphi_firmware, "0.1.0"),
        enable_pipeline=_bool(_g("CONDUCTOR_ENABLE_PIPELINE", None, "true")),
        pipeline_cadence_minutes=_int(
            _g("CONDUCTOR_PIPELINE_CADENCE_MINUTES", args.pipeline_cadence_minutes), 60
        ),
        pipeline_skip_build=_bool(_g("CONDUCTOR_PIPELINE_SKIP_BUILD", None, "false")),
        pipeline_skip_clips=_bool(_g("CONDUCTOR_PIPELINE_SKIP_CLIPS", None, "true")),
        pipeline_skip_snapshot=_bool(_g("CONDUCTOR_PIPELINE_SKIP_SNAPSHOT", None, "false")),
        enable_metadata=_bool(_g("CONDUCTOR_ENABLE_METADATA", None, "true")),
        enable_noise_floor=_bool(_g("CONDUCTOR_ENABLE_NOISE_FLOOR", None, "false")),
        noise_floor_n_seconds=_float(_g("CONDUCTOR_NOISE_FLOOR_N_SECONDS", None, "10.0"), 10.0),
        noise_floor_max_channels=noise_floor_max_channels,
        enable_upload=_bool(_g("CONDUCTOR_ENABLE_UPLOAD", None, "true")),
        s3_bucket=_g("CONDUCTOR_S3_BUCKET", args.s3_bucket, "aind-open-data"),
        contact_email=_g("CONDUCTOR_CONTACT_EMAIL", args.contact_email, ""),
        project_name=_g("CONDUCTOR_PROJECT_NAME", args.project_name, ""),
        upload_batch_size=_int(
            _g("CONDUCTOR_UPLOAD_BATCH_SIZE", args.upload_batch_size), 2
        ),
        num_last_chunks_to_ignore=_int(
            _g("CONDUCTOR_NUM_LAST_CHUNKS_TO_IGNORE", None, "2"), 2
        ),
        dry_run=_bool(_g("CONDUCTOR_DRY_RUN", "true" if args.dry_run else None, "false")),
        delete_after_upload=_bool(_g("CONDUCTOR_DELETE_AFTER_UPLOAD", None, "false")),
        keep_local_patterns=_list(
            _g(
                "CONDUCTOR_KEEP_LOCAL_PATTERNS",
                None,
                "behavior/delphi_dataset.csv,behavior/DelphiController/**,"
                "behavior/results/**,behavior/metadata/**",
            )
        ),
        poll_interval_s=_float(_g("CONDUCTOR_POLL_INTERVAL_S", None, "60.0"), 60.0),
        min_session_age_minutes=_float(
            _g("CONDUCTOR_MIN_SESSION_AGE_MINUTES", None, "5.0"), 5.0
        ),
        max_consecutive_errors=_int(
            _g("CONDUCTOR_MAX_CONSECUTIVE_ERRORS", None, "5"), 5
        ),
        error_backoff_minutes=_float(
            _g("CONDUCTOR_ERROR_BACKOFF_MINUTES", None, "30.0"), 30.0
        ),
        state_file=state_file,
        chunk_camera_folder=_g(
            "CONDUCTOR_CHUNK_CAMERA_FOLDER", None, "behavior-videos/TopCamera"
        ),
    )
