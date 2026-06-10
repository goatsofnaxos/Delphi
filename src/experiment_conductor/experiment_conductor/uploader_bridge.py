"""Bridge to the aind-chronic-ephys-uploader package.

Wraps SubmitUploadJob with pause/resume support by overriding the
sleep between upload batches. Also provides local-file deletion
after confirmed S3 upload.

Duplicate-submission protection
--------------------------------
``_SUBMITTED_CHUNKS`` tracks every chunk whose job has been posted to the
transfer service in the current process run.  Before submitting any batch the
set is checked so that chunks already in-flight (accepted by the service but
not yet visible in S3) are not re-submitted.  The set is never cleared during
a run; it accumulates across all cadence cycles.

Confirmed-before-delete guarantee
-----------------------------------
``delete_local_files_after_upload`` queries S3 directly to obtain the set of
chunks that are confirmed present before touching any local files.  A file is
only deleted if its chunk ancestor directory is in the confirmed S3 set.  Files
whose chunk has not yet completed (still in-flight or not yet submitted) are
left untouched.
"""
from __future__ import annotations

import fnmatch
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import List, Optional, Set

log = logging.getLogger(__name__)

# ── Pause / stop events shared with the hotkey handler ───────────────────────
UPLOAD_PAUSE_EVENT = threading.Event()
UPLOAD_STOP_EVENT = threading.Event()

# ── In-flight chunk tracking (fix 1) ─────────────────────────────────────────
# Populated as chunks are submitted; never cleared during a run so that
# cadence cycles cannot re-submit chunks that are still being transferred.
_SUBMITTED_CHUNKS: Set[str] = set()

_CHUNK_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}")


def toggle_upload_pause() -> bool:
    """Toggle the upload pause state.

    Returns
    -------
    bool
        True if upload is now paused, False if resumed.
    """
    if UPLOAD_PAUSE_EVENT.is_set():
        UPLOAD_PAUSE_EVENT.clear()
        log.info("Upload RESUMED.")
        return False
    else:
        UPLOAD_PAUSE_EVENT.set()
        log.info("Upload PAUSED. Press Ctrl+Shift+U to resume.")
        return True


def stop_upload() -> None:
    """Signal the uploader to stop after the current batch."""
    UPLOAD_STOP_EVENT.set()
    UPLOAD_PAUSE_EVENT.set()  # unblock any ongoing inter-batch wait
    log.info("Upload stop signalled.")


