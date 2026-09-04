"""Per-session upload history sidecar.

Written to ``<run_dir>/.upload_history.json`` on the first upload cycle and
updated incrementally on every subsequent cycle.  Tracks each chunk's
submission, S3 confirmation, deletion, and retry count so the conductor can:

* Resume correctly after a restart without re-submitting already-confirmed
  chunks.
* Skip chunks that have exceeded the configured retry limit.
* Expose a complete, readable history to the ``conductor-status`` viewer.

Chunk states
------------
``pending``
    Chunk exists locally but has never been submitted.
``submitted``
    Submission was sent to the transfer service; S3 confirmation pending.
``success``
    Chunk confirmed present in S3.
``skipped``
    Chunk exceeded the maximum retry count and will no longer be submitted.
``failed``
    Chunk submission encountered a hard error (recorded in ``errors``).

Delete states
-------------
``disabled``
    ``delete_after_upload`` is off; local files are kept.
``pending``
    Chunk is in S3 but the local delete sweep has not run yet.
``success``
    Local large files for this chunk have been removed.
``failed``
    Deletion was attempted but at least one file could not be removed.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set

log = logging.getLogger(__name__)

SIDECAR_FILENAME = ".upload_history.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ChunkRecord:
    """State record for a single chunk.

    Attributes
    ----------
    state:
        Upload lifecycle state (see module docstring).
    submitted_at:
        ISO-8601 timestamp of the most recent submission attempt.
    confirmed_at:
        ISO-8601 timestamp when the chunk was first confirmed in S3.
    delete_state:
        Local-file deletion state (see module docstring).
    deleted_at:
        ISO-8601 timestamp when local files were confirmed deleted.
    retries:
        Number of times submission has been attempted for this chunk.
    errors:
        Ordered list of error messages recorded for this chunk.
    """

    state: str = "pending"
    submitted_at: Optional[str] = None
    confirmed_at: Optional[str] = None
    delete_state: str = "disabled"
    deleted_at: Optional[str] = None
    retries: int = 0
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "submitted_at": self.submitted_at,
            "confirmed_at": self.confirmed_at,
            "delete_state": self.delete_state,
            "deleted_at": self.deleted_at,
            "retries": self.retries,
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChunkRecord":
        obj = cls()
        obj.state = d.get("state", "pending")
        obj.submitted_at = d.get("submitted_at")
        obj.confirmed_at = d.get("confirmed_at")
        obj.delete_state = d.get("delete_state", "disabled")
        obj.deleted_at = d.get("deleted_at")
        obj.retries = d.get("retries", 0)
        obj.errors = list(d.get("errors", []))
        return obj


class UploadSidecar:
    """Thread-safe manager for the per-session upload history sidecar file.

    Parameters
    ----------
    run_dir:
        Session run directory.  The sidecar is written to
        ``run_dir / ".upload_history.json"``.
    subject_id:
        AIND subject identifier (stored in the sidecar for context).
    session_ts:
        Session timestamp string (stored in the sidecar for context).
    delete_enabled:
        Whether ``delete_after_upload`` is configured.  Sets the initial
        ``delete_state`` on new chunk records.
    """

    def __init__(
        self,
        run_dir: Path,
        subject_id: str,
        session_ts: str,
        delete_enabled: bool,
    ) -> None:
        self.path = run_dir / SIDECAR_FILENAME
        self.subject_id = subject_id
        self.session_ts = session_ts
        self.delete_enabled = delete_enabled
        self._lock = threading.Lock()
        self._chunks: dict[str, ChunkRecord] = {}
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for chunk_ts, rec in data.get("chunks", {}).items():
                self._chunks[chunk_ts] = ChunkRecord.from_dict(rec)
            log.debug(
                "Upload sidecar loaded from %s (%d chunk(s)).",
                self.path,
                len(self._chunks),
            )
        except Exception as exc:
            log.warning("Could not load upload sidecar %s: %s", self.path, exc)

    def _save(self) -> None:
        """Write the current state to disk.  Must be called under ``_lock``."""
        try:
            data = {
                "subject_id": self.subject_id,
                "session_ts": self.session_ts,
                "delete_enabled": self.delete_enabled,
                "chunks": {
                    k: v.to_dict()
                    for k, v in sorted(self._chunks.items())
                },
                "updated_at": _now_iso(),
            }
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("Could not save upload sidecar %s: %s", self.path, exc)

    # ── Chunk state mutations (all thread-safe) ───────────────────────────────

    def mark_submitted(self, chunk_ts: str, max_retries: int) -> None:
        """Record a submission attempt for *chunk_ts*.

        Increments ``retries``.  When *retries* exceeds *max_retries* the
        chunk is immediately transitioned to ``skipped`` so the uploader no
        longer spends bandwidth on it.
        """
        with self._lock:
            if chunk_ts not in self._chunks:
                self._chunks[chunk_ts] = ChunkRecord()
            rec = self._chunks[chunk_ts]
            if rec.state in ("success", "skipped"):
                return
            rec.retries += 1
            rec.submitted_at = _now_iso()
            rec.delete_state = "pending" if self.delete_enabled else "disabled"
            if rec.retries > max_retries:
                rec.state = "skipped"
                log.warning(
                    "Chunk %s exceeded max upload retries (%d) — marking skipped.",
                    chunk_ts,
                    max_retries,
                )
            else:
                rec.state = "submitted"
            self._save()

    def mark_confirmed(self, chunk_ts: str) -> None:
        """Mark *chunk_ts* as confirmed present in S3."""
        with self._lock:
            if chunk_ts not in self._chunks:
                self._chunks[chunk_ts] = ChunkRecord()
            rec = self._chunks[chunk_ts]
            if rec.state == "skipped":
                return
            rec.state = "success"
            if rec.confirmed_at is None:
                rec.confirmed_at = _now_iso()
            if not self.delete_enabled:
                rec.delete_state = "disabled"
            elif rec.delete_state == "disabled":
                rec.delete_state = "pending"
            self._save()

    def mark_deleted(self, chunk_ts: str) -> None:
        """Record that local large files for *chunk_ts* have been removed."""
        with self._lock:
            rec = self._chunks.get(chunk_ts)
            if rec is None:
                return
            rec.delete_state = "success"
            rec.deleted_at = _now_iso()
            self._save()

    def mark_delete_failed(self, chunk_ts: str, error: str) -> None:
        """Record that local file deletion for *chunk_ts* failed."""
        with self._lock:
            rec = self._chunks.get(chunk_ts)
            if rec is None:
                return
            rec.delete_state = "failed"
            rec.errors.append(f"delete-failed: {error}")
            self._save()

    def mark_error(self, chunk_ts: str, message: str) -> None:
        """Append *message* to *chunk_ts*'s error list and set state ``failed``."""
        with self._lock:
            if chunk_ts not in self._chunks:
                self._chunks[chunk_ts] = ChunkRecord()
            rec = self._chunks[chunk_ts]
            if rec.state not in ("success", "skipped"):
                rec.state = "failed"
            rec.errors.append(message)
            self._save()

    def reset_submitted_chunks(self) -> int:
        """Reset all chunks in ``submitted`` state back to ``pending``.

        Called when a previously-submitted start job is found to have never
        landed in DocDB (meaning it failed on the transfer service side).
        Resetting allows the uploader to include those chunks in the next
        start-job submission attempt.

        Returns
        -------
        int
            Number of chunks reset.
        """
        with self._lock:
            count = 0
            for rec in self._chunks.values():
                if rec.state == "submitted":
                    rec.state = "pending"
                    rec.submitted_at = None
                    count += 1
            if count:
                self._save()
        return count

    # ── Queries ──────────────────────────────────────────────────────────────

    def chunks_to_skip(self, max_retries: int) -> Set[str]:
        """Return the set of chunk timestamps the uploader must exclude.

        Includes chunks that already succeeded in S3 and chunks whose retry
        count equals or exceeds *max_retries*.
        """
        with self._lock:
            return {
                ts
                for ts, rec in self._chunks.items()
                if rec.state in ("success", "skipped")
                or rec.retries >= max_retries
            }

    def submitted_chunk_timestamps(self) -> Set[str]:
        """Return chunk timestamps currently in the ``submitted`` state."""
        with self._lock:
            return {
                ts for ts, rec in self._chunks.items()
                if rec.state == "submitted"
            }

    def recover_upload_state(self) -> dict:
        """Reconstruct session-level upload state from the sidecar.

        Intended for use after a conductor restart when ``conductor_state.json``
        is absent, incomplete, or newer than the sidecar.  The sidecar is the
        ground truth for what has actually been submitted, so this method
        always wins over a stale in-memory ``SessionState``.

        Returns
        -------
        dict
            ``upload_started`` (*bool*) — *True* if at least one chunk has ever
            been submitted, confirmed, skipped, or failed.  *False* only when
            the sidecar is completely empty (no upload attempts at all).

            ``last_upload_run`` (*datetime | None*) — The most recent
            ``submitted_at`` timestamp across all chunk records, parsed as a
            timezone-aware UTC datetime.  *None* when no submission timestamp
            is recorded.
        """
        with self._lock:
            if not self._chunks:
                return {"upload_started": False, "last_upload_run": None}

            upload_started = any(
                rec.state in ("submitted", "success", "skipped", "failed")
                for rec in self._chunks.values()
            )

            last_run: Optional[datetime] = None
            for rec in self._chunks.values():
                if rec.submitted_at:
                    try:
                        ts = datetime.fromisoformat(rec.submitted_at)
                        if not ts.tzinfo:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if last_run is None or ts > last_run:
                            last_run = ts
                    except ValueError:
                        pass

            return {"upload_started": upload_started, "last_upload_run": last_run}

    def snapshot(self) -> dict[str, ChunkRecord]:
        """Return a deep copy of all chunk records keyed by timestamp."""
        with self._lock:
            return {
                k: ChunkRecord.from_dict(v.to_dict())
                for k, v in self._chunks.items()
            }
