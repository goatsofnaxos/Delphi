# Experiment Conductor

The experiment conductor watches a shared network drive for new acquisition
sessions and automatically orchestrates the full post-acquisition pipeline.
It runs on a local workstation or VM with network access to where the
acquisition computer saves data — it does **not** run on the acquisition
computer and does **not** control Bonsai.

## Architecture

```
Shared network drive
└── <watch_path>/
    └── <subject_id>/
        └── <session_ts>/       ← discovered automatically
            ├── <run_ts>/       ← run dir (Bonsai data here)
            └── <run_ts>/       ← second run dir if Bonsai restarted

conductor (local PC / VM)
├── watcher.py          scan watch paths for new sessions
├── session_manager.py  orchestrate cadence cycles (one worker per subject ID)
│   ├── pipeline_bridge.py   → delphi-data consolidate / pipeline
│   ├── metadata_bridge.py   → metadata_generator (4 AIND JSON files)
│   ├── noise_floor.py       → RMS noise floor from raw ephys data
│   ├── uploader_bridge.py   → aind-chronic-ephys-uploader → S3
│   └── upload_sidecar.py    → per-session chunk history & state recovery
├── pause_control.py    upload pause / resume via sentinel lock file
├── upload_status_cli.py  conductor-status interactive viewer
├── session.py          per-session state machine
└── config.py           .env + CLI configuration
```

## Session lifecycle

Each discovered session passes through the following phases on every cadence
cycle:

| Phase | What happens |
|-------|-------------|
| `DISCOVERED` | Session directory found; waiting for minimum age before processing |
| `CONSOLIDATING` | Merge Bonsai restart run dirs into the earliest; move JSONL metadata to `behavior/metadata/`; normalise ONIX SampleMetadata |
| `METADATA_CHECK` | Check for `subject.json`, `instrument.json`, `acquisition.json`, `procedures.json` |
| `METADATA_GENERATING` | Run `metadata_generator` to produce any missing JSON files |
| `BUILDING` | Run `delphi-data pipeline` if `DelphiController*.jsonl` is present (builds/appends `delphi_dataset.csv` and figures) |
| `NOISE_FLOOR` | Estimate RMS noise floor per channel from raw Open Ephys / SpikeGLX data |
| `UPLOADING` | Submit chunk upload jobs to AIND data-transfer service |
| `ERROR` | Unrecoverable error after `MAX_CONSECUTIVE_ERRORS` failures |

After the UPLOADING phase the session loops back to CONSOLIDATING on the next
cadence cycle (new data may have arrived).

## Supported experiment types

| Type | Consolidation | Dataset build | Metadata | Upload |
|------|:---:|:---:|:---:|:---:|
| `delphi` | ✓ | ✓ (requires DelphiController file) | ✓ | ✓ |
| `pirouette` | ✓ | — | ✓ | ✓ |
| `delphi_pirouette` | ✓ | ✓ | ✓ | ✓ |

## Subject ID filtering

Set `CONDUCTOR_SUBJECT_IDS` to restrict which subject directories the
conductor scans:

```bash
CONDUCTOR_SUBJECT_IDS=842456,842457,842458
```

- Only directories whose name exactly matches one of the listed IDs are
  processed.
- The list is **hot-reloaded** from `.env` on every poll cycle — add or
  remove animals while the conductor is running without restarting it.
- The thread-pool size is automatically set to `len(subject_ids)` so each
  animal gets its own worker.  When no restriction is configured the fallback
  is `CONDUCTOR_MAX_WORKERS` (default 8).

## Upload history sidecar

Every dataset gets a `.upload_history.json` sidecar in its run directory
tracking the full lifecycle of every chunk:

```json
{
  "subject_id": "842456",
  "session_ts": "2026-09-01T10-00-00",
  "chunks": {
    "2026-09-01T10-05-00": {
      "state": "success",
      "retries": 1,
      "confirmed_at": "2026-09-01T11-00-00+00:00",
      "delete_state": "disabled"
    }
  }
}
```

The sidecar also acts as a **state recovery object**: if `conductor_state.json`
is absent after a restart the conductor reads `upload_started` and
`last_upload_run` from the sidecar so it never re-submits a start job on an
already-started dataset.

Chunks that exceed `CONDUCTOR_UPLOAD_MAX_RETRIES` (default 3) are marked
`skipped` and permanently excluded from future submission attempts.

## Pause / resume uploads

Pause all upload submissions interactively through `conductor-status`:

```bash
conductor-status
```

