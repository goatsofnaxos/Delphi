# upload_sidecar

Per-session upload history — written to `<run_dir>/.upload_history.json`.

Tracks the full lifecycle of every chunk for a dataset: submission attempts,
S3 confirmation, local deletion, retry count, and any errors.  The sidecar
is the authoritative source of truth for upload progress and doubles as a
**state-recovery object** — after a conductor restart the sidecar can
reconstruct `upload_started` and `last_upload_run` even when
`conductor_state.json` is absent or stale.

## Chunk states

| State | Meaning |
|-------|---------|
| `pending` | Chunk exists locally; never submitted |
| `submitted` | Submission sent to transfer service; S3 confirmation pending |
| `success` | Chunk confirmed present in S3 |
| `skipped` | Chunk exceeded `CONDUCTOR_UPLOAD_MAX_RETRIES`; will not be retried |
| `failed` | Hard error recorded in `errors` list |

## Delete states

| State | Meaning |
|-------|---------|
| `disabled` | `CONDUCTOR_DELETE_AFTER_UPLOAD` is off; local files are kept |
| `pending` | Chunk is in S3 but local delete sweep has not run yet |
| `success` | Local large files for this chunk have been removed |
| `failed` | Deletion was attempted but at least one file could not be removed |

::: experiment_conductor.upload_sidecar
