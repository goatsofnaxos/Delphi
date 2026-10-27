# uploader_bridge

S3 upload job submission with duplicate-submission protection and sidecar
integration.

Three safety guarantees:

1. **No duplicate submissions** — `_SUBMITTED_CHUNKS` (module-level,
   thread-safe `set`) tracks every chunk submitted in the current process.
   In-flight chunks are never re-submitted across cadence cycles even though
   they are not yet visible in S3.

2. **Sidecar-skip integration** — `run_upload_cycle` accepts a
   `skip_chunks` set supplied by
   [`UploadSidecar`](upload_sidecar.md#experiment_conductor.upload_sidecar.UploadSidecar).
   Chunks that already succeeded, were skipped, or exceeded the retry limit
   are excluded before any POST request is formed.

3. **Confirmed-before-delete** — `delete_local_files_after_upload` queries
   S3 directly before removing any local file.  Only chunks confirmed present
   in the bucket are eligible for deletion.

## Upload job types

| `is_start_job` | `job_type` | Chunks submitted | When |
|:-:|---|---|---|
| `True` | `chronic_ephys_start` | Exactly **1** (oldest pending) | First upload cycle per dataset; gates on ≥ 3 local chunks |
| `False` | `chronic_ephys_chunk` | All pending (up to `batch_size` per POST) | Every subsequent cycle |

::: experiment_conductor.uploader_bridge