def _list_confirmed_s3_chunks(s3_bucket: str, s3_prefix: str) -> Set[str]:
    """Return the set of chunk names confirmed present under *s3_prefix* in S3.

    Uses unsigned (public-read) boto3 access, matching the uploader library's
    own approach.  Returns an empty set on any error so that callers fail safe
    (keep files locally rather than deleting unconfirmed data).

    Parameters
    ----------
    s3_bucket : str
        S3 bucket name.
    s3_prefix : str
        Object key prefix for this dataset (e.g. ``subject_YYYY-MM-DDTHH-MM-SS``).

    Returns
    -------
    set of str
        Chunk timestamp strings (e.g. ``"2026-01-01T10-00-00"``) confirmed in S3.
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
        log.debug("Confirmed S3 chunks under '%s': %d", s3_prefix, len(chunks))
        return chunks
    except Exception as exc:
        log.error("Could not list S3 chunks (treating as empty): %s", exc)
        return set()


class PausableSubmitUploadJob:
    """SubmitUploadJob wrapper with pause/resume and duplicate-submission guard.

    Before submitting each batch, chunks that are already confirmed on S3
    *or* already submitted this session (in-flight) are removed from the
    candidate list.  After each successful POST the submitted chunks are
    recorded in ``_SUBMITTED_CHUNKS``.

    Parameters
    ----------
    job_settings : JobSettings
        Settings for the upload job.
    pause_event : threading.Event
        When set, pauses the uploader between batches.
    stop_event : threading.Event
        When set, stops the uploader after the current batch.
    """

    def __init__(self, job_settings, pause_event: threading.Event, stop_event: threading.Event):
        from aind_chronic_ephys_uploader.submit_job import SubmitUploadJob
        self._inner = SubmitUploadJob(job_settings=job_settings)
        self._pause = pause_event
        self._stop = stop_event

    def run_job(self) -> None:
        """Run the upload job with duplicate-submission filtering and pause/resume.

        Chunk filtering order (most to least recently checked):

        1. Already confirmed in S3 (live query).
        2. Already submitted this session (``_SUBMITTED_CHUNKS``).

        After each successful batch POST the chunk names are added to
        ``_SUBMITTED_CHUNKS`` so subsequent cadence cycles cannot re-submit
        them while they are still in-flight.
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

        log.info(
            "Local chunks: %d  |  S3 confirmed: %d  |  In-flight (this session): %d",
            len(local_chunks), len(cloud_chunks), len(_SUBMITTED_CHUNKS),
        )

        # Exclude chunks already on S3 or already submitted this session
        already_handled = cloud_chunks | _SUBMITTED_CHUNKS
        chunks_pending = sorted(set(local_chunks) - already_handled)

        skipped_inflight = len(set(local_chunks) - cloud_chunks) - len(chunks_pending)
        if skipped_inflight > 0:
            log.info(
                "Skipping %d in-flight chunk(s) already submitted this session.",
                skipped_inflight,
            )

        if job_type == "chronic_ephys_start":
            # Use total pending (before in-flight filter) to meet the >=3 requirement
            pending_total = sorted(set(local_chunks) - cloud_chunks)
            if len(pending_total) < 3:
                raise Exception(
                    f"Need at least 3 chunks before starting upload; "
                    f"found {len(pending_total)} not yet on S3."
                )
            # Submit only the first chunk not already in-flight
            chunks_to_process = chunks_pending[:1]
        elif settings.num_of_last_chunks_to_ignore > 0:
            chunks_to_process = chunks_pending[: -settings.num_of_last_chunks_to_ignore]
        else:
            chunks_to_process = chunks_pending

        if not chunks_to_process:
            log.info("No new chunks to submit this cycle.")
            return

        all_batches = list(batched(chunks_to_process, settings.batches_to_process_concurrently))
        total = len(all_batches)
        log.info("Submitting %d batch(es) covering %d chunk(s).", total, len(chunks_to_process))

        for idx, batch in enumerate(all_batches):
            if self._stop.is_set():
                log.info("Upload stopped by user after batch %d/%d.", idx, total)
                break

            upload_jobs = [self._inner._get_upload_job_configs(chunk) for chunk in batch]
            self._inner._submit_request(upload_jobs=upload_jobs)

            # Record as submitted immediately after a successful POST so
            # subsequent cycles will not re-submit these chunks while in-flight
            _SUBMITTED_CHUNKS.update(batch)
            log.info(
                "Submitted batch %d/%d (%d chunk(s)). Total in-flight this session: %d.",
                idx + 1, total, len(batch), len(_SUBMITTED_CHUNKS),
            )

            if idx < total - 1 and not settings.dry_run:
                wait_secs = settings.time_to_wait_between_batches
                log.info(
                    "Waiting %ds before next batch (Ctrl+Shift+U to pause/resume) ...",
                    wait_secs,
                )
                elapsed = 0
                while elapsed < wait_secs:
                    if self._stop.is_set():
                        break
                    while self._pause.is_set() and not self._stop.is_set():
                        sleep(1)
                    sleep(1)
                    elapsed += 1

        log.info("Upload job finished.")


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
) -> bool:
    """Submit one upload cycle (start or chunk job).

    Parameters
    ----------
    source_directory : str
        Local data root path.
    subject_id : str
        Mouse subject ID.
    acq_datetime : datetime
        Acquisition start datetime.
    project_name : str
        AIND project name.
    contact_email : str
        Contact email for job notifications.
    s3_bucket : str
        S3 bucket name.
    batch_size : int
        Number of chunks per batch submitted to the transfer service (default 2).
    modalities : list, optional
        List of Modality values. Defaults to ECEPHYS + BEHAVIOR + BEHAVIOR_VIDEOS.
    num_of_last_chunks_to_ignore : int
        Number of most-recent chunks to skip (avoid uploading in-progress data).
    dry_run : bool
        If True, print requests without submitting.
    is_start_job : bool
        If True, submit ``chronic_ephys_start``; otherwise ``chronic_ephys_chunk``.

    Returns
    -------
    bool
        True on success, False if an exception occurred.
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

        job = PausableSubmitUploadJob(
            job_settings=settings,
            pause_event=UPLOAD_PAUSE_EVENT,
            stop_event=UPLOAD_STOP_EVENT,
        )
        job.run_job()
        return True

    except (FileNotFoundError, FileExistsError) as exc:
        log.warning("Upload cycle skipped: %s", exc)
        return False
    except Exception as exc:
        log.error("Upload cycle failed: %s", exc, exc_info=True)
        return False


def delete_local_files_after_upload(
    data_root: Path,
    keep_patterns: List[str],
    s3_bucket: str,
    subject_id: str,
    acq_datetime: datetime,
) -> None:
    """Delete large local files only after confirming their chunk is on S3.

    For every file under ``behavior-videos/`` and ``ecephys/``, the chunk
    timestamp is extracted from the file's path.  The file is deleted only if:

    - Its chunk is confirmed present in S3 (live query before deletion starts).
    - It does not match any pattern in ``keep_patterns``.

    Files in directories whose chunk has not yet been confirmed on S3 (still
    in-flight or not yet submitted) are always left untouched.

    Parameters
    ----------
    data_root : Path
        Run-level session directory.
    keep_patterns : list of str
        Glob patterns relative to ``data_root`` for files to always keep.
    s3_bucket : str
        S3 bucket used for the upload (needed to query confirmed chunks).
    subject_id : str
        Subject ID, used to compute the S3 prefix.
    acq_datetime : datetime
        Acquisition start datetime, used to compute the S3 prefix.

    Returns
    -------
    None
    """
    # Compute the S3 prefix the same way the uploader library does
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
        log.error("Could not compute S3 prefix; aborting local deletion: %s", exc)
        return

    log.info("Querying S3 for confirmed chunks before local deletion ...")
    confirmed_chunks = _list_confirmed_s3_chunks(s3_bucket, s3_prefix)

    if not confirmed_chunks:
        log.warning(
            "No chunks confirmed on S3 — skipping local deletion to avoid data loss."
        )
        return

    log.info(
        "%d chunk(s) confirmed on S3; proceeding with local deletion.", len(confirmed_chunks)
    )

    delete_dirs = [data_root / "behavior-videos", data_root / "ecephys"]
    kept = 0
    deleted = 0
    skipped_unconfirmed = 0

    for delete_dir in delete_dirs:
        if not delete_dir.exists():
            continue
        for fpath in sorted(delete_dir.rglob("*")):
            if not fpath.is_file():
                continue

            rel = fpath.relative_to(data_root).as_posix()

            # Always keep files matching keep patterns
            if any(fnmatch.fnmatch(rel, pat) for pat in keep_patterns):
                log.debug("Keeping (keep pattern): %s", rel)
                kept += 1
                continue

            # Extract the chunk timestamp from the file path
            m = _CHUNK_RE.search(rel)
            if m:
                chunk = m.group(0)
                if chunk not in confirmed_chunks:
                    log.debug(
                        "Keeping (chunk not yet confirmed on S3): %s [chunk=%s]", rel, chunk
                    )
                    skipped_unconfirmed += 1
                    continue
            # Files with no chunk in their path are treated as non-chunked
            # ancillary files; delete them if they passed the keep-pattern check.

            try:
                fpath.unlink()
                log.info("Deleted: %s", fpath)
                deleted += 1
            except Exception as exc:
                log.warning("Could not delete %s: %s", fpath, exc)

    log.info(
        "Post-upload deletion: %d deleted, %d kept (pattern), %d kept (unconfirmed chunk).",
        deleted, kept, skipped_unconfirmed,
    )
