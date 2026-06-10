# uploader_bridge

S3 upload job submission with pause/resume and duplicate-submission protection.

Key guarantees:

- **No duplicate submissions** — `_SUBMITTED_CHUNKS` tracks every chunk POSTed
  this session; in-flight chunks are excluded from subsequent cycles.
- **Confirmed-before-delete** — `delete_local_files_after_upload` queries S3
  directly before removing any file; chunks not yet confirmed are left untouched.

::: experiment_conductor.uploader_bridge
