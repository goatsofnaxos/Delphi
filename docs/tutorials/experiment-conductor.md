# Experiment Conductor — Complete Tutorial

The experiment conductor watches a shared network drive for Delphi / Pirouette
acquisition sessions and automatically orchestrates everything that happens
after Bonsai stops recording: directory consolidation, AIND metadata
generation, behavioral dataset building, ephys noise-floor estimation, and
S3 upload.

It runs on a **local workstation or VM** with network access to the drive
where the acquisition computer saves data.  It does **not** launch or control
Bonsai and does **not** need to run on the acquisition computer.

---

## 0. Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.11+ | `uv` is recommended for environment management |
| Network share access | Read/write to the drive where data is saved |
| AIND VPN access | Required for subject metadata lookups (`subject.json`) |
| Surgery notes DOCX | `\\allen\aind\scratch\chronos\surgeryNotes\<subject_id>\<subject_id>_craniotomy-implantation.docx` |
| `aind-chronic-ephys-uploader` | Installed editable or on your Python path |

Install all Delphi packages from the repo root:

```bash
uv sync
uv pip install -e "src/experiment_conductor"
```

Verify the install:

```bash
conductor --help
```

---

## 1. Understanding the directory layout

The conductor expects data saved in this structure on the network drive:

```
<watch_path>/
└── <subject_id>/                           # e.g. 842456
    └── 2026-03-20T20-23-05/                # session root (YYYY-MM-DDTHH-MM-SS)
        ├── 2026-03-20T20-23-37/            # run dir — Bonsai writes here
        │   ├── behavior/
        │   │   ├── DelphiController/       # Harp binary data (.bin files)
        │   │   │   ├── DelphiController_32_2026-03-20T20-00-00.bin
        │   │   │   └── ...
        │   │   ├── HardwareSettings_*.jsonl
        │   │   ├── RuleSettings_*.jsonl
        │   │   └── metadata/               # created by conductor
        │   │       ├── HardwareSettings_*.jsonl  (moved here)
        │   │       └── RuleSettings_*.jsonl      (moved here)
        │   ├── behavior-videos/
        │   │   └── TopCamera/
        │   │       ├── 2026-03-20T20-23-37/  # chunk dirs (one per recording segment)
        │   │       ├── 2026-03-20T21-23-37/
        │   │       └── ...
        │   ├── ecephys/                    # Pirouette/combined sessions only
        │   │   ├── continuous.dat          # Open Ephys data
        │   │   └── noise_floor.json        # written by conductor
        │   └── metadata/                   # written by conductor
        │       ├── subject.json
        │       ├── instrument.json
        │       ├── acquisition.json
        │       └── procedures.json
        └── 2026-03-20T21-45-00/            # second run dir (if Bonsai restarted)
            └── behavior/
                └── ...
```

**Key points:**

- The **session root** (e.g. `2026-03-20T20-23-05`) is what the conductor
  discovers.  Its name is the timestamp when the session was started.
- Inside the session root, one or more **run dirs** may exist — one per Bonsai
  launch.  If the experimenter stopped and restarted Bonsai mid-session, there
  will be two.
- The conductor consolidates multiple run dirs into the **earliest** one on
  every cycle.

---

## 2. Configure `.env`

The conductor reads all settings from a `.env` file in whichever directory
you run it from (or from the environment).

```bash
cd src/experiment_conductor
cp .env.example .env
```

Then open `.env` in a text editor.  The groups below are presented in the
order you'll typically fill them in.

### 2a. Watch paths (required first)

```dotenv
# Comma-separated list of root directories to monitor.
# The conductor scans <watch_path>/<subject_id>/<session_ts>/ automatically.
# UNC paths work on Windows.
CONDUCTOR_WATCH_PATHS=\\allen\aind\stage\chronic

# To monitor multiple roots:
# CONDUCTOR_WATCH_PATHS=\\allen\aind\stage\chronic,D:\local_data\chronic
```

### 2b. Experiment identity