Press **`p`** to pause, **`r`** to resume.  The running conductor checks for
the pause sentinel file at the start of every upload cycle and skips
submissions while it exists.  A warning is printed at conductor startup if a
stale pause file is detected.

See [pause_control](api/pause_control.md) for the underlying mechanism.

## Upload job types

| Cycle | `job_type` | Chunks submitted | Gate |
|-------|-----------|-----------------|------|
| First | `chronic_ephys_start` | Exactly 1 (oldest pending) | ≥ 3 local chunks must exist |
| All subsequent | `chronic_ephys_chunk` | All pending (up to `batch_size` per POST) | None |

## ONIX SampleMetadata normalisation

During the CONSOLIDATING phase the conductor normalises
`ecephys/OnixEphys/OnixEphys_SampleMetadata_*.json` files so that
`start_sample` values are relative to the session start rather than
device-boot time.  A `_normalization_offset.json` sidecar persists the
original offset across restarts so that:

- New chunks arriving after a conductor restart are normalised correctly.
- Already-normalised files are never double-processed.

## Data root resolution

The conductor discovers sessions by scanning each watch path for the pattern:

```
<watch_path>/<subject_id>/<YYYY-MM-DDTHH-MM-SS>/
```

where `<YYYY-MM-DDTHH-MM-SS>` is a Bonsai session timestamp directory
containing at least one run sub-directory.  After consolidation the earliest
run sub-directory becomes the **run dir** — this is where all subsequent
steps write their output.

## Configuration

All settings are loaded from a `.env` file (and/or shell environment),
optionally overridden by CLI flags.

### Minimum required variables

| Variable | Description |
|----------|-------------|
| `CONDUCTOR_WATCH_PATHS` | Comma-separated directories to monitor |
| `CONDUCTOR_PROTOCOL_ID` | AIND protocol ID |
| `CONDUCTOR_INSTRUMENT_ID` | Rig identifier |
| `CONDUCTOR_EXPERIMENT_ROOM` | Physical room |
| `CONDUCTOR_DELPHI_COMPUTER_ID` | Acquisition computer hostname |
| `CONDUCTOR_CONTACT_EMAIL` | Email for upload notifications |
| `CONDUCTOR_PROJECT_NAME` | AIND project name |

### Key optional settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CONDUCTOR_SUBJECT_IDS` | *(all)* | Comma-separated subject ID allowlist; hot-reloaded |
| `CONDUCTOR_MAX_WORKERS` | `8` | Fallback worker count when no subject IDs are configured |
| `CONDUCTOR_EXPERIMENT_TYPE` | `delphi` | Experiment type |
| `CONDUCTOR_PIPELINE_CADENCE_MINUTES` | `60` | Processing interval per session |
| `CONDUCTOR_UPLOAD_MAX_RETRIES` | `3` | Max submission attempts per chunk before skipping |
| `CONDUCTOR_ENABLE_NOISE_FLOOR` | `false` | Estimate ephys noise floor |
| `CONDUCTOR_DRY_RUN` | `false` | No real upload requests |
| `CONDUCTOR_DELETE_AFTER_UPLOAD` | `false` | Remove large local files after S3 confirmation |
| `CONDUCTOR_STATE_FILE` | — | JSON file for state persistence across restarts |
| `CONDUCTOR_POLL_INTERVAL_S` | `60` | Watch-path scan interval (s) |
| `CONDUCTOR_MIN_SESSION_AGE_MINUTES` | `5` | Minimum age before processing |

See `.env.example` in the package source for the full reference.

## Upload safety

Three guarantees prevent data loss or duplicate work:

1. **No duplicate submissions** — `_SUBMITTED_CHUNKS` (module-level,
   thread-safe `set`) tracks every chunk submitted in the current process.
   In-flight chunks are never re-submitted across cadence cycles.

2. **Sidecar-skip integration** — chunks already confirmed in S3 or exceeding
   the retry limit are excluded from every upload cycle via the
   `.upload_history.json` sidecar.

3. **Confirmed-before-delete** — `delete_local_files_after_upload` queries S3
   directly before removing any local file.  Only chunks confirmed present in
   the bucket are eligible for deletion.

## Quick start

```bash
cp .env.example .env
# Edit .env — set CONDUCTOR_WATCH_PATHS, protocol/instrument/room IDs, etc.

conductor                                  # start watching
conductor --add-session /path/to/session   # register a specific session
conductor --dry-run                        # test mode — no real uploads
conductor --subject-ids 842456,842457      # restrict to specific animals

conductor-status                           # view upload progress; pause/resume
```
