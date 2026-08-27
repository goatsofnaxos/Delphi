# uploader_bridge

S3 upload job submission with duplicate-submission protection.

Two safety guarantees:

1. **No duplicate submissions** — `_SUBMITTED_CHUNKS` (thread-safe `set`)
   tracks every chunk submitted this process run.  In-flight chunks are
   never re-submitted across cadence cycles, even though they are not yet
   visible in S3.

2. **Confirmed-before-delete** — `delete_local_files_after_upload` queries S3
   before touching any local file.  Only chunks confirmed present in the
   bucket are eligible for deletion.

::: experiment_conductor.uploader_bridge
