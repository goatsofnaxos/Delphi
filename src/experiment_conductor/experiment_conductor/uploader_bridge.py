"""Bridge to the aind-chronic-ephys-uploader package.

Wraps :class:`~aind_chronic_ephys_uploader.submit_job.SubmitUploadJob` with:

* **Duplicate-submission protection** — ``_SUBMITTED_CHUNKS`` (thread-safe)
  tracks every chunk submitted in the current process.  Chunks already
  in-flight (accepted by the transfer service but not yet visible in S3) are
  never re-submitted across cadence cycles.
* **Confirmed-before-delete guarantee** — :func:`delete_local_files_after_upload`
  queries S3 before touching any local files.  A file is deleted only if its
  chunk is confirmed present in the bucket.

Stop signal
-----------
:data:`UPLOAD_STOP_EVENT` can be set externally (e.g. by a SIGTERM handler)
to cause the uploader to stop cleanly after the current batch completes.
"""
from __future__ import annotations

import fnmatch
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import List, Optional, Set

log = logging.getLogger(__name__)


@dataclass
class UploadCycleResult:
    """Result returned by :func:`run_upload_cycle`.

    Attributes
    ----------
    success:
        *True* when the upload job ran without a hard error.
    submitted_chunks:
        Chunk timestamp strings that were submitted to the transfer service
        in this cycle (empty when the job was skipped or no new chunks exist).
    start_job_not_in_docdb:
        *True* when a ``chronic_ephys_chunk`` job was skipped because the
        matching DocDB record (created by the start job) does not exist yet.
        The caller should reset ``upload_started`` so the next cycle
        re-submits the start job.
    """

    success: bool
    submitted_chunks: list[str] = field(default_factory=list)
    start_job_not_in_docdb: bool = False


# ── Module-level state ────────────────────────────────────────────────────────

UPLOAD_STOP_EVENT = threading.Event()
"""Set this to stop all future upload cycles cleanly after the current batch."""

_SUBMITTED_CHUNKS: Set[str] = set()
_SUBMITTED_LOCK = threading.Lock()

_CHUNK_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}")


def stop_upload() -> None:
    """Signal the uploader to stop after the current batch completes."""
    UPLOAD_STOP_EVENT.set()
    log.info("Upload stop signalled.")


# ── S3 helpers ────────────────────────────────────────────────────────────────

def _list_confirmed_s3_chunks(s3_bucket: str, s3_prefix: str) -> Set[str]:
    """Return the set of chunk timestamp names confirmed present in S3.

    Uses unsigned (public-read) boto3 access.  Returns an empty set on any
    error so that callers fail safe — files are kept locally rather than
    deleted from unconfirmed data.

    Parameters
    ----------
    s3_bucket : str
        S3 bucket name.
    s3_prefix : str
        Object key prefix for this dataset.

    Returns
    -------
    set of str
        Chunk timestamp strings (e.g. ``"2026-01-01T10-00-00"``) that are
        confirmed present in S3.
    """
    try:
        import boto3
        from botocore import UNSIGNED
        from botocore.client import Config

        client = boto3.client("s3", config=Config(signature_version=UNSIGNED))
        paginator = client.get_paginator("list_objects_v2")
        chunks: Set[str] = set()
        for page in paginator.paginate(Bucket=s3_bucket, Prefix=s3_prefix):
            for obj in page.get("Contents", []):
                m = _CHUNK_RE.search(obj["Key"])
                if m:
                    chunks.add(m.group(0))
        log.debug(
            "Confirmed S3 chunks under '%s': %d found.", s3_prefix, len(chunks)
        )
        return chunks
    except Exception as exc:
        log.error("Could not list S3 chunks (treating as empty): %s", exc)
        return set()


# ── Upload job wrapper ────────────────────────────────────────────────────────

