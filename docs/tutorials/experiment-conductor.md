# Tutorial: experiment-conductor

The conductor wraps the launcher, `delphi-data`, `metadata-generator`, and the S3 uploader into a single supervised process for live chronic-recording sessions.  Start it before the experiment, and it handles data processing, metadata generation, and upload automatically until you signal end-of-experiment.

---

## Prerequisites

All four packages must be installed and accessible in the same Python environment:

```bash
cd src/experiment_conductor
uv sync
```

Confirm the other packages are reachable:

```bash
python -c "import delphi_data, metadata_generator, launcher; print('ok')"
```

---

## Step 1 — Configure `.env`

Copy the example and fill in your session details:

```bash
cp src/experiment_conductor/.env.example src/experiment_conductor/.env
```

Minimum required fields:

```ini
EXPERIMENT_TYPE=delphi_pirouette
EXPERIMENT_CONFIG=delphi_pirouette_experiment   # launcher profile name

# Path to server root (data_root is resolved automatically after launcher exits)
SERVER_ROOT=\allen\aind\stage\chronic
SUBJECT_ID=842456

# Metadata inputs
PROTOCOL_ID=DRn20231002
INSTRUMENT_ID=delphi-rig-0
EXPERIMENT_ROOM=447
EXPERIMENTERS=B. Pratt
SURGEONS=John Smith
SURGERY_NOTES_BASE=\allen\aind\scratch\chronos\surgeryNotes

# delphi-data pipeline
DELPHI_EXPERIMENT=bonhoeffer
DELPHI_FIRMWARE=0.1.0

# Upload
S3_BUCKET=aind-open-data
CONTACT_EMAIL=brandon.pratt@alleninstitute.org
PROJECT_NAME=Chronos
```

Leave `SESSION_DATETIME` blank — the conductor detects the newest session directory automatically after the launcher exits.

---

## Step 2 — Start the conductor

```bash
cd src/experiment_conductor
uv run scripts/run_conductor.py
```

The conductor transitions through four states:

```
LAUNCHING ──► RUNNING ──► ENDING ──► DONE
```

### LAUNCHING

The launcher opens (`launcher.py --experiment <EXPERIMENT_CONFIG>`).  You complete the subject-ID and experimenter prompts in the launcher's terminal interface, then Bonsai starts.  The conductor blocks here until Bonsai exits.

After Bonsai finishes launching, the conductor resolves `data_root` (polling up to 10 minutes for robocopy to create the session directory on the server).

### RUNNING

A cadence scheduler fires a **cycle** every `PIPELINE_CADENCE_MINUTES` minutes (default 60), or at a fixed `SCHEDULE_MINUTE_OF_HOUR` if set.  The first cycle fires immediately.

Each cycle performs four steps in order:

| Step | What happens |
|------|-------------|
| **a. Pipeline** | Runs `delphi-data pipeline --append` — consolidates run dirs, rebuilds the behavioral dataset with new Harp data, saves snapshot figures. |
| **b. Move metadata** | Moves `HardwareSettings`/`RuleSettings` JSONL files to `behavior/metadata/` (once only). |
| **c. Generate AIND metadata** | Writes `subject.json`, `instrument.json`, `acquisition.json`, `procedures.json` to `metadata/` (once only; skipped on subsequent cycles). |
| **d. Upload** | Submits camera-video chunks to the AIND transfer service once ≥ 3 chunks exist and all four metadata files are present. |

For **pirouette-only** experiments the pipeline step is replaced by a single `delphi-data consolidate` call (run once).  Step b is skipped because there are no Delphi controller metadata files.

---

## Step 3 — Monitor progress

The conductor logs all activity to stdout.  Key log lines:

```
INFO  Cadence cycle starting (cycle #3) ...
INFO  delphi-data pipeline completed successfully.
INFO  AIND metadata written to /data/842456/2026-03-20T20-23-05/metadata
INFO  Submitted upload job for chunk_0 (job_id=abc123)
```

Use the runtime hotkeys to interact without stopping the process:

| Hotkey | Action |
|--------|--------|
| `Ctrl+Shift+P` | Trigger a pipeline cycle immediately |
| `Ctrl+Shift+U` | Pause / resume upload |
| `Ctrl+Shift+E` | Signal end-of-experiment |
| `Ctrl+Shift+T` | Update acquisition end time (prompts for HH:MM) |
| `Ctrl+Shift+R` | Retry metadata generation on the next cycle |
| `Ctrl+Shift+1` | Toggle pipeline on / off |
| `Ctrl+Shift+2` | Toggle metadata generation on / off |
| `Ctrl+Shift+3` | Toggle upload on / off |

---

## Step 4 — Signal end-of-experiment

Press **Ctrl+Shift+E** (or the key mapped to `HOTKEY_END_EXPERIMENT`) when the experiment is over.  The conductor:

1. Shows the current end time (if already set by `Ctrl+Shift+T`).
2. Prompts you to confirm or enter a new end time (`HH:MM` in local time, or press Enter to use now).
3. For pirouette experiments, verifies that `probe.json` exists in `ecephys/`.
4. Waits for any in-flight cycle to finish.
5. Runs one final cycle to capture the last data.
6. Updates `acquisition_end_time` in `acquisition.json`.
7. Stops the upload cleanly.

---

## Step 5 — Optional: delete large local files

When `DELETE_AFTER_UPLOAD=true` is set, the conductor deletes `behavior-videos/` and `ecephys/` from the local server after each chunk is confirmed present on S3.  Files matching `KEEP_LOCAL_PATTERNS` are never deleted:

```
behavior/delphi_dataset.csv
behavior/DelphiController/**
behavior/results/**
metadata/**
```

You are prompted to confirm deletion at the end of the run if `DELETE_AFTER_UPLOAD=true`.

---

## Dry-run mode

Set `DRY_RUN=true` (or pass `--dry-run`) to run the full state machine without actually submitting upload jobs:

```bash
uv run scripts/run_conductor.py --dry-run
```

---

## Pirouette-specific notes

- Set `EXPERIMENT_TYPE=pirouette` (or `delphi_pirouette`).
- `probe.json` must be present in `ecephys/` before end-of-experiment is signalled.  The conductor warns if it is missing.
- The CCFv3 `ProbeConfig` (atlas coordinates, BREGMA_ARI coordinate system, chronic implant note) is extracted from `acquisition.json` automatically and written into `procedures.json`.
- The delphi pipeline step is skipped for `pirouette`-only experiments; `delphi-data consolidate` runs once instead.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Conductor can't find `data_root` | Check that robocopy is running and that `SERVER_ROOT` and `SUBJECT_ID` are set correctly.  The conductor polls for up to 10 minutes. |
| `delphi-data pipeline` exits non-zero | Check that `DELPHI_EXPERIMENT` and `DELPHI_FIRMWARE` match valid values for the session. |
| Metadata generation fails | Confirm VPN/network is active (for subject fetch), that `SURGERY_NOTES_BASE` points to the correct share, and that `behavior/metadata/` contains the JSONL files. |
| Upload not starting | Check that `S3_BUCKET`, `CONTACT_EMAIL`, and `PROJECT_NAME` are set, and that ≥ 3 camera-video chunks exist under `CHUNK_CAMERA_FOLDER`. |
| `probe.json` warning at end | Copy the Neuropixels `probe.json` file from the acquisition computer to `ecephys/` on the server before signalling end. |
