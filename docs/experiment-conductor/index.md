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
├── watcher.py        scan watch paths for new sessions
├── session_manager.py orchestrate cadence cycles (up to 4 sessions in parallel)
│   ├── pipeline_bridge.py  → delphi-data consolidate / pipeline
│   ├── metadata_bridge.py  → metadata_generator (4 AIND JSON files)
│   ├── noise_floor.py      → RMS noise floor from raw ephys data
│   └── uploader_bridge.py  → aind-chronic-ephys-uploader → S3
├── session.py        per-session state machine
└── config.py         .env + CLI configuration
```

## Session lifecycle

Each discovered session passes through the following phases on every cadence cycle:

| Phase | What happens |
|-------|-------------|
| `DISCOVERED` | Session directory found; waiting for minimum age before processing |
| `CONSOLIDATING` | Merge Bonsai restart run dirs into the earliest; move JSONL metadata files to `behavior/metadata/` |
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

## Data root resolution

The conductor discovers sessions by scanning each watch path for the pattern::

    <watch_path>/<subject_id>/<YYYY-MM-DDTHH-MM-SS>/

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
| `CONDUCTOR_EXPERIMENT_TYPE` | `delphi` | Experiment type |
| `CONDUCTOR_PIPELINE_CADENCE_MINUTES` | `60` | Processing interval per session |
| `CONDUCTOR_ENABLE_NOISE_FLOOR` | `false` | Estimate ephys noise floor |
| `CONDUCTOR_DRY_RUN` | `false` | No real upload requests |
| `CONDUCTOR_STATE_FILE` | — | JSON file for state persistence |
| `CONDUCTOR_POLL_INTERVAL_S` | `60` | Watch-path scan interval (s) |
| `CONDUCTOR_MIN_SESSION_AGE_MINUTES` | `5` | Minimum age before processing |

See `.env.example` for the full reference.

## Upload safety

Two guarantees prevent data loss:

1. **No duplicate submissions** — `_SUBMITTED_CHUNKS` (module-level, thread-safe)
   tracks every chunk submitted in the current process.  In-flight chunks are
   never re-submitted across cadence cycles.

2. **Confirmed-before-delete** — `delete_local_files_after_upload` queries S3
   directly before removing any file.  Only chunks confirmed present in the
   bucket are eligible for local deletion.

## Quick start

```bash
cp .env.example .env
# Edit .env — set CONDUCTOR_WATCH_PATHS, protocol/instrument/room IDs, etc.

conductor                              # start watching
conductor --add-session /path/to/session   # register a specific session
conductor --dry-run                    # test mode — no real uploads
```