class _StoppableSubmitUploadJob:
    """Thin wrapper around :class:`SubmitUploadJob` with stop-signal support.

    Filters out chunks that are already confirmed in S3 or already submitted
    in this session before posting any batch.

    Parameters
    ----------
    job_settings : JobSettings
        Settings for the underlying upload job.
    stop_event : threading.Event
        When set, the uploader exits cleanly after the current batch.
    """

    def __init__(self, job_settings, stop_event: threading.Event):
        from aind_chronic_ephys_uploader.submit_job import SubmitUploadJob

        self._inner = SubmitUploadJob(job_settings=job_settings)
        self._stop = stop_event

    def run_job(
        self,
        skip_chunks: Optional[Set[str]] = None,
    ) -> list[str]:
        """Run the upload job with duplicate-filtering and stop support.

        Parameters
        ----------
        skip_chunks:
            Additional chunk timestamps to exclude (e.g. already-confirmed or
            sidecar-skipped chunks from a previous run).  Combined with the
            in-process ``_SUBMITTED_CHUNKS`` set and S3-confirmed cloud chunks.

        Returns
        -------
        list of str
            Chunk timestamps submitted in this call (may be empty).
        """
        from itertools import batched

        settings = self._inner.job_settings
        is_in_docdb = self._inner._is_in_docdb()
        job_type = settings.job_type

        if job_type == "chronic_ephys_chunk" and not is_in_docdb:
            raise FileNotFoundError(
                f"{settings.s3_location} not found in DocDB yet!"
            )
        if job_type == "chronic_ephys_start" and is_in_docdb:
            raise FileExistsError(f"{settings.s3_location} already exists!")

        local_files = self._inner._get_list_of_local_files()
        local_chunks = self._inner._get_list_of_chunks(local_files)
        cloud_files = self._inner._get_list_of_s3_files()
        cloud_chunks = set(self._inner._get_list_of_chunks(cloud_files))

        with _SUBMITTED_LOCK:
            submitted_snap = set(_SUBMITTED_CHUNKS)

        extra_skip = set(skip_chunks) if skip_chunks else set()

        log.info(
            "Chunks — local: %d  S3 confirmed: %d  in-flight (this session): %d"
            "  sidecar-skip: %d",
            len(local_chunks),
            len(cloud_chunks),
            len(submitted_snap),
            len(extra_skip),
        )

        already_handled = cloud_chunks | submitted_snap | extra_skip
        chunks_pending = sorted(set(local_chunks) - already_handled)

        skipped_inflight = len(set(local_chunks) - cloud_chunks - extra_skip) - len(chunks_pending)
        if skipped_inflight > 0:
            log.info(
                "Skipping %d in-flight chunk(s) already submitted this session.",
                skipped_inflight,
            )

        if job_type == "chronic_ephys_start":
            pending_total = sorted(set(local_chunks) - cloud_chunks)
            if len(pending_total) < 3:
                raise Exception(
                    f"Need ≥3 chunks before starting upload; "
                    f"found {len(pending_total)} not yet on S3."
                )
            chunks_to_process = chunks_pending[:1]
        elif settings.num_of_last_chunks_to_ignore > 0:
            chunks_to_process = chunks_pending[: -settings.num_of_last_chunks_to_ignore]
        else:
            chunks_to_process = chunks_pending

        newly_submitted: list[str] = []

        if not chunks_to_process:
            log.info("No new chunks to submit this cycle.")
            return newly_submitted

        all_batches = list(batched(chunks_to_process, settings.batches_to_process_concurrently))
        total_batches = len(all_batches)
        log.info(
            "Submitting %d batch(es) covering %d chunk(s).",
            total_batches,
            len(chunks_to_process),
        )

        for idx, batch in enumerate(all_batches):
            if self._stop.is_set():
                log.info("Upload stopped after batch %d/%d.", idx, total_batches)
                break

            upload_jobs = [self._inner._get_upload_job_configs(chunk) for chunk in batch]
            self._inner._submit_request(upload_jobs=upload_jobs)

            with _SUBMITTED_LOCK:
                _SUBMITTED_CHUNKS.update(batch)
            newly_submitted.extend(batch)
            log.info(
                "Submitted batch %d/%d (%d chunk(s)).  "
                "Total in-flight this session: %d.",
                idx + 1,
                total_batches,
                len(batch),
                len(_SUBMITTED_CHUNKS),
            )

            if idx < total_batches - 1 and not settings.dry_run:
                wait_secs = settings.time_to_wait_between_batches
                log.info(
                    "Waiting %d s before next batch ...", wait_secs
                )
                elapsed = 0
                while elapsed < wait_secs:
                    if self._stop.is_set():
                        break
                    sleep(1)
                    elapsed += 1

        log.info("Upload job finished.")
        return newly_submitted


# ── Public API ────────────────────────────────────────────────────────────────

