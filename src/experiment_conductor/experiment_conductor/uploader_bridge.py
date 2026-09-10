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
from typing import Callable, List, Optional, Set

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
    """

    success: bool
    submitted_chunks: list[str] = field(default_factory=list)


# ── Module-level state ────────────────────────────────────────────────────────

UPLOAD_STOP_EVENT = threading.Event()
"""Set this to stop all future upload cycles cleanly after the current batch."""

_SUBMITTED_CHUNKS: Set[str] = set()
_SUBMITTED_LOCK = threading.Lock()

_CHUNK_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}")

# Extensions whose handling in the deletion loop requires special-casing
# (see delete_local_files_after_upload for the per-type strategy).
# AmplifierData .bin → zarr-based verification; Clock/HubClock .bin → per-file
# key check.  Both are caught by this set so the loop can branch on filename.
_FORMAT_CONVERTED_EXTENSIONS: frozenset[str] = frozenset({".bin"})

# The transfer service writes ecephys/ecephys_compressed/<name>.zarr/.zmetadata
# as the final step of Zarr creation.  Its presence in S3 indicates the zarr
# container exists, but does NOT guarantee every individual chunk is present.
_ZARR_COMPLETION_PREFIX = "ecephys/ecephys_compressed/"
_ZARR_COMPLETION_MARKER = ".zmetadata"

# Filename stem that identifies ONIX AmplifierData .bin files.
# Only AmplifierData is zarr-compressed by the transfer service — Clock and
# HubClock .bin files are uploaded as-is to ecephys/OnixEphys/ and confirmed
# via per-file key existence in S3.
_AMPLIFIER_DATA_STEM = "AmplifierData"

# Zarr array within the AmplifierData zarr that holds per-sample trace data.
# Its shape[0] is the total number of samples in the zarr (cumulative across
# all chunks uploaded so far).
_ZARR_TRACES_ARRAY = "traces_seg0"


def stop_upload() -> None:
    """Signal the uploader to stop after the current batch completes."""
    UPLOAD_STOP_EVENT.set()
    log.info("Upload stop signalled.")


def _to_linux_path(path: str) -> str:
    """Convert a Windows UNC path to the POSIX double-slash form that the
    AIND data-transfer service (Linux) expects.

    The transfer workers mount the Allen share as ``//allen/aind/...``
    (CIFS double-slash notation).  When the conductor runs on Windows,
    ``source_directory`` is ``\\\\allen\\aind\\...``; passing that form to
    the transfer service causes a ``FileNotFoundError`` in its path-validation
    step.  This function is applied inside the conductor bridge so no changes
    to the upstream ``aind-chronic-ephys-uploader`` package are required.

    Conversion rules
    ----------------
    * Windows UNC  ``\\\\server\\share\\...`` → ``//server/share/...``
    * Already-POSIX ``/…`` or ``//…``         → returned as-is
    * Plain Windows ``C:\\foo\\bar``           → ``C:/foo/bar``
    """
    if path.startswith("\\\\"):
        return "//" + path[2:].replace("\\", "/")
    return path.replace("\\", "/")


# ── S3 helpers ────────────────────────────────────────────────────────────────

def _list_s3_objects(
    s3_bucket: str, s3_prefix: str
) -> tuple[Set[str], Set[str]]:
    """List all objects under *s3_prefix* in one paging pass.

    Uses unsigned (public-read) boto3 access.  Returns (set(), set()) on any
    error so callers fail safe — files are kept locally rather than deleted.

    Parameters
    ----------
    s3_bucket : str
        S3 bucket name.
    s3_prefix : str
        Object key prefix for this dataset (no trailing slash).

    Returns
    -------
    chunk_timestamps : set of str
        ``YYYY-MM-DDTHH-MM-SS`` strings found anywhere in any key — used to
        confirm that a chunk has at least one object in S3.
    relative_keys : set of str
        Every object key with *s3_prefix* + ``"/"`` stripped from the front —
        i.e. paths relative to the dataset root, matching the local
        ``fpath.relative_to(data_root).as_posix()`` form used in the deletion
        loop.  Used for per-file existence checks before deletion.
    """
    try:
        import boto3
        from botocore import UNSIGNED
        from botocore.client import Config

        client = boto3.client("s3", config=Config(signature_version=UNSIGNED))
        paginator = client.get_paginator("list_objects_v2")
        chunks: Set[str] = set()
        keys: Set[str] = set()
        prefix_slash = s3_prefix.rstrip("/") + "/"
        for page in paginator.paginate(Bucket=s3_bucket, Prefix=s3_prefix):
            for obj in page.get("Contents", []):
                full_key = obj["Key"]
                rel_key = (
                    full_key[len(prefix_slash):]
                    if full_key.startswith(prefix_slash)
                    else full_key
                )
                keys.add(rel_key)
                m = _CHUNK_RE.search(full_key)
                if m:
                    chunks.add(m.group(0))
        log.debug(
            "S3 listing under '%s': %d object(s), %d chunk timestamp(s).",
            s3_prefix,
            len(keys),
            len(chunks),
        )
        return chunks, keys
    except Exception as exc:
        log.error("Could not list S3 objects (treating as empty): %s", exc)
        return set(), set()


def _list_confirmed_s3_chunks(s3_bucket: str, s3_prefix: str) -> Set[str]:
    """Return the set of chunk timestamps confirmed present in S3.

    Thin wrapper around :func:`_list_s3_objects` that discards the per-file
    key set, preserving the existing call sites in :mod:`session_manager`.
    """
    chunks, _ = _list_s3_objects(s3_bucket, s3_prefix)
    return chunks


def _fetch_zarr_sample_count(
    s3_bucket: str,
    s3_prefix: str,
    s3_keys: Set[str],
) -> Optional[int]:
    """Return the total sample count stored in the AmplifierData zarr on S3.

    Locates the ``.zmetadata`` object for the AmplifierData zarr from the
    already-fetched *s3_keys* listing (no extra pagination call), then
    downloads that single JSON to read ``traces_seg0/.zarray["shape"][0]``.
    Uses unsigned (public-read) boto3 access; returns *None* on any error so
    callers fail safe and keep the local ``.bin`` files.

    Parameters
    ----------
    s3_bucket : str
        S3 bucket name.
    s3_prefix : str
        Dataset prefix (no trailing slash) — the same prefix used by
        :func:`_list_s3_objects`.
    s3_keys : set of str
        Relative object keys already returned by :func:`_list_s3_objects`.
        The ``.zmetadata`` file is located by scanning this set rather than
        making another list call.

    Returns
    -------
    int or None
        Total number of samples in ``traces_seg0`` (axis 0 of its shape), or
        *None* if the ``.zmetadata`` key is not present in *s3_keys* or the
        object cannot be read / parsed.
    """
    # Find the .zmetadata key for the AmplifierData zarr in the S3 listing.
    zmetadata_rel = next(
        (
            k for k in s3_keys
            if k.startswith(_ZARR_COMPLETION_PREFIX)
            and _AMPLIFIER_DATA_STEM in k
            and k.endswith(f"/{_ZARR_COMPLETION_MARKER}")
        ),
        None,
    )
    if zmetadata_rel is None:
        log.debug(
            "AmplifierData zarr .zmetadata not found in S3 listing"
            " — zarr not yet finalised."
        )
        return None

    full_key = s3_prefix.rstrip("/") + "/" + zmetadata_rel
    try:
        import boto3
        import json as _json
        from botocore import UNSIGNED
        from botocore.client import Config

        client = boto3.client("s3", config=Config(signature_version=UNSIGNED))
        resp = client.get_object(Bucket=s3_bucket, Key=full_key)
        zmetadata = _json.loads(resp["Body"].read())
        zarray_key = f"{_ZARR_TRACES_ARRAY}/.zarray"
        shape = zmetadata["metadata"][zarray_key]["shape"]
        total_samples = int(shape[0])
        log.debug(
            "Zarr %s total samples (shape[0]): %d", _ZARR_TRACES_ARRAY, total_samples
        )
        return total_samples
    except Exception as exc:
        log.debug("Could not read zarr .zmetadata for sample count: %s", exc)
        return None


def _build_sample_metadata_index(data_root: Path) -> dict:
    """Return a sorted mapping of chunk_timestamp → start_sample from local JSON files.

    Reads every ``*SampleMetadata*.json`` file found under
    ``data_root/ecephys/``, extracts the ``"start_sample"`` field, and
    returns a plain ``dict`` sorted by timestamp string (lexicographic order
    matches chronological order for the ``YYYY-MM-DDTHH-MM-SS`` format).

    Parameters
    ----------
    data_root : Path
        Run-level session directory.

    Returns
    -------
    dict mapping str → int
        ``{chunk_timestamp: start_sample}`` sorted by timestamp.  Empty if
        no SampleMetadata files are found or none can be parsed.
    """
    import json as _json

    raw: dict = {}
    ecephys_dir = data_root / "ecephys"
    if not ecephys_dir.exists():
        return raw
    for jpath in sorted(ecephys_dir.rglob("*SampleMetadata*.json")):
        m = _CHUNK_RE.search(jpath.name)
        if not m:
            continue
        ts = m.group(0)
        try:
            with open(jpath) as f:
                data = _json.load(f)
            start_sample = data.get("start_sample")
            if start_sample is not None:
                raw[ts] = int(start_sample)
        except Exception as exc:
            log.debug("Could not read SampleMetadata %s: %s", jpath, exc)
    # Return sorted by timestamp (lexicographic == chronological for this format)
    return dict(sorted(raw.items()))


# ── Upload job wrapper ────────────────────────────────────────────────────────

class _StoppableSubmitUploadJob:
    """Thin wrapper around :class:`SubmitUploadJob` with stop-signal support.

    Filters out chunks that are already confirmed in S3 or already submitted
    in this session before posting any batch.  Also patches the inner job's
    ``_submit_request`` to raise on HTTP errors and log the response body so
    failures are immediately visible in the conductor log — without modifying
    the upstream ``aind-chronic-ephys-uploader`` package.

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
        # Monkey-patch _submit_request on the instance so the upstream class is
        # unchanged but the conductor gets raise_for_status + ERROR-level logging.
        self._inner._submit_request = self._checked_submit_request

    def _checked_submit_request(self, upload_jobs) -> None:
        """Drop-in replacement for ``SubmitUploadJob._submit_request`` that
        raises on non-2xx responses and logs the full response body at ERROR.

        This is a shim so changes to the upstream package are not required.
        """
        import requests as _requests
        from urllib3.util.retry import Retry
        from requests.adapters import HTTPAdapter
        from aind_data_transfer_service.models.core import SubmitJobRequestV2

        settings = self._inner.job_settings
        log.info("Submitting %d job(s) to transfer service.", len(upload_jobs))
        retry_strategy = Retry(
            total=3,
            backoff_factor=300,
            status_forcelist=[500],
            allowed_methods=["POST"],
        )
        submit_request = SubmitJobRequestV2(
            upload_jobs=upload_jobs, user_email=settings.contact_email
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        endpoint = settings.transfer_service_endpoint.unicode_string()
        request_content = submit_request.model_dump(mode="json", exclude_none=True)

        if not settings.dry_run:
            with _requests.Session() as session:
                session.mount("http://", adapter)
                response = session.post(url=endpoint, json=request_content)
            log.debug("Transfer service response: %d", response.status_code)
            if not response.ok:
                log.error(
                    "Transfer service returned HTTP %d for %d job(s).\n%s",
                    response.status_code,
                    len(upload_jobs),
                    response.text[:4000],
                )
                response.raise_for_status()
        else:
            log.info("DRY RUN: would have sent %s", request_content)

    def run_job(
        self,
        skip_chunks: Optional[Set[str]] = None,
        on_batch_submitted: Optional[Callable[[list[str]], None]] = None,
        on_cadence_tick: Optional[Callable[[], None]] = None,
        cadence_secs: int = 300,
    ) -> list[str]:
        """Run the upload job with duplicate-filtering and stop support.

        Parameters
        ----------
        skip_chunks:
            Additional chunk timestamps to exclude (e.g. already-confirmed or
            sidecar-skipped chunks from a previous run).  Combined with the
            in-process ``_SUBMITTED_CHUNKS`` set and S3-confirmed cloud chunks.
        on_batch_submitted:
            Optional callback invoked immediately after each batch is accepted
            by the transfer service, before the inter-batch sleep begins.
            Receives the list of chunk timestamps in that batch.  Use this to
            update persistent state (e.g. the upload sidecar) after each batch
            rather than waiting for all batches to complete.
        on_cadence_tick:
            Optional callback invoked once every *cadence_secs* seconds during
            the inter-batch sleep.  Use this to run work that must happen on a
            short cadence (e.g. directory consolidation) even while the upload
            cycle is waiting between batches.  Exceptions are caught and logged
            as warnings so they never interrupt the sleep loop.
        cadence_secs:
            How often (in seconds) to fire *on_cadence_tick* during the
            inter-batch sleep.  Default 300 (5 minutes).

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

            # Notify the caller immediately so it can persist state (e.g. update
            # the upload sidecar) before the inter-batch sleep begins.
            if on_batch_submitted is not None:
                try:
                    on_batch_submitted(list(batch))
                except Exception:
                    log.warning(
                        "on_batch_submitted callback raised an exception "
                        "(batch %d/%d); continuing.", idx + 1, total_batches,
                        exc_info=True,
                    )

            if idx < total_batches - 1 and not settings.dry_run:
                wait_secs = settings.time_to_wait_between_batches
                log.info(
                    "Waiting %d s before next batch ...", wait_secs
                )
                elapsed = 0
                next_tick = cadence_secs  # first tick fires after one cadence interval
                while elapsed < wait_secs:
                    if self._stop.is_set():
                        break
                    sleep(1)
                    elapsed += 1
                    if on_cadence_tick is not None and elapsed >= next_tick:
                        next_tick += cadence_secs
                        try:
                            on_cadence_tick()
                        except Exception:
                            log.warning(
                                "on_cadence_tick callback raised an exception "
                                "(batch %d/%d); continuing.",
                                idx + 1,
                                total_batches,
                                exc_info=True,
                            )

        log.info("Upload job finished.")
        return newly_submitted


# ── Transfer-service status helpers ──────────────────────────────────────────

def _query_job_status_for_prefix(
    transfer_service_url: str,
    s3_prefix: str,
    job_type: str = "chronic_ephys_start",
) -> Optional[str]:
    """Query ``/api/v1/get_job_status_list`` for the most recent Airflow run
    matching *s3_prefix* and *job_type*.

    Parameters
    ----------
    transfer_service_url : str
        Full URL of the submit endpoint (e.g.
        ``http://aind-data-transfer-service/api/v2/submit_jobs``).  The path
        is replaced with ``/api/v1/get_job_status_list`` internally.
    s3_prefix : str
        The S3 prefix string for this dataset (the ``name`` field in the
        service's job list response, e.g.
        ``"ecephys_842456_2026-01-01_10-00-00"``).
    job_type : str
        Job type to filter on.  Default ``"chronic_ephys_start"``.

    Returns
    -------
    str or None
        Airflow state string (``"running"``, ``"queued"``, ``"success"``,
        ``"failed"``) for the most recent matching job, or *None* if the
        job cannot be found or the query fails.
    """
    import requests as _req
    from urllib.parse import urlparse, urlunparse

    try:
        parsed = urlparse(transfer_service_url)
        status_url = urlunparse(
            parsed._replace(path="/api/v1/get_job_status_list", query="", fragment="")
        )
        resp = _req.get(status_url, timeout=15)
        if not resp.ok:
            log.debug("Job-status query returned HTTP %d.", resp.status_code)
            return None
        job_list = resp.json().get("data", {}).get("job_status_list", [])
        matching = [
            j for j in job_list
            if j.get("name") == s3_prefix and j.get("job_type") == job_type
        ]
        if not matching:
            return None
        # Most recent first by submit_time
        matching.sort(key=lambda j: j.get("submit_time") or "", reverse=True)
        return matching[0].get("job_state")
    except Exception as exc:
        log.debug("Could not query transfer service job status: %s", exc)
        return None


def query_transfer_job_status(
    s3_prefix: str,
    transfer_service_url: Optional[str] = None,
    job_type: str = "chronic_ephys_start",
) -> Optional[str]:
    """Public wrapper: return the Airflow state for the most recent job.

    If *transfer_service_url* is not supplied, the default from
    :class:`~aind_chronic_ephys_uploader.models.JobSettings` is used
    (``http://aind-data-transfer-service/api/v2/submit_jobs``).

    Parameters
    ----------
    s3_prefix : str
        The S3 prefix for this dataset.
    transfer_service_url : str, optional
        Submit-endpoint URL; path is replaced with the status endpoint.
    job_type : str
        Job type to filter on (default ``"chronic_ephys_start"``).

    Returns
    -------
    str or None
        Airflow state string or *None*.
    """
    if transfer_service_url is None:
        transfer_service_url = (
            "http://aind-data-transfer-service/api/v2/submit_jobs"
        )
    return _query_job_status_for_prefix(transfer_service_url, s3_prefix, job_type)


def query_chronic_ephys_job_statuses(
    s3_prefix: str,
    transfer_service_url: Optional[str] = None,
) -> list[dict]:
    """Return all recent Airflow job records for a chronic-ephys dataset.

    Queries ``/api/v1/get_job_status_list`` and returns every entry whose
    ``name`` field matches *s3_prefix* — the identifier used by
    ``chronic_ephys_start`` and ``chronic_ephys_chunk`` jobs.  Sorted newest
    first.

    Parameters
    ----------
    s3_prefix : str
        The S3 prefix for this dataset (e.g.
        ``"ecephys_842456_2026-01-01_10-00-00"``).  This is the ``name``
        field the transfer service stores in each job's Airflow conf.
    transfer_service_url : str, optional
        Submit-endpoint URL; path is replaced with the status endpoint.

    Returns
    -------
    list of dict
        Each dict has keys ``job_type``, ``job_state``, ``job_id``,
        ``submit_time``, ``start_time``, ``end_time`` (may be None).
        Empty list on error or no matches.
    """
    import requests as _req
    from urllib.parse import urlparse, urlunparse

    if transfer_service_url is None:
        transfer_service_url = (
            "http://aind-data-transfer-service/api/v2/submit_jobs"
        )
    try:
        parsed = urlparse(transfer_service_url)
        status_url = urlunparse(
            parsed._replace(path="/api/v1/get_job_status_list", query="", fragment="")
        )
        resp = _req.get(status_url, timeout=15)
        if not resp.ok:
            log.debug("Job-status query returned HTTP %d.", resp.status_code)
            return []
        job_list = resp.json().get("data", {}).get("job_status_list", [])
        matching = [j for j in job_list if j.get("name") == s3_prefix]
        matching.sort(key=lambda j: j.get("submit_time") or "", reverse=True)
        return matching
    except Exception as exc:
        log.debug("Could not query transfer service job list: %s", exc)
        return []


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
    on_batch_submitted: Optional[Callable[[list[str]], None]] = None,
    on_cadence_tick: Optional[Callable[[], None]] = None,
    cadence_secs: int = 300,
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
    on_batch_submitted : callable, optional
        Invoked immediately after each batch is accepted by the transfer
        service, before the inter-batch sleep.  Receives the list of chunk
        timestamps in that batch.  Use this to persist state after each batch
        rather than waiting for all batches to complete.
    on_cadence_tick : callable, optional
        Invoked once every *cadence_secs* seconds during the inter-batch sleep.
        Use for work that must run on a short cadence (e.g. directory
        consolidation) even while the upload cycle is waiting between batches.
        Exceptions are caught and logged as warnings.
    cadence_secs : int
        Interval in seconds between *on_cadence_tick* calls.  Default 300.

    Returns
    -------
    UploadCycleResult
        ``.success`` is *True* when the job ran without a hard error.
        ``.submitted_chunks`` lists every chunk timestamp submitted in this cycle.
    """
    settings = None  # sentinel — used in except block for status query
    try:
        from aind_chronic_ephys_uploader.models import JobSettings
        from aind_data_schema_models.modalities import Modality

        if modalities is None:
            modalities = [Modality.ECEPHYS, Modality.BEHAVIOR, Modality.BEHAVIOR_VIDEOS]

        # Convert Windows UNC paths (\\server\share\...) to the POSIX double-
        # slash form (//server/share/...) that the Linux transfer workers expect.
        # Applied here so the upstream aind-chronic-ephys-uploader is unmodified.
        linux_source = _to_linux_path(source_directory)
        if linux_source != source_directory:
            log.debug("Source path converted for transfer service: %s", linux_source)

        job_type = "chronic_ephys_start" if is_start_job else "chronic_ephys_chunk"
        settings = JobSettings(
            source_directory=linux_source,
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
        submitted = job.run_job(
            skip_chunks=skip_chunks,
            on_batch_submitted=on_batch_submitted,
            on_cadence_tick=on_cadence_tick,
            cadence_secs=cadence_secs,
        )
        return UploadCycleResult(success=True, submitted_chunks=submitted)

    except FileNotFoundError as exc:
        # "not found in DocDB yet" — start job is still processing (or failed).
        # Query the transfer service so the log shows the actual Airflow state.
        if settings is not None:
            try:
                ts_url = settings.transfer_service_endpoint.unicode_string()
                job_state = _query_job_status_for_prefix(ts_url, settings.s3_prefix)
            except Exception:
                job_state = None
        else:
            job_state = None

        if job_state in ("running", "queued"):
            log.info(
                "Upload cycle waiting — start job is %s on transfer service "
                "(DocDB record not yet written).",
                job_state,
            )
        elif job_state == "success":
            log.warning(
                "Upload cycle waiting — start job reported %s on transfer "
                "service but record not yet in DocDB.  Will retry.",
                job_state,
            )
        elif job_state == "failed":
            log.error(
                "Start job FAILED on the transfer service (s3_prefix=%s).  "
                "Use `conductor-status` → Reset upload state to retry.  "
                "Original error: %s",
                settings.s3_prefix if settings else "unknown",
                exc,
            )
        else:
            # job_state is None — not found in history (still queuing, or too old)
            log.info("Upload cycle waiting: %s", exc)
        return UploadCycleResult(success=False)

    except FileExistsError as exc:
        # "already exists" — DocDB already has the record; chunk jobs should work.
        log.info("Upload cycle waiting: %s", exc)
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


def _upload_ancillary_files(
    data_root: Path,
    upload_dirs: List[Path],
    s3_bucket: str,
    s3_prefix: str,
    already_in_s3: Set[str],
) -> None:
    """Upload non-chunked ancillary files to S3 using authenticated boto3.

    Walks *upload_dirs* and uploads every file whose relative path contains no
    chunk timestamp (``YYYY-MM-DDTHH-MM-SS``) and that is not already present
    in S3 (as recorded in *already_in_s3*).  Examples: ``ecephys/probe.json``,
    ``behavior/metadata/Rule_*``.

    Keep patterns do **not** affect this function — uploading to S3 and keeping
    a file locally are orthogonal concerns.  A file that matches a keep pattern
    (and therefore won't be deleted locally) should still be uploaded so the S3
    copy is complete.

    Requires standard AWS credentials (environment variables, instance profile,
    or ``~/.aws/credentials``).  Failures are logged as warnings; the function
    never raises so the caller's deletion pass always continues.

    Parameters
    ----------
    data_root : Path
        Run-level session directory (files are relative to this).
    upload_dirs : list of Path
        Directories to search.  Typically ``behavior/``, ``behavior-videos/``,
        and ``ecephys/`` — broader than the deletion directories so that
        ancillary files in ``behavior/`` (rule files, metadata, etc.) are also
        captured.
    s3_bucket : str
        Destination S3 bucket.
    s3_prefix : str
        Dataset prefix within the bucket (no trailing slash).
    already_in_s3 : set of str
        Relative keys already present in S3 (from :func:`_list_s3_objects`).
        Files whose relative path is in this set are skipped.
    """
    try:
        import boto3
        client = boto3.client("s3")  # standard credential chain
    except Exception as exc:
        log.warning(
            "Could not create authenticated S3 client for ancillary upload: %s", exc
        )
        return

    uploaded = skipped_existing = 0
    prefix_slash = s3_prefix.rstrip("/") + "/"

    for upload_dir in upload_dirs:
        if not upload_dir.exists():
            continue
        for fpath in sorted(upload_dir.rglob("*")):
            if not fpath.is_file():
                continue
            rel = fpath.relative_to(data_root).as_posix()

            # Only handle files that have no chunk timestamp in their path;
            # chunked files are the transfer service's responsibility.
            if _CHUNK_RE.search(rel):
                continue

            # Skip if already present in S3
            if rel in already_in_s3:
                skipped_existing += 1
                continue

            s3_key = prefix_slash + rel
            try:
                client.upload_file(str(fpath), s3_bucket, s3_key)
                log.info(
                    "Uploaded ancillary file: %s → s3://%s/%s", rel, s3_bucket, s3_key
                )
                uploaded += 1
            except Exception as exc:
                log.warning(
                    "Could not upload ancillary file %s: %s", fpath, exc
                )

    if uploaded or skipped_existing:
        log.info(
            "Ancillary upload: %d uploaded, %d already in S3.",
            uploaded,
            skipped_existing,
        )


def delete_local_files_after_upload(
    data_root: Path,
    keep_patterns: List[str],
    s3_bucket: str,
    subject_id: str,
    acq_datetime: datetime,
) -> None:
    """Delete large local files only after robust per-file S3 confirmation.

    Applies one of three confirmation strategies depending on file type:

    **AmplifierData .bin** (zarr-compressed by the transfer service):
        Multiple independent checks must all pass before deletion:

        1. The AmplifierData zarr ``.zmetadata`` must exist on S3 (zarr
           container is finalised).
        2. The *next* chunk's ``start_sample`` (read from the local
           ``SampleMetadata_T+1.json``) must be ``≤`` the zarr's total
           sample count (``traces_seg0.shape[0]`` from ``.zmetadata``).
           This proves every sample in chunk T has been written to the zarr.
        3. Chunk T must not be the last chunk in the directory — there must
           be a next SampleMetadata to reference.

    **Clock / HubClock .bin** (uploaded as-is to ``ecephys/OnixEphys/``):
        Per-file key existence — the exact relative path must be present
        in the S3 object listing.

    **All other files** (``.mp4``, etc.):
        Per-file key existence (same as Clock/HubClock above).

    Files matching any pattern in *keep_patterns* are always kept.  Non-
    chunked ancillary files (no chunk timestamp in path) are never deleted.

    Parameters
    ----------
    data_root : Path
        Run-level session directory.
    keep_patterns : list of str
        Glob patterns relative to *data_root* for files to always keep.
    s3_bucket : str
        S3 bucket name.
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

    log.info("Querying S3 for confirmed objects before local deletion ...")
    confirmed_chunks, s3_keys = _list_s3_objects(s3_bucket, s3_prefix)

    if not confirmed_chunks:
        log.warning(
            "No chunks confirmed on S3 — skipping local deletion to avoid data loss."
        )
        return

    log.info(
        "%d chunk(s) confirmed in S3 (%d total object(s)); proceeding.",
        len(confirmed_chunks),
        len(s3_keys),
    )

    # delete_dirs: only the directories whose chunked files are candidates for
    # local deletion after confirmed S3 upload.
    delete_dirs = [data_root / "behavior-videos", data_root / "ecephys"]

    # upload_dirs: a superset that also includes behavior/ so that ancillary
    # files there (rule files, task metadata, probe configs, etc.) are uploaded
    # to S3 even though they are never candidates for local deletion.
    upload_dirs = [data_root / "behavior", *delete_dirs]

    # Upload ancillary files (no chunk timestamp) before the deletion sweep so
    # they reach S3 even if the transfer-service jobs don't include them.
    _upload_ancillary_files(
        data_root=data_root,
        upload_dirs=upload_dirs,
        s3_bucket=s3_bucket,
        s3_prefix=s3_prefix,
        already_in_s3=s3_keys,
    )

    # ── Pre-compute AmplifierData zarr verification data ──────────────────────
    # Read the zarr .zmetadata from S3 once and build the local SampleMetadata
    # index once — used for every AmplifierData .bin file in the loop below.
    #
    # zarr_samples: total samples in traces_seg0 (None → zarr not yet finalised).
    # sample_meta_index: {chunk_timestamp: start_sample}, sorted chronologically.
    #
    # The verification formula:  chunk T is fully in the zarr iff
    #   sample_meta_index[T+1] ≤ zarr_samples
    # where T+1 is the next chronological chunk.  If T is the last chunk
    # in the index (no T+1) we always keep — we can never confirm whether
    # data is still being written to the zarr.
    zarr_samples: Optional[int] = _fetch_zarr_sample_count(
        s3_bucket, s3_prefix, s3_keys
    )
    sample_meta_index: dict = _build_sample_metadata_index(data_root)
    if zarr_samples is not None:
        log.debug(
            "Zarr verification ready: %d total samples, %d SampleMetadata entries.",
            zarr_samples,
            len(sample_meta_index),
        )
    else:
        log.debug(
            "Zarr .zmetadata not yet on S3 — all AmplifierData .bin files will be kept."
        )

    # Sorted timestamp list for fast next-chunk lookup
    _sorted_meta_ts: list = sorted(sample_meta_index.keys())

    kept = deleted = skipped_unconfirmed = skipped_ancillary = 0

    for delete_dir in delete_dirs:
        if not delete_dir.exists():
            continue
        for fpath in sorted(delete_dir.rglob("*")):
            if not fpath.is_file():
                continue

            rel = fpath.relative_to(data_root).as_posix()

            # 1. Keep-pattern allowlist — always wins.
            if any(fnmatch.fnmatch(rel, pat) for pat in keep_patterns):
                log.debug("Keeping (keep pattern): %s", rel)
                kept += 1
                continue

            # 2. Non-chunked ancillary files (no timestamp in path) — keep locally.
            #    These were uploaded above by _upload_ancillary_files; we never
            #    delete them because they are not individually tracked by the
            #    transfer service and may be shared across chunks.
            chunk_m = _CHUNK_RE.search(rel)
            if not chunk_m:
                log.debug("Keeping (ancillary — no chunk timestamp): %s", rel)
                skipped_ancillary += 1
                continue
            chunk_ts = chunk_m.group(0)

            # 3. S3 confirmation — strategy depends on file type.
            #
            #    AmplifierData .bin (zarr-compressed on S3):
            #      Multi-check verification using local SampleMetadata JSONs and
            #      the zarr's traces_seg0 total sample count read from .zmetadata.
            #      The file is safe to delete only when:
            #        (a) zarr .zmetadata exists on S3 (zarr is finalised), AND
            #        (b) the NEXT chunk's start_sample ≤ zarr_samples, proving
            #            every sample in THIS chunk has been written to the zarr, AND
            #        (c) there IS a next chunk (last chunk is always kept).
            #
            #    Clock / HubClock .bin (uploaded as-is to ecephys/OnixEphys/):
            #      Per-file key existence check — the exact relative path must
            #      appear in the S3 listing.
            #
            #    All other files (.mp4, etc.):
            #      Per-file key existence check (same as Clock/HubClock).
            if fpath.suffix.lower() in _FORMAT_CONVERTED_EXTENSIONS:
                if _AMPLIFIER_DATA_STEM in fpath.name:
                    # ── AmplifierData zarr verification ────────────────────────
                    # (a) zarr not yet finalised on S3
                    if zarr_samples is None:
                        log.debug(
                            "Keeping AmplifierData .bin"
                            " (zarr not yet finalised on S3): %s",
                            rel,
                        )
                        kept += 1
                        continue

                    # (b) chunk timestamp must be in the SampleMetadata index
                    try:
                        ts_idx = _sorted_meta_ts.index(chunk_ts)
                    except ValueError:
                        log.warning(
                            "Keeping AmplifierData .bin"
                            " (chunk %s not in SampleMetadata index"
                            " — cannot verify zarr coverage): %s",
                            chunk_ts,
                            rel,
                        )
                        kept += 1
                        continue

                    # (c) never delete the last chunk — no next SampleMetadata
                    if ts_idx + 1 >= len(_sorted_meta_ts):
                        log.debug(
                            "Keeping AmplifierData .bin"
                            " (last chunk in directory — no next SampleMetadata): %s",
                            rel,
                        )
                        kept += 1
                        continue

                    # (d) next chunk's start_sample must be ≤ zarr total samples
                    next_ts = _sorted_meta_ts[ts_idx + 1]
                    start_sample_next = sample_meta_index[next_ts]
                    if start_sample_next > zarr_samples:
                        log.debug(
                            "Keeping AmplifierData .bin"
                            " (next chunk start_sample %d > zarr total %d"
                            " — chunk not fully in zarr): %s",
                            start_sample_next,
                            zarr_samples,
                            rel,
                        )
                        kept += 1
                        continue

                    log.debug(
                        "AmplifierData .bin verified"
                        " (next chunk start_sample %d ≤ zarr total %d"
                        " → full chunk in zarr): %s",
                        start_sample_next,
                        zarr_samples,
                        rel,
                    )
                    # Falls through to deletion below.

                else:
                    # ── Clock / HubClock .bin — per-file key check ──────────
                    if rel not in s3_keys:
                        log.debug(
                            "Keeping .bin (file not yet in S3): %s", rel
                        )
                        skipped_unconfirmed += 1
                        continue
                    # Falls through to deletion below.

            else:
                # ── All other files (.mp4, etc.) — per-file key check ───────
                if rel not in s3_keys:
                    log.debug("Keeping (file not yet in S3): %s", rel)
                    skipped_unconfirmed += 1
                    continue
                # Falls through to deletion below.

            try:
                fpath.unlink()
                log.info("Deleted: %s", fpath)
                deleted += 1
            except Exception as exc:
                log.warning("Could not delete %s: %s", fpath, exc)

    log.info(
        "Post-upload deletion: %d deleted, %d kept (pattern), "
        "%d kept (file not in S3), %d kept (ancillary).",
        deleted,
        kept,
        skipped_unconfirmed,
        skipped_ancillary,
    )
