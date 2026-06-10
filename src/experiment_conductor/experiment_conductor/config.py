"""Configuration loading for experiment_conductor.

Merges .env file values with CLI overrides. CLI flags always win.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
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
    data_root : Path
        Run-level session directory.
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
    data_root: Path
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
    p.add_argument("--data-root", default=None, type=Path)
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
    """Load .env then overlay CLI arguments to produce a ConductorConfig.

    Returns
    -------
    ConductorConfig
        Fully merged and validated configuration.

    Raises
    ------
    ValueError
        If required fields (data_root, subject_id) are not provided.
    """
    args = _parse_cli()
    load_dotenv(args.env_file, override=False)

    def _g(env_key: str, cli_val=None, default=None):
        return cli_val if cli_val is not None else os.getenv(env_key, default)

    data_root_raw = _g("DATA_ROOT", args.data_root)
    if not data_root_raw:
        raise ValueError("DATA_ROOT must be set (via .env or --data-root).")

    subject_id = _g("SUBJECT_ID", args.subject_id, "")
    if not subject_id:
        raise ValueError("SUBJECT_ID must be set (via .env or --subject-id).")

    surgery_notes_raw = _g("SURGERY_NOTES_BASE", args.surgery_notes_base)

    return ConductorConfig(
        experiment_type=_g("EXPERIMENT_TYPE", args.experiment_type, "delphi_pirouette"),
        experiment_config=_g("EXPERIMENT_CONFIG", args.experiment_config, "delphi_pirouette_experiment"),
        data_root=Path(data_root_raw),
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
        delphi_firmware=_g("DELPHI_FIRMWARE", args.delphi_firmware, "1.0.0"),
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