```dotenv
# Experiment type: delphi | pirouette | delphi_pirouette
CONDUCTOR_EXPERIMENT_TYPE=delphi

# Acquisition type used in acquisition.json
# (typically "ChronicRecording" for Delphi/Pirouette)
CONDUCTOR_ACQUISITION_TYPE=ChronicRecording

# AIND protocol ID
CONDUCTOR_PROTOCOL_ID=2115

# Rig identifier (must match an AIND instrument record)
CONDUCTOR_INSTRUMENT_ID=323_EPHYS1_OPTO_20240223

# Physical experiment room
CONDUCTOR_EXPERIMENT_ROOM=323

# Hostname of the acquisition computer (used for instrument.json)
CONDUCTOR_DELPHI_COMPUTER_ID=W10DT714023

# Comma-separated names
CONDUCTOR_SURGEONS=Scientist A
CONDUCTOR_EXPERIMENTERS=Scientist B

# Base path for surgery notes DOCX files.
# The conductor appends: <base>/<subject_id>/<subject_id>_craniotomy-implantation.docx
CONDUCTOR_SURGERY_NOTES_BASE=\\allen\aind\scratch\chronos\surgeryNotes
```

### 2c. delphi-data pipeline

```dotenv
# Experiment name for the snapshot step (must match an entry in snapshots/REGISTRY)
CONDUCTOR_DELPHI_EXPERIMENT=bonhoeffer

# Harp firmware version (determines which registers are read)
CONDUCTOR_DELPHI_FIRMWARE=0.1.0

# Enable the pipeline (build-dataset + snapshot)
CONDUCTOR_ENABLE_PIPELINE=true

# How often to run a processing cycle per session (minutes)
# Each session is processed independently on this cadence.
CONDUCTOR_PIPELINE_CADENCE_MINUTES=60

# Skip individual pipeline steps to save time:
CONDUCTOR_PIPELINE_SKIP_BUILD=false      # false = run build-dataset
CONDUCTOR_PIPELINE_SKIP_CLIPS=true       # true  = skip poke-clip extraction (slow)
CONDUCTOR_PIPELINE_SKIP_SNAPSHOT=false   # false = run snapshot (figures)
```

### 2d. Metadata generation

```dotenv
# Enable AIND metadata JSON generation
CONDUCTOR_ENABLE_METADATA=true
```

When enabled the conductor generates `subject.json`, `instrument.json`,
`acquisition.json`, and `procedures.json` the first time it sees a session
whose `metadata/` directory is incomplete.  Subsequent cycles skip this step
unless files are manually deleted.

!!! note "VPN required for subject.json"
    `subject.json` is fetched from the AIND metadata service.  You must be
    on the AIND VPN.  If the service is unavailable, the conductor falls back
    to a minimal stub and logs a warning.

### 2e. Ephys noise-floor estimation

```dotenv
# Enable RMS noise-floor estimation from raw ephys data
CONDUCTOR_ENABLE_NOISE_FLOOR=false    # set true for Pirouette / combined sessions

# How many seconds of data to use (from the start of the recording)
CONDUCTOR_NOISE_FLOOR_N_SECONDS=10.0

# Optional: limit to first N channels (0 = all channels)
CONDUCTOR_NOISE_FLOOR_MAX_CHANNELS=0
```

