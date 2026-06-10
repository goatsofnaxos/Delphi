# Experiment Conductor

Orchestrates the full Delphi experiment workflow: launch → processing → metadata generation → S3 upload.

## Purpose

The conductor ties together four sibling packages into a single supervised session loop:

1. **Launcher** — spawns Bonsai workflows via the launcher subprocess.
2. **delphi-data pipeline** — runs data consolidation, dataset build, and behavioral snapshot on a configurable cadence.
3. **metadata-generator** — builds AIND-compliant JSON metadata once Delphi hardware/settings files are available.
4. **aind-chronic-ephys-uploader** — submits `chronic_ephys_start` / `chronic_ephys_chunk` jobs to `aind-data-transfer-service`.

## Usage

```bash
# From the experiment_conductor directory
uv run scripts/run_conductor.py

# Or with explicit overrides (all .env keys can be passed as flags)
uv run scripts/run_conductor.py \
    --data-root /data/subject_id/2026-01-01T00-00-00 \
    --subject-id 123456 \
    --experiment-type delphi_pirouette \
    --dry-run
```

All options can also be set in a `.env` file — copy `.env.example` to `.env` and fill in the values.
CLI flags **always override** `.env` values.

## .env Keys

| Key | Description |
|-----|-------------|
| `EXPERIMENT_TYPE` | `delphi`, `pirouette`, or `delphi_pirouette` |
| `EXPERIMENT_CONFIG` | Launcher profile name (resolved in `experiment_configs/`) |
| `DATA_ROOT` | **Required.** Run-level session directory |
| `LAUNCHER_DIR` | Absolute path to launcher directory |
| `SURGERY_NOTES_BASE` | Base path for surgery notes (subject subfolder appended automatically) |
| `SUBJECT_ID` | **Required.** Mouse subject ID |
| `PROTOCOL_ID` | AIND protocol ID |
| `INSTRUMENT_ID` | Instrument identifier |
| `EXPERIMENT_ROOM` | Physical room number |
| `DELPHI_COMPUTER_ID` | Hostname of the Delphi acquisition computer |
| `SURGEONS` | Comma-separated surgeon names |
| `EXPERIMENTERS` | Comma-separated experimenter names |
| `ACQUISITION_TYPE` | Passed to metadata-generator |
| `DELPHI_EXPERIMENT` | Experiment name for delphi-data (e.g. `bonhoeffer`) |
| `DELPHI_FIRMWARE` | Firmware version string (e.g. `1.0.0`) |
| `PIPELINE_CADENCE_MINUTES` | How often to run the processing + upload cycle (default: 60) |
| `S3_BUCKET` | S3 bucket name (default: `aind-open-data`) |
| `CONTACT_EMAIL` | Contact email for upload job notifications |
| `PROJECT_NAME` | AIND project name |
| `DRY_RUN` | `true` to print upload requests without submitting |
| `DELETE_AFTER_UPLOAD` | `true` to delete large local files after confirmed upload |
| `KEEP_LOCAL_PATTERNS` | Comma-separated glob patterns to keep locally even when deleting |

## Hotkeys

| Hotkey | Action |
|--------|--------|
| `Ctrl+Shift+P` | Trigger a pipeline cycle immediately |
| `Ctrl+Shift+U` | Pause / resume upload batches |
| `Ctrl+Shift+E` | Signal experiment end |
| `Ctrl+C` | Emergency exit |

Hotkeys are configurable via `HOTKEY_PIPELINE`, `HOTKEY_UPLOAD_PAUSE`, and `HOTKEY_END_EXPERIMENT` in `.env`.

## Upload Phases

1. **`chronic_ephys_start`** — submitted once, when AIND metadata is ready AND ≥ 3 local chunks exist. Sends the first chunk plus the `metadata/` directory to DocDB.
2. **`chronic_ephys_chunk`** — submitted on every subsequent cadence cycle, uploading pending chunks in batches.
3. **Final cycle** — triggered automatically at experiment end; uploads any remaining chunks.

Between batches the conductor respects a pause event (Ctrl+Shift+U) and a stop event (set at experiment end) so uploads can be halted cleanly without data loss.

## Development

```bash
cd src/experiment_conductor
uv venv --python 3.12
uv pip install -e ".[dev]" \
    -e "../delphi-data[all]" \
    -e "../metadata_generator" \
    -e "../launcher" \
    -e "path/to/aind-chronic-ephys-uploader"
```
