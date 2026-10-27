"""Per-session state for the experiment conductor.

Each acquisition session discovered on the network drive is tracked as an
independent :class:`SessionState` instance, progressing through the
:class:`SessionPhase` lifecycle independently of all other sessions.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class SessionPhase(str, Enum):
    """Lifecycle phase for a single acquisition session."""

    DISCOVERED = "DISCOVERED"
    """Session directory found; no processing has started yet."""

    CONSOLIDATING = "CONSOLIDATING"
    """Merging run sub-directories into the earliest run directory."""

    METADATA_CHECK = "METADATA_CHECK"
    """Checking whether all four AIND metadata JSON files are present."""

    METADATA_GENERATING = "METADATA_GENERATING"
    """Running ``metadata_generator`` to produce missing JSON files."""

    BUILDING = "BUILDING"
    """Running the ``delphi-data`` pipeline (build-dataset and/or snapshot)."""

    NOISE_FLOOR = "NOISE_FLOOR"
    """Estimating the ephys noise floor from raw binary data."""

    UPLOADING = "UPLOADING"
    """Submitting chunk upload jobs to the AIND data-transfer service."""

    COMPLETE = "COMPLETE"
    """All local chunks confirmed in S3; no further processing needed."""

    ERROR = "ERROR"
    """Unrecoverable error; manual intervention required."""


@dataclass
class SessionState:
    """Mutable state for one acquisition session.

    Thread-safety is provided by the embedded :attr:`lock`.  Any field that
    is mutated after initialisation must be accessed under ``state.lock``.

    Parameters
    ----------
    data_root : Path
        Session root directory.  Contains one or more run-timestamp
        sub-directories (e.g. ``2026-03-20T20-23-37/``) before consolidation.
    subject_id : str
        Numeric AIND subject identifier (e.g. ``"842456"``).
    session_datetime : str
        Session timestamp in ``YYYY-MM-DDTHH-MM-SS`` format; equals
        ``data_root.name``.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    data_root: Path
    """Session root directory (e.g. ``…/842456/2026-03-20T20-23-05``)."""

    subject_id: str
    """Numeric AIND subject identifier."""

    session_datetime: str
    """Session timestamp string matching ``YYYY-MM-DDTHH-MM-SS``."""

    # ── Resolved paths ────────────────────────────────────────────────────────
    run_dir: Path | None = None
    """Earliest run sub-directory; resolved after the first consolidation.
    All per-session steps (pipeline, metadata, upload) operate on this path."""

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    phase: SessionPhase = SessionPhase.DISCOVERED

    # ── Step-completion flags ──────────────────────────────────────────────────
    consolidation_done: bool = False
    """True once run sub-directories have been merged into *run_dir*."""

    metadata_present: bool = False
    """True if all four AIND JSON files were found on the last check."""

    metadata_generated: bool = False
    """True once ``metadata_generator`` finished without errors."""

    dataset_built: bool = False
    """True once ``delphi-data pipeline`` has run at least once."""

    noise_floor_estimated: bool = False
    """True once a noise-floor estimate has been written to ``ecephys/``."""

    upload_started: bool = False
    """True once the initial ``chronic_ephys_start`` job has been posted."""

    # ── Timing ────────────────────────────────────────────────────────────────
    discovered_at: datetime | None = None
    last_processed: datetime | None = None
    last_upload_run: datetime | None = None

    # ── Error tracking ────────────────────────────────────────────────────────
    error_message: str | None = None
    consecutive_errors: int = 0

    # ── Threading ─────────────────────────────────────────────────────────────
    lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation (excludes ``lock``)."""

        def _iso(v: datetime | None) -> str | None:
            return v.isoformat() if v else None

        return {
            "data_root": str(self.data_root),
            "subject_id": self.subject_id,
            "session_datetime": self.session_datetime,
            "run_dir": str(self.run_dir) if self.run_dir else None,
            "phase": self.phase.value,
            "consolidation_done": self.consolidation_done,
            "metadata_present": self.metadata_present,
            "metadata_generated": self.metadata_generated,
            "dataset_built": self.dataset_built,
            "noise_floor_estimated": self.noise_floor_estimated,
            "upload_started": self.upload_started,
            "discovered_at": _iso(self.discovered_at),
            "last_processed": _iso(self.last_processed),
            "last_upload_run": _iso(self.last_upload_run),
            "error_message": self.error_message,
            "consecutive_errors": self.consecutive_errors,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionState":
        """Reconstruct a :class:`SessionState` from a serialised dict.

        Parameters
        ----------
        d : dict
            Mapping produced by :meth:`to_dict`.

        Returns
        -------
        SessionState
        """

        def _dt(v: str | None) -> datetime | None:
            return datetime.fromisoformat(v) if v else None

        run_dir_raw = d.get("run_dir")
        return cls(
            data_root=Path(d["data_root"]),
            subject_id=d["subject_id"],
            session_datetime=d["session_datetime"],
            run_dir=Path(run_dir_raw) if run_dir_raw else None,
            phase=SessionPhase(d.get("phase", SessionPhase.DISCOVERED)),
            consolidation_done=d.get("consolidation_done", False),
            metadata_present=d.get("metadata_present", False),
            metadata_generated=d.get("metadata_generated", False),
            dataset_built=d.get("dataset_built", False),
            noise_floor_estimated=d.get("noise_floor_estimated", False),
            upload_started=d.get("upload_started", False),
            discovered_at=_dt(d.get("discovered_at")),
            last_processed=_dt(d.get("last_processed")),
            last_upload_run=_dt(d.get("last_upload_run")),
            error_message=d.get("error_message"),
            consecutive_errors=d.get("consecutive_errors", 0),
        )