See [Section 6](#6-noise-floor-estimation) for full details.

### 2f. S3 upload

```dotenv
# Enable S3 upload
CONDUCTOR_ENABLE_UPLOAD=true

# Target S3 bucket
CONDUCTOR_S3_BUCKET=aind-open-data

# Email for upload-job status notifications
CONDUCTOR_CONTACT_EMAIL=you@alleninstitute.org

# AIND project name
CONDUCTOR_PROJECT_NAME=DynamicRoutingTask

# Chunks submitted per upload POST request
CONDUCTOR_UPLOAD_BATCH_SIZE=2

# Skip the N most-recent chunks (avoid uploading data still being written)
CONDUCTOR_NUM_LAST_CHUNKS_TO_IGNORE=2

# Print upload requests but do not submit them (useful for testing)
CONDUCTOR_DRY_RUN=false

# Delete large local files after confirming S3 upload
CONDUCTOR_DELETE_AFTER_UPLOAD=false

# Always keep these files locally (glob patterns relative to run dir)
CONDUCTOR_KEEP_LOCAL_PATTERNS=behavior/delphi_dataset.csv,behavior/DelphiController/**,behavior/results/**,behavior/metadata/**
```

### 2g. Polling and state

```dotenv
# How often to scan for new sessions (seconds)
CONDUCTOR_POLL_INTERVAL_S=60.0

# Don't process a session until it's this old (minutes)
# Gives the acquisition computer time to finish writing before the first cycle.
CONDUCTOR_MIN_SESSION_AGE_MINUTES=5.0

# After this many consecutive errors, mark a session ERROR (requires manual fix)
CONDUCTOR_MAX_CONSECUTIVE_ERRORS=5

# After an error, back off for this many minutes before retrying
CONDUCTOR_ERROR_BACKOFF_MINUTES=30.0

# Persist session state here so work survives restarts (leave blank to disable)
CONDUCTOR_STATE_FILE=conductor_state.json
```

---

## 3. Start the conductor

```bash
conductor
```

You'll see a startup summary, then the conductor enters its polling loop:

```
2026-03-20T21:00:00  INFO  …cli              === Experiment Conductor ===
2026-03-20T21:00:00  INFO  …cli                Experiment type : delphi
2026-03-20T21:00:00  INFO  …cli                Watch paths     : 1 configured
2026-03-20T21:00:00  INFO  …cli                  \\allen\aind\stage\chronic
2026-03-20T21:00:00  INFO  …cli                Cadence         : 60 min
2026-03-20T21:00:00  INFO  …cli                S3 bucket       : aind-open-data
2026-03-20T21:00:00  INFO  …cli                Dry run         : False
2026-03-20T21:00:00  INFO  …cli                Pipeline        : ON  (build=on, clips=skip, snapshot=on)
2026-03-20T21:00:00  INFO  …cli                Metadata        : ON
2026-03-20T21:00:00  INFO  …cli                Noise floor     : OFF
2026-03-20T21:00:00  INFO  …cli                Upload          : ON
2026-03-20T21:00:00  INFO  …session_manager  Session manager started. Watching 1 path(s). Poll interval: 60 s. Cadence: 60 min.
```

Press `Ctrl+C` to stop gracefully (current cycle finishes, then conductor exits).

### Useful CLI flags

```bash
conductor --dry-run               # test run: all steps except real upload POSTs
conductor --log-level DEBUG       # verbose output (shows every directory scan)
conductor --state-file /path/to/state.json   # override CONDUCTOR_STATE_FILE
conductor --watch-paths "D:\data" # override CONDUCTOR_WATCH_PATHS from CLI
```

---

## 4. What happens — step by step

### 4a. Session discovery

Every `CONDUCTOR_POLL_INTERVAL_S` seconds (default 60 s), the conductor scans
each watch path for new session directories:

```
2026-03-20T21:00:01  INFO  …session_manager  Registered session: subject=842456  ts=2026-03-20T20-23-05
```

A session is detected if:

- Its directory name matches `YYYY-MM-DDTHH-MM-SS`
- It contains at least one run sub-directory (also timestamp-named) with a
  `behavior/` folder

New sessions discovered by subsequent scans are registered and processed
without restarting the conductor.

### 4b. Minimum age gate

A newly registered session is **not processed** until it is at least
`CONDUCTOR_MIN_SESSION_AGE_MINUTES` old (default 5 min).  This prevents
the conductor from touching a session while Bonsai is still actively writing.

### 4c. Per-session cadence cycle

Once a session is due (older than the minimum age and the cadence interval
has elapsed since the last cycle), the conductor runs these five steps **in
sequence**:

---

#### Step 1 — Consolidation

```
2026-03-20T21:05:00  INFO  …session_manager  [842456] Consolidating run directories.
2026-03-20T21:05:01  INFO  …pipeline_bridge  Running delphi-data consolidate: ...
2026-03-20T21:05:03  INFO  …pipeline_bridge  delphi-data consolidate completed successfully.
2026-03-20T21:05:03  INFO  …pipeline_bridge  Moving Delphi metadata JSONL files to behavior/metadata/ ...
2026-03-20T21:05:03  INFO  …pipeline_bridge  Moved 2 metadata file(s).
```

What this does:

1. **Merges run sub-directories** — if the experimenter restarted Bonsai
   mid-session, two run dirs exist (e.g. `2026-03-20T20-23-37/` and
   `2026-03-20T21-45-00/`).  All files from later dirs are moved into the
   earliest, then empty dirs are removed.
2. **Moves JSONL metadata files** — `HardwareSettings_*.jsonl` and
   `RuleSettings_*.jsonl` are moved from `behavior/` into `behavior/metadata/`
   so the metadata generator can find them.

After consolidation the conductor resolves the **canonical run dir** (the one
remaining timestamp-named sub-directory) and uses it for all subsequent steps.

---

#### Step 2 — Metadata check / generation

```
2026-03-20T21:05:04  INFO  …session_manager  [842456] METADATA_CHECK
2026-03-20T21:05:04  INFO  …session_manager  [842456] Generating AIND metadata.
2026-03-20T21:05:04  INFO  …metadata_bridge  Generating subject.json ...
2026-03-20T21:05:05  INFO  …metadata_bridge  subject.json generated.
2026-03-20T21:05:05  INFO  …metadata_bridge  Generating instrument.json ...
2026-03-20T21:05:06  INFO  …metadata_bridge  Odor channels: [0, 1, 2, 4]  names: ['blank', 'isoamyl acetate', 'methyl valerate', 'ethyl butyrate']
2026-03-20T21:05:06  INFO  …metadata_bridge  instrument.json generated.
2026-03-20T21:05:06  INFO  …metadata_bridge  Generating acquisition.json ...
2026-03-20T21:05:07  INFO  …metadata_bridge  acquisition.json generated.
2026-03-20T21:05:07  INFO  …metadata_bridge  Generating procedures.json ...
2026-03-20T21:05:08  INFO  …metadata_bridge  procedures.json generated.
2026-03-20T21:05:08  INFO  …metadata_bridge  All AIND metadata written to …/metadata.
```

The conductor checks whether all four files exist in `<run_dir>/metadata/`.
If any are missing, it calls `metadata_generator` to produce them:

| File | Source |
|------|--------|
| `subject.json` | AIND metadata service (requires VPN) |
| `instrument.json` | `HardwareSettings_*.jsonl` + `RuleSettings_*.jsonl` from `behavior/metadata/` |
| `acquisition.json` | JSONL files + file creation times for session start/end |
| `procedures.json` | Surgery notes DOCX (e.g. `\\allen\aind\scratch\chronos\surgeryNotes\842456\842456_craniotomy-implantation.docx`) |

After generating metadata, `acquisition_end_time` in `acquisition.json` is
stamped with the current UTC time as a placeholder.  Update it later if
needed:

```python
from experiment_conductor.metadata_bridge import update_acquisition_end_time
from pathlib import Path
from datetime import datetime, timezone

update_acquisition_end_time(
    Path(r"\\allen\aind\stage\chronic\842456\2026-03-20T20-23-05\2026-03-20T20-23-37\metadata"),
    datetime(2026, 3, 20, 22, 15, 0, tzinfo=timezone.utc),
)
```

---

#### Step 3 — delphi-data pipeline

```
2026-03-20T21:05:09  INFO  …session_manager  [842456] Running delphi-data pipeline.
2026-03-20T21:05:09  INFO  …pipeline_bridge  Running delphi-data pipeline: python -m delphi_data.cli pipeline --data-root ... --experiment bonhoeffer --firmware 0.1.0 --subject-id 842456 --append --skip-clips
```

This step only runs if `behavior/DelphiController/` exists under the run dir
(confirming Bonsai wrote Harp data).  It calls `delphi-data pipeline`, which:

1. **Ingests Harp data** from `behavior/DelphiController/` → poke events
2. **Builds / appends** `behavior/delphi_dataset.csv`
3. **Generates analysis figures** in `behavior/results/`

On the first cycle the full CSV is built; on subsequent cycles `--append`
is passed so only new Harp data is added to the existing CSV.

---

#### Step 4 — Noise floor estimation (optional)

```
2026-03-20T21:05:45  INFO  …session_manager  [842456] Estimating noise floor.
2026-03-20T21:05:45  INFO  …noise_floor      Estimating noise floor from …/ecephys/continuous.dat
2026-03-20T21:05:47  INFO  …noise_floor      Noise floor estimate: 384 channels, median RMS = 8.43 µV (from 300000 samples).
2026-03-20T21:05:47  INFO  …noise_floor      Noise floor saved: …/ecephys/noise_floor.json
```

Only runs when `CONDUCTOR_ENABLE_NOISE_FLOOR=true`.  See [Section 6](#6-noise-floor-estimation).

---

#### Step 5 — S3 upload

The conductor submits upload jobs to the AIND data-transfer service.

**First cycle (start job):** requires ≥3 video chunks to exist locally.

```
2026-03-20T22:05:00  INFO  …session_manager  [842456] Running upload cycle (is_start=True).
2026-03-20T22:05:00  INFO  …uploader_bridge  Chunks — local: 5  S3 confirmed: 0  in-flight (this session): 0
2026-03-20T22:05:01  INFO  …uploader_bridge  Submitting 1 batch(es) covering 1 chunk(s).
2026-03-20T22:05:02  INFO  …uploader_bridge  Submitted batch 1/1 (1 chunk(s)).  Total in-flight: 1.
```

**Subsequent cycles (chunk jobs):** new chunks not yet on S3 are submitted.

```
2026-03-20T23:05:00  INFO  …uploader_bridge  Chunks — local: 8  S3 confirmed: 3  in-flight (this session): 0
2026-03-20T23:05:01  INFO  …uploader_bridge  Submitting 1 batch(es) covering 3 chunk(s).
```

The last `CONDUCTOR_NUM_LAST_CHUNKS_TO_IGNORE` chunks (default 2) are always
skipped in case data is still being written to them.

---

## 5. Adding new sessions

### Automatic (recommended)

Any new `<subject_id>/<session_ts>/` directory that appears under a watch path
is automatically discovered on the next poll and processed without any
intervention.

### Manual — register a specific path

To register a session that is not under any watch path, or to register it
immediately rather than waiting for the next poll:

```bash
conductor --add-session "\\allen\aind\stage\chronic\842456\2026-04-15T09-30-00"
```

The subject ID is inferred from the parent directory name.  Alternatively,
add a new watch path to `.env` and restart:

```dotenv
CONDUCTOR_WATCH_PATHS=\\allen\aind\stage\chronic,\\allen\aind\stage\pirouette
```

---

## 6. Noise floor estimation

### What was implemented

The conductor estimates the **RMS noise floor** per channel from raw binary
ephys data.  This is a standard measure of recording quality — values of
5–15 µV (median) indicate a healthy recording; values above 30 µV may
indicate poor grounding, excessive motion, or probe damage.

Enable it in `.env`:

```dotenv
CONDUCTOR_ENABLE_NOISE_FLOOR=true
CONDUCTOR_NOISE_FLOOR_N_SECONDS=10.0    # read first 10 s
CONDUCTOR_NOISE_FLOOR_MAX_CHANNELS=0    # 0 = all channels
```

### How it works

1. The conductor searches for `ecephys/` under the run dir.
2. It finds the first continuous binary data file:
   - SpikeGLX: `*.ap.bin` (paired with `*.meta`)
   - Open Ephys: `continuous.dat` (with 1024-byte ASCII header)
   - Fallback: any `*.dat` or `*.bin`
3. It reads `n_seconds` worth of samples from the start of the file.
4. It computes `RMS = sqrt(mean(samples²))` per channel.
5. It scales from raw int16 LSB to µV using `uv_per_bit = 0.195` (the
   Neuropixels 1.0 AP-band calibration factor at gain=500).
6. Results are written to `ecephys/noise_floor.json`.

### Output file

```json
{
  "channel_rms_uv": [8.1, 7.9, 9.3, ...],   // one value per channel
  "median_rms_uv": 8.43,
  "n_samples": 300000,
  "n_channels": 384,
  "sample_rate_hz": 30000.0,
  "uv_per_bit": 0.195,
  "source_file": "...\\ecephys\\continuous.dat",
  "timestamp": "2026-03-20T21:05:47+00:00"
}
```

### Neuropixels 2.0 / custom gain

The default `uv_per_bit = 0.195` is correct for **Neuropixels 1.0** at the
default AP gain (500).  For **Neuropixels 2.0**, the conversion is
`0.0125 µV/bit`.  You can override this in a custom script:

```python
from experiment_conductor.noise_floor import estimate_noise_floor
from pathlib import Path

result = estimate_noise_floor(
    Path(r"\\allen\aind\stage\chronic\842456\2026-03-20T20-23-05\2026-03-20T20-23-37"),
    n_seconds=30.0,
    uv_per_bit=0.0125,   # NP2.0
)
print(f"Median RMS: {result['median_rms_uv']:.2f} µV")
```

!!! note "One estimate per session"
    The noise floor is estimated **once per session** (on the first cycle
    after metadata is present) and not recalculated on subsequent cycles.
    To force a re-run, delete `ecephys/noise_floor.json` and delete the
    session entry from `conductor_state.json`.

### Limitation — method assumed

The noise floor implementation uses a **simple whole-segment RMS** approach
(no spike removal, no bandpass filtering).  If you discussed a different
method (e.g. MAD-based estimation, inter-spike-interval blanking, or a
specific frequency band) in a prior conversation, the current implementation
can be updated in `noise_floor.py:estimate_noise_floor()`.

---

## 7. State persistence and restart recovery

If `CONDUCTOR_STATE_FILE` is set, the conductor writes a JSON file containing
the current state of every known session after each poll:

```json
{
  "\\\\allen\\aind\\stage\\chronic\\842456\\2026-03-20T20-23-05": {
    "data_root": "\\\\allen\\aind\\stage\\chronic\\842456\\2026-03-20T20-23-05",
    "subject_id": "842456",
    "session_datetime": "2026-03-20T20-23-05",
    "run_dir": "...\\2026-03-20T20-23-37",
    "phase": "UPLOADING",
    "consolidation_done": true,
    "metadata_present": true,
    "metadata_generated": true,
    "dataset_built": true,
    "noise_floor_estimated": false,
    "upload_started": true,
    "last_processed": "2026-03-20T22:05:03",
    ...
  }
}
```

On restart, the conductor reads this file and skips any steps already marked
`true`.  This means:

- Consolidation that already ran is not repeated.
- Metadata that was successfully generated is not regenerated.
- Upload jobs that were submitted are not re-submitted.

### Resetting a session

To force a session to re-run from the beginning, either:

1. Edit the state JSON file and delete the session's entry, or
2. Delete the state file entirely (`rm conductor_state.json`).

On the next poll the session will be re-discovered and processed from scratch.

To re-run only a specific step:

```python
import json
path = "conductor_state.json"
key = r"\\allen\aind\stage\chronic\842456\2026-03-20T20-23-05"

with open(path) as f:
    state = json.load(f)

# Force metadata to regenerate on the next cycle
state[key]["metadata_present"] = False
state[key]["metadata_generated"] = False

with open(path, "w") as f:
    json.dump(state, f, indent=2)
```

---

## 8. Monitoring multiple sessions

The conductor processes up to **4 sessions concurrently** using a thread pool.
Each session logs with its subject ID as a prefix, making it easy to follow:

```
2026-03-20T22:00:00  INFO  …  [842456] Running delphi-data pipeline.
2026-03-20T22:00:01  INFO  …  [801055] Consolidating run directories.
2026-03-20T22:00:02  INFO  …  [842456] Running upload cycle (is_start=True).
2026-03-20T22:00:05  INFO  …  [111111] Generating AIND metadata.
```

To see the status of all sessions at any time, read the state file:

```bash
python - << 'EOF'
import json
from pathlib import Path

state = json.loads(Path("conductor_state.json").read_text())
for key, s in state.items():
    print(f"{s['subject_id']}  {s['session_datetime']}  phase={s['phase']}"
          f"  errors={s['consecutive_errors']}")
EOF
```

---

## 9. Skipping individual steps

Disable any step globally in `.env`:

```dotenv
CONDUCTOR_ENABLE_PIPELINE=false    # skip delphi-data pipeline
CONDUCTOR_ENABLE_METADATA=false    # skip metadata generation
CONDUCTOR_ENABLE_NOISE_FLOOR=false # skip noise floor (default)
CONDUCTOR_ENABLE_UPLOAD=false      # skip S3 upload
```

Or skip individual sub-steps of the pipeline:

```dotenv
CONDUCTOR_PIPELINE_SKIP_BUILD=true      # don't run build-dataset
CONDUCTOR_PIPELINE_SKIP_CLIPS=true      # don't extract poke clips (default)
CONDUCTOR_PIPELINE_SKIP_SNAPSHOT=true   # don't generate figures
```

---

## 10. Complete `.env` example

```dotenv
# Watch paths
CONDUCTOR_WATCH_PATHS=\\allen\aind\stage\chronic

# Experiment identity
CONDUCTOR_EXPERIMENT_TYPE=delphi
CONDUCTOR_ACQUISITION_TYPE=ChronicRecording
CONDUCTOR_PROTOCOL_ID=2115
CONDUCTOR_INSTRUMENT_ID=323_EPHYS1_OPTO_20240223
CONDUCTOR_EXPERIMENT_ROOM=323
CONDUCTOR_DELPHI_COMPUTER_ID=W10DT714023
CONDUCTOR_SURGEONS=Scientist A,Scientist B
CONDUCTOR_EXPERIMENTERS=Scientist A
CONDUCTOR_SURGERY_NOTES_BASE=\\allen\aind\scratch\chronos\surgeryNotes

# Pipeline
CONDUCTOR_DELPHI_EXPERIMENT=bonhoeffer
CONDUCTOR_DELPHI_FIRMWARE=0.1.0
CONDUCTOR_ENABLE_PIPELINE=true
CONDUCTOR_PIPELINE_CADENCE_MINUTES=60
CONDUCTOR_PIPELINE_SKIP_BUILD=false
CONDUCTOR_PIPELINE_SKIP_CLIPS=true
CONDUCTOR_PIPELINE_SKIP_SNAPSHOT=false

# Metadata
CONDUCTOR_ENABLE_METADATA=true

# Noise floor (set true for Pirouette / combined sessions)
CONDUCTOR_ENABLE_NOISE_FLOOR=false
CONDUCTOR_NOISE_FLOOR_N_SECONDS=10.0
CONDUCTOR_NOISE_FLOOR_MAX_CHANNELS=0

# Upload
CONDUCTOR_ENABLE_UPLOAD=true
CONDUCTOR_S3_BUCKET=aind-open-data
CONDUCTOR_CONTACT_EMAIL=you@alleninstitute.org
CONDUCTOR_PROJECT_NAME=DynamicRoutingTask
CONDUCTOR_UPLOAD_BATCH_SIZE=2
CONDUCTOR_NUM_LAST_CHUNKS_TO_IGNORE=2
CONDUCTOR_DRY_RUN=false
CONDUCTOR_DELETE_AFTER_UPLOAD=false
CONDUCTOR_KEEP_LOCAL_PATTERNS=behavior/delphi_dataset.csv,behavior/DelphiController/**,behavior/results/**,behavior/metadata/**

# Polling / error handling
CONDUCTOR_POLL_INTERVAL_S=60.0
CONDUCTOR_MIN_SESSION_AGE_MINUTES=5.0
CONDUCTOR_MAX_CONSECUTIVE_ERRORS=5
CONDUCTOR_ERROR_BACKOFF_MINUTES=30.0

# State persistence
CONDUCTOR_STATE_FILE=conductor_state.json
```

---

## 11. Troubleshooting

| Symptom | Most likely cause | Fix |
|---------|------------------|-----|
| No sessions discovered | Watch path unreachable or wrong structure | Verify path exists and contains `<subject_id>/<session_ts>/` sub-dirs |
| Session discovered but never processed | Minimum-age gate not met | Wait 5 min or set `CONDUCTOR_MIN_SESSION_AGE_MINUTES=0` |
| `Metadata already present` but files look wrong | Old/corrupt JSON files | Delete `run_dir/metadata/*.json` and the state entry; conductor will regenerate |
| `subject.json` fails | AIND metadata service unreachable | Check VPN; conductor falls back to a minimal stub |
| `procedures.json` minimal (no surgery data) | Surgery notes DOCX not found | Place DOCX at `<CONDUCTOR_SURGERY_NOTES_BASE>/<subject_id>/<subject_id>_craniotomy-implantation.docx` |
| Pipeline step skipped | `behavior/DelphiController/` missing | Bonsai may not have written Harp data; check acquisition computer |
| Upload skipped with "Need ≥3 chunks" | Too few video chunk dirs | Wait for recording to accumulate; check `behavior-videos/TopCamera/` |
| Upload skipped with "already exists" | S3 start job already submitted before | Normal — conductor detected the session is already started in DocDB |
| `NOISE_FLOOR` step skipped silently | No `ecephys/` directory | Expected for Delphi-only sessions; set `CONDUCTOR_ENABLE_NOISE_FLOOR=false` |
| Session marked `ERROR` | 5 consecutive failures | Read the `error_message` in state file; fix root cause; reset state entry |
| Conductor hangs on `Ctrl+C` | Upload batch in progress | Wait for current batch POST to complete (usually <10 s) |