def run_upload_cycle(
    *,
    source_directory: str,
    subject_id: str,
    acq_datetime: datetime,
    project_name: str,
    contact_email: str,
    s3_bucket: str,
    batch_size: int = 2,
    modalities: Optional[List] = None,
    num_of_last_chunks_to_ignore: int = 2,
    dry_run: bool = False,
    is_start_job: bool = False,
    skip_chunks: Optional[Set[str]] = None,
) -> UploadCycleResult:
    """Submit one upload cycle (start or chunk job) to the transfer service.

    Parameters
    ----------
    source_directory : str
        Local run-level data directory path.
    subject_id : str
        Numeric AIND subject identifier.
    acq_datetime : datetime
        Acquisition start datetime (used for the S3 prefix and DocDB key).
    project_name : str
        AIND project name for the upload job.
    contact_email : str
        Email address for upload job notifications.
    s3_bucket : str
        S3 bucket name.
    batch_size : int
        Number of chunks submitted per POST request (default 2).
    modalities : list, optional
        Modality values.  Defaults to
        ``[ECEPHYS, BEHAVIOR, BEHAVIOR_VIDEOS]``.
    num_of_last_chunks_to_ignore : int
        Most-recent chunks to skip to avoid uploading in-progress data.
    dry_run : bool
        If *True*, print requests without submitting.
    is_start_job : bool
        If *True*, submit ``chronic_ephys_start``; otherwise
        ``chronic_ephys_chunk``.
    skip_chunks : set of str, optional
        Chunk timestamps to unconditionally skip (e.g. already-confirmed or
        sidecar-skipped chunks supplied by :class:`~.upload_sidecar.UploadSidecar`).

    Returns
    -------
    UploadCycleResult
        ``.success`` is *True* when the job ran without a hard error.
        ``.submitted_chunks`` lists every chunk timestamp submitted in this cycle.
    """
    try:
        from aind_chronic_ephys_uploader.models import JobSettings
        from aind_data_schema_models.modalities import Modality

        if modalities is None:
            modalities = [Modality.ECEPHYS, Modality.BEHAVIOR, Modality.BEHAVIOR_VIDEOS]

        job_type = "chronic_ephys_start" if is_start_job else "chronic_ephys_chunk"
        settings = JobSettings(
            source_directory=source_directory,
            job_type=job_type,
            acq_datetime=acq_datetime,
            subject_id=subject_id,
            project_name=project_name,
            contact_email=contact_email,
            modalities=modalities,
            s3_bucket=s3_bucket,
            batches_to_process_concurrently=batch_size,
            num_of_last_chunks_to_ignore=num_of_last_chunks_to_ignore,
            dry_run=dry_run,
        )

        job = _StoppableSubmitUploadJob(
            job_settings=settings,
            stop_event=UPLOAD_STOP_EVENT,
        )
        submitted = job.run_job(skip_chunks=skip_chunks)
        return UploadCycleResult(success=True, submitted_chunks=submitted)

    except FileNotFoundError as exc:
        # "not found in DocDB yet" — chunk job blocked because the start job
        # record was never registered.  Signal the caller so it can reset
        # upload_started and re-submit the start job next cycle.
        msg = str(exc)
        not_in_docdb = "not found in DocDB yet" in msg
        log.warning("Upload cycle skipped: %s", exc)
        return UploadCycleResult(success=False, start_job_not_in_docdb=not_in_docdb)
    except FileExistsError as exc:
        log.warning("Upload cycle skipped: %s", exc)
        return UploadCycleResult(success=False)
    except Exception as exc:
        log.error("Upload cycle failed: %s", exc, exc_info=True)
        return UploadCycleResult(success=False)


def list_confirmed_s3_chunks(s3_bucket: str, s3_prefix: str) -> Set[str]:
    """Return the set of chunk timestamps confirmed present in S3.

    Public wrapper around :func:`_list_confirmed_s3_chunks`.

    Parameters
    ----------
    s3_bucket : str
        S3 bucket name.
    s3_prefix : str
        Object key prefix for this dataset (e.g. ``"ecephys_842456_2026-01-01_10-00-00"``).

    Returns
    -------
    set of str
        Chunk timestamp strings confirmed in S3.  Empty on any error.
    """
    return _list_confirmed_s3_chunks(s3_bucket, s3_prefix)


