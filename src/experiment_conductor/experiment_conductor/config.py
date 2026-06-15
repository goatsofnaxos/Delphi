"""Configuration loading for experiment_conductor.

Merges .env file values with CLI overrides. CLI flags always win.

Data root resolution
---------------------
``data_root`` — the session directory used by the pipeline, metadata
generator, and uploader — is resolved in one of two ways:

1. **Direct** (``DATA_ROOT``): set the full path explicitly.
   Use this when the session directory already exists or is known up front.

2. **Server-relative** (``SERVER_ROOT`` + ``SUBJECT_ID`` + optional
   ``SESSION_DATETIME``): the conductor computes the path as
   ``SERVER_ROOT / SUBJECT_ID / SESSION_DATETIME`` after the launcher exits.
   ``SESSION_DATETIME`` is auto-detected (newest timestamp directory under
   ``SERVER_ROOT / SUBJECT_ID``) when not supplied explicitly.
   Use this for the standard workflow where data is robocopied from the
   acquisition computer to a local server (e.g.
   ``\\\\allen\\aind\\stage\\chronic``).

Exactly one of ``DATA_ROOT`` or ``SERVER_ROOT`` must be set.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).lower() in {"1", "true", "yes", "y"}


def _list(v: str | None) -> List[str]:
    if not v:
        return []
    return [s.strip() for s in v.split(",") if s.strip()]


@dataclass
class ConductorConfig:
    """Merged configuration for the experiment conductor.

    Parameters
    ----------
    experiment_type : str
        One of ``delphi``, ``pirouette``, ``delphi_pirouette``.
    experiment_config : str
        Launcher profile name resolved in ``experiment_configs/``.
    server_root : Optional[Path]
        Root of the local server where data is robocopied from the acquisition
        computer (e.g. ``\\\\allen\\aind\\stage\\chronic``).  Corresponds to
        ``remote_transfer_root_path`` in the hardware schema.  When set,
        ``data_root`` is computed as
        ``server_root / subject_id / session_datetime`` after the launcher
        exits.  Mutually exclusive with providing ``data_root`` directly.
    session_datetime : Optional[str]
        Session timestamp string in ``YYYY-MM-DDTHH-MM-SS`` format
        (e.g. ``2026-03-20T20-23-05``).  Required when ``server_root`` is set
        and auto-detection is not desired.  When ``None`` the conductor scans
        ``server_root / subject_id`` for the newest timestamp directory after
        the launcher exits.
    data_root : Optional[Path]
        Resolved run-level session directory on the server.  ``None`` at
        startup when using server-relative mode; populated by the conductor
        after ``launch_experiments`` returns.
    launcher_dir : Path
        Directory containing ``launcher.py``.
    surgery_notes_base : Optional[Path]
        Base directory for surgery notes; subject subfolder appended automatically.
    subject_id : str
        Mouse subject ID.
    protocol_id : str
        AIND protocol ID.
    instrument_id : str
        Instrument identifier.
    experiment_room : str
        Physical room identifier.
    delphi_computer_id : str
        Hostname of the Delphi acquisition computer.
    surgeons : List[str]
        Surgeon names.
    experimenters : List[str]
        Experimenter names.
    acquisition_type : str
        Acquisition type passed to metadata-generator.
    delphi_experiment : str
        Experiment name for delphi-data snapshot (e.g. ``bonhoeffer``).
    delphi_firmware : str
        Firmware version string for delphi-data ingestion.
    upload_batch_size : int
        Number of chunks submitted to the transfer service per batch (default 2).
        The transfer service processes each chunk independently; keep this small
        to avoid overwhelming the queue.
    pipeline_cadence_minutes : int
        How often (minutes) to run the processing + upload cycle.
        Ignored when ``schedule_minute_of_hour`` is set.
    schedule_minute_of_hour : Optional[int]
        When set (0–59), run each cycle at that minute past every hour
        (e.g. ``45`` → 10:45, 11:45, …).  Mutually exclusive with
        ``pipeline_cadence_minutes``; this takes priority when both are set.
    s3_bucket : str
        S3 bucket name.
    contact_email : str
        Contact email for upload job notifications.
    project_name : str
        AIND project name for the upload job.
    dry_run : bool
        If True, print upload requests without submitting.
    delete_after_upload : bool
        If True, delete large local files after confirmed S3 upload.
    keep_local_patterns : List[str]
        Glob patterns relative to ``data_root`` to always keep locally.
    hotkey_pipeline : str
        pynput key string to manually trigger a pipeline cycle.
    hotkey_upload_pause : str
        pynput key string to toggle upload pause/resume.
    hotkey_end_experiment : str
        pynput key string to signal experiment end.
    """

    experiment_type: str
    experiment_config: str
    server_root: Optional[Path]
    session_datetime: Optional[str]
    data_root: Optional[Path]          # None until resolved post-launch
    launcher_dir: Path
    surgery_notes_base: Optional[Path]
    subject_id: str
    protocol_id: str
    instrument_id: str
    experiment_room: str
    delphi_computer_id: str
    surgeons: List[str]
    experimenters: List[str]
    acquisition_type: str
    delphi_experiment: str
    delphi_firmware: str
    upload_batch_size: int
    pipeline_cadence_minutes: int
    schedule_minute_of_hour: Optional[int]
    s3_bucket: str
    contact_email: str
    project_name: str
    dry_run: bool
    delete_after_upload: bool
    keep_local_patterns: List[str]
    hotkey_pipeline: str
    hotkey_upload_pause: str
    hotkey_end_experiment: str


def _parse_cli() -> argparse.Namespace:
    """Build and parse the CLI argument parser."""
    p = argparse.ArgumentParser(
        description="Experiment Conductor — orchestrates launch, processing, metadata, and upload."
    )
    p.add_argument("--env-file", default=".env", help="Path to .env file (default: .env)")
    p.add_argument("--experiment-type", default=None)
    p.add_argument("--experiment-config", default=None)
    # Data root — two mutually exclusive modes
    p.add_argument(
        "--server-root", default=None, type=Path, metavar="PATH",
        help="Local server root where data is robocopied (e.g. \\\\allen\\aind\\stage\\chronic). "
             "Mutually exclusive with --data-root.",
    )
    p.add_argument(
        "--session-datetime", default=None, metavar="YYYY-MM-DDTHH-MM-SS",
        help="Session timestamp used with --server-root to build data_root. "
             "Auto-detected from newest directory when omitted.",
    )
    p.add_argument(
        "--data-root", default=None, type=Path, metavar="PATH",
        help="Full path to the session directory. Mutually exclusive with --server-root.",
    )
    p.add_argument("--launcher-dir", default=None, type=Path)
    p.add_argument("--surgery-notes-base", default=None, type=Path)
    p.add_argument("--subject-id", default=None)
    p.add_argument("--protocol-id", default=None)
    p.add_argument("--instrument-id", default=None)
    p.add_argument("--experiment-room", default=None)
    p.add_argument("--delphi-computer-id", default=None)
    p.add_argument("--surgeons", default=None, help="Comma-separated surgeon names")
    p.add_argument("--experimenters", default=None, help="Comma-separated experimenter names")
    p.add_argument("--acquisition-type", default=None)
    p.add_argument("--delphi-experiment", default=None)
    p.add_argument("--delphi-firmware", default=None)
    p.add_argument(
        "--upload-batch-size", default=None, type=int, metavar="N",
        help="Chunks per upload batch submitted to the transfer service (default 2).",
    )
    p.add_argument("--pipeline-cadence-minutes", default=None, type=int)
    p.add_argument(
        "--schedule-minute-of-hour",
        default=None, type=int, metavar="MINUTE",
        help="Run cycles at this minute past every hour (0-59). Overrides --pipeline-cadence-minutes.",
    )
    p.add_argument("--s3-bucket", default=None)
    p.add_argument("--contact-email", default=None)
    p.add_argument("--project-name", default=None)
    p.add_argument("--dry-run", action="store_true", default=None)
    p.add_argument("--delete-after-upload", action="store_true", default=None)
    p.add_argument("--keep-local-patterns", default=None)
    return p.parse_args()


def build_config() -> ConductorConfig:
    """Load .env then overlay CLI arguments to produce a ``ConductorConfig``.

    ``data_root`` is resolved as follows:

    - If ``DATA_ROOT`` / ``--data-root`` is provided, use it directly.
    - If ``SERVER_ROOT`` / ``--server-root`` is provided, set ``data_root``
      to ``None``; the conductor will resolve it after the launcher exits by
      computing ``server_root / subject_id / session_datetime`` (where
      ``session_datetime`` is auto-detected when not explicitly set).
    - If neither is provided, raise ``ValueError``.

    Returns
    -------
    ConductorConfig
        Fully merged and validated configuration.

    Raises
    ------
    ValueError
        If neither ``DATA_ROOT`` nor ``SERVER_ROOT`` is provided, or if
        ``SUBJECT_ID`` is missing.
    """
    args = _parse_cli()
    load_dotenv(args.env_file, override=False)

    def _g(env_key: str, cli_val=None, default=None):
        return cli_val if cli_val is not None else os.getenv(env_key, default)

    subject_id = _g("SUBJECT_ID", args.subject_id, "")
    if not subject_id:
        raise ValueError("SUBJECT_ID must be set (via .env or --subject-id).")

    # ── Data root resolution ──────────────────────────────────────────────────
    server_root_raw = _g("SERVER_ROOT", args.server_root)
    data_root_raw = _g("DATA_ROOT", args.data_root)
    session_datetime = _g("SESSION_DATETIME", args.session_datetime)

    if data_root_raw and server_root_raw:
        raise ValueError(
            "Set either DATA_ROOT or SERVER_ROOT, not both."
        )
    if not data_root_raw and not server_root_raw:
        raise ValueError(
            "Either DATA_ROOT (full session path) or SERVER_ROOT "
            "(e.g. \\\\allen\\aind\\stage\\chronic) must be set."
        )

    server_root = Path(server_root_raw) if server_root_raw else None
    # data_root is None in server-relative mode — conductor fills it in post-launch
    data_root = Path(data_root_raw) if data_root_raw else None

    surgery_notes_raw = _g("SURGERY_NOTES_BASE", args.surgery_notes_base)

    return ConductorConfig(
        experiment_type=_g("EXPERIMENT_TYPE", args.experiment_type, "delphi_pirouette"),
        experiment_config=_g("EXPERIMENT_CONFIG", args.experiment_config, "delphi_pirouette_experiment"),
        server_root=server_root,
        session_datetime=session_datetime or None,
        data_root=data_root,
        launcher_dir=Path(_g("LAUNCHER_DIR", args.launcher_dir,
                              str(Path(__file__).parents[3] / "launcher"))),
        surgery_notes_base=Path(surgery_notes_raw) if surgery_notes_raw else None,
        subject_id=subject_id,
        protocol_id=_g("PROTOCOL_ID", args.protocol_id, ""),
        instrument_id=_g("INSTRUMENT_ID", args.instrument_id, ""),
        experiment_room=_g("EXPERIMENT_ROOM", args.experiment_room, ""),
        delphi_computer_id=_g("DELPHI_COMPUTER_ID", args.delphi_computer_id, ""),
        surgeons=_list(_g("SURGEONS", args.surgeons)),
        experimenters=_list(_g("EXPERIMENTERS", args.experimenters)),
        acquisition_type=_g("ACQUISITION_TYPE", args.acquisition_type, ""),
        delphi_experiment=_g("DELPHI_EXPERIMENT", args.delphi_experiment, "bonhoeffer"),
        delphi_firmware=_g("DELPHI_FIRMWARE", args.delphi_firmware, "0.1.0"),
        upload_batch_size=int(_g("UPLOAD_BATCH_SIZE", args.upload_batch_size, 2)),
        pipeline_cadence_minutes=int(_g("PIPELINE_CADENCE_MINUTES", args.pipeline_cadence_minutes, 60)),
        schedule_minute_of_hour=int(v) if (v := _g("SCHEDULE_MINUTE_OF_HOUR", args.schedule_minute_of_hour)) is not None else None,
        s3_bucket=_g("S3_BUCKET", args.s3_bucket, "aind-open-data"),
        contact_email=_g("CONTACT_EMAIL", args.contact_email, ""),
        project_name=_g("PROJECT_NAME", args.project_name, ""),
        dry_run=_bool(_g("DRY_RUN", "true" if args.dry_run else None, "false")),
        delete_after_upload=_bool(_g("DELETE_AFTER_UPLOAD",
                                     "true" if args.delete_after_upload else None, "false")),
        keep_local_patterns=_list(_g("KEEP_LOCAL_PATTERNS", args.keep_local_patterns,
                                     "behavior/delphi_dataset.csv,behavior/DelphiController/**,"
                                     "behavior/results/**,metadata/**")),
        hotkey_pipeline=os.getenv("HOTKEY_PIPELINE", "<ctrl>+<shift>+p"),
        hotkey_upload_pause=os.getenv("HOTKEY_UPLOAD_PAUSE", "<ctrl>+<shift>+u"),
        hotkey_end_experiment=os.getenv("HOTKEY_END_EXPERIMENT", "<ctrl>+<shift>+e"),
    )
