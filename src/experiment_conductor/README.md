# experiment-conductor

Network-drive watcher that orchestrates post-acquisition processing for
Delphi and Pirouette sessions.  Run on any machine (local workstation or VM)
with access to the shared drive where the acquisition computer saves data.

## What it does

The conductor polls one or more **watch paths** for new session directories
and runs each session through a five-step cadence:

| Step | Action |
|------|--------|
| Consolidate | Merge Bonsai restart sub-directories into the earliest run dir; relocate `HardwareSettings` / `RuleSettings` JSONL files to `behavior/metadata/` |
| Metadata check | Verify `subject.json`, `instrument.json`, `acquisition.json`, `procedures.json` are present; generate them via `metadata_generator` if not |
| Build dataset | Run `delphi-data pipeline` if a `DelphiController*.jsonl` file is present (creates / appends `delphi_dataset.csv` and figures) |
| Noise floor | *(optional)* Estimate the RMS noise floor per channel from raw Open Ephys or SpikeGLX data; write `ecephys/noise_floor.json` |
| Upload | Submit chunk upload jobs to the AIND data-transfer service via `aind-chronic-ephys-uploader` |

Sessions are processed concurrently (up to 4 at once) and state is persisted
to a JSON file so work survives restarts.

## Quick start

```bash
# 1. Copy and edit the config
cp .env.example .env

# 2. Install (from the Delphi repo root)
uv pip install -e "src/experiment_conductor"

# 3. Run
conductor
```

Watch paths and specific sessions can also be passed on the command line:

```bash
# Monitor a network share
conductor --watch-paths "\\\\server\\data\\chronic"

# Or register a specific session directly (subject ID inferred from parent dir)
conductor --add-session "\\\\server\\data\\chronic\\842456\\2026-03-20T20-23-05"
```

## Directory layout expected

```
<watch_path>/
└── <subject_id>/                     # e.g. 842456
    └── <YYYY-MM-DDTHH-MM-SS>/        # session root (e.g. 2026-03-20T20-23-05)
        ├── <YYYY-MM-DDTHH-MM-SS>/    # run dir (Bonsai writes here)
        │   ├── behavior/
        │   │   └── DelphiController_*.jsonl
        │   └── behavior-videos/
        │       └── TopCamera/
        │           └── <chunk_ts>/
        └── <YYYY-MM-DDTHH-MM-SS>/    # second run dir (if Bonsai restarted)
```

After consolidation, all run dirs are merged into the earliest one.

## Configuration

All settings are read from `.env` (and/or shell environment) with optional
CLI overrides.  Copy `.env.example` and fill in your values.

### Minimum required fields

| Variable | Description |
|----------|-------------|
| `CONDUCTOR_WATCH_PATHS` | Comma-separated list of root directories to monitor |
| `CONDUCTOR_PROTOCOL_ID` | AIND protocol ID |
| `CONDUCTOR_INSTRUMENT_ID` | Rig identifier |
| `CONDUCTOR_EXPERIMENT_ROOM` | Physical room identifier |
| `CONDUCTOR_DELPHI_COMPUTER_ID` | Acquisition computer hostname |
| `CONDUCTOR_CONTACT_EMAIL` | Email for upload job notifications |
| `CONDUCTOR_PROJECT_NAME` | AIND project name |

### Key settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CONDUCTOR_EXPERIMENT_TYPE` | `delphi` | `delphi` / `pirouette` / `delphi_pirouette` |
| `CONDUCTOR_ENABLE_PIPELINE` | `true` | Run delphi-data processing |
| `CONDUCTOR_PIPELINE_CADENCE_MINUTES` | `60` | How often to process each session |
| `CONDUCTOR_ENABLE_METADATA` | `true` | Generate AIND metadata JSON files |
| `CONDUCTOR_ENABLE_NOISE_FLOOR` | `false` | Estimate ephys noise floor |
| `CONDUCTOR_ENABLE_UPLOAD` | `true` | Submit S3 upload jobs |
| `CONDUCTOR_DRY_RUN` | `false` | Print upload requests without submitting |
| `CONDUCTOR_STATE_FILE` | — | Path for persisting session states |
| `CONDUCTOR_POLL_INTERVAL_S` | `60` | Watch-path scan interval (seconds) |

See `.env.example` for the full list with descriptions.

## Adding new sessions at runtime

The conductor automatically picks up new session directories that appear
under any configured watch path.  You can also register a specific directory
immediately without restarting:

```bash
# Add a single session on the fly (keep the existing conductor running and
# run this in a second terminal):
conductor --add-session "\\\\server\\data\\842456\\2026-04-15T09-30-00"
```

Or just add the subject's directory to `CONDUCTOR_WATCH_PATHS` — the next
poll will discover all sessions under it automatically.

## Session lifecycle

```
DISCOVERED → CONSOLIDATING → METADATA_CHECK → METADATA_GENERATING
           → BUILDING → NOISE_FLOOR → UPLOADING → (loops back to CONSOLIDATING)
```

Sessions with persistent errors are marked `ERROR` after
`CONDUCTOR_MAX_CONSECUTIVE_ERRORS` (default 5) consecutive failures and
skipped until the underlying issue is resolved.

## Development install

```bash
uv pip install -e "src/experiment_conductor[dev]"
pytest src/experiment_conductor/tests/
```