def compute_s3_prefix(
    source_directory: str,
    subject_id: str,
    acq_datetime: datetime,
    s3_bucket: str,
    modalities: Optional[List] = None,
) -> Optional[str]:
    """Compute the S3 key prefix for a dataset without submitting any jobs.

    Uses :class:`~aind_chronic_ephys_uploader.models.JobSettings` to derive
    the same prefix the uploader would use, so callers can query S3 with a
    consistent prefix.

    Parameters
    ----------
    source_directory : str
        Local run-level data directory path.
    subject_id : str
        Numeric AIND subject identifier.
    acq_datetime : datetime
        Acquisition start datetime.
    s3_bucket : str
        S3 bucket name.
    modalities : list, optional
        Modality values.  Defaults to ``[ECEPHYS, BEHAVIOR, BEHAVIOR_VIDEOS]``.

    Returns
    -------
    str or None
        The S3 prefix string, or *None* if it could not be computed.
    """
    try:
        from aind_chronic_ephys_uploader.models import JobSettings
        from aind_data_schema_models.modalities import Modality

        if modalities is None:
            modalities = [Modality.ECEPHYS, Modality.BEHAVIOR, Modality.BEHAVIOR_VIDEOS]

        settings = JobSettings(
            source_directory=source_directory,
            job_type="chronic_ephys_chunk",
            acq_datetime=acq_datetime,
            subject_id=subject_id,
            project_name="",
            contact_email="noreply@example.com",
            modalities=modalities,
            s3_bucket=s3_bucket,
        )
        return settings.s3_prefix
    except Exception as exc:
        log.error("Could not compute S3 prefix: %s", exc)
        return None


def delete_local_files_after_upload(
    data_root: Path,
    keep_patterns: List[str],
    s3_bucket: str,
    subject_id: str,
    acq_datetime: datetime,
) -> None:
    """Delete large local files only after confirming their chunk is on S3.

    For every file under ``behavior-videos/`` and ``ecephys/``, the chunk
    timestamp is extracted from its path.  The file is deleted only if its
    chunk is confirmed present in S3 and it does not match any of
    ``keep_patterns``.

    Parameters
    ----------
    data_root : Path
        Run-level session directory.
    keep_patterns : list of str
        Glob patterns relative to *data_root* for files to always keep.
    s3_bucket : str
        S3 bucket name (used to query confirmed chunks).
    subject_id : str
        Subject ID (used to compute the S3 prefix).
    acq_datetime : datetime
        Acquisition start datetime (used to compute the S3 prefix).
    """
    try:
        from aind_chronic_ephys_uploader.models import JobSettings
        from aind_data_schema_models.modalities import Modality

        _settings = JobSettings(
            source_directory=str(data_root),
            job_type="chronic_ephys_chunk",
            acq_datetime=acq_datetime,
            subject_id=subject_id,
            project_name="",
            contact_email="noreply@example.com",
            modalities=[Modality.ECEPHYS, Modality.BEHAVIOR, Modality.BEHAVIOR_VIDEOS],
            s3_bucket=s3_bucket,
        )
        s3_prefix = _settings.s3_prefix
    except Exception as exc:
        log.error("Could not compute S3 prefix — aborting local deletion: %s", exc)
        return

    log.info("Querying S3 for confirmed chunks before local deletion ...")
    confirmed_chunks = _list_confirmed_s3_chunks(s3_bucket, s3_prefix)

    if not confirmed_chunks:
        log.warning(
            "No chunks confirmed on S3 — skipping local deletion to avoid data loss."
        )
        return

    log.info(
        "%d chunk(s) confirmed in S3; proceeding with local deletion.",
        len(confirmed_chunks),
    )

    delete_dirs = [data_root / "behavior-videos", data_root / "ecephys"]
    kept = deleted = skipped_unconfirmed = 0

    for delete_dir in delete_dirs:
        if not delete_dir.exists():
            continue
        for fpath in sorted(delete_dir.rglob("*")):
            if not fpath.is_file():
                continue

            rel = fpath.relative_to(data_root).as_posix()

            if any(fnmatch.fnmatch(rel, pat) for pat in keep_patterns):
                log.debug("Keeping (keep pattern): %s", rel)
                kept += 1
                continue

            m = _CHUNK_RE.search(rel)
            if m and m.group(0) not in confirmed_chunks:
                log.debug(
                    "Keeping (chunk not yet in S3): %s [chunk=%s]", rel, m.group(0)
                )
                skipped_unconfirmed += 1
                continue

            try:
                fpath.unlink()
                log.info("Deleted: %s", fpath)
                deleted += 1
            except Exception as exc:
                log.warning("Could not delete %s: %s", fpath, exc)

    log.info(
        "Post-upload deletion: %d deleted, %d kept (pattern), %d kept (S3 unconfirmed).",
        deleted,
        kept,
        skipped_unconfirmed,
    )
