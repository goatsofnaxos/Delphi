# Tutorial: metadata-generator

`metadata-generator` produces the four AIND-compliant JSON files required for every session upload: `subject.json`, `instrument.json`, `acquisition.json`, and `procedures.json`.

---

## Prerequisites

```bash
cd src/metadata_generator
uv sync          # or: pip install -e .
```

Network access (VPN or on-site) is required to fetch subject metadata from the AIND subject database.

---

## Step 1 — Set up configuration

All inputs can be passed as CLI flags or environment variables.  The simplest approach is a `.env` file:

```ini
SUBJECT_ID=842456
CURRENT_EXPERIMENT=delphi_pirouette
DATASET_ROOT=/data/842456/2026-03-20T20-23-05/2026-03-20T20-23-37
METADATA_OUTPUT_PATH=/data/842456/2026-03-20T20-23-05/metadata
INSTRUMENT_ID=delphi-rig-0
EXPERIMENT_ROOM=447
PROTOCOL_ID=DRn20231002
SURGEONS=John Smith
EXPERIMENTERS=B. Pratt
SURGERY_NOTES_PATH=/allen/aind/scratch/chronos/surgeryNotes/842456/842456_craniotomy-implantation.docx
DELPHI_COMPUTER_ID=W10DT714591
```

---

## Step 2 — Run the generator

```bash
python -m metadata_generator
```

Or pass everything on the command line:

```bash
python -m metadata_generator \
    --subject-id 842456 \
    --current-experiment delphi_pirouette \
    --dataset-root /data/842456/2026-03-20T20-23-05/2026-03-20T20-23-37 \
    --metadata-output-path /data/842456/2026-03-20T20-23-05/metadata \
    --instrument-id delphi-rig-0 \
    --experiment-room 447
```

---

## Step 3 — Check the output

After a successful run, four files appear in `METADATA_OUTPUT_PATH`:

```
metadata/
  subject.json       ← mouse info from AIND subject service
  instrument.json    ← rig hardware (cameras, DAQ, probes)
  acquisition.json   ← session timing, odors, electrode configuration
  procedures.json    ← surgery details and chronic implant ProbeConfig
```

Each file is validated by a pydantic round-trip before being written — if validation fails, the error is logged but the other files are still attempted.

---

## What each file requires

### subject.json

Fetched from the AIND subject database using `SUBJECT_ID`.  Requires network access.  If the fetch fails, a minimal fallback is written and a warning is logged.

### instrument.json

Built from `INSTRUMENT_ID`, `EXPERIMENT_ROOM`, and `CURRENT_EXPERIMENT`.  Reads `HardwareSettings_*.jsonl` from `behavior/metadata/` to populate the DAQ channel list.

### acquisition.json

Built from session timing (derived from file timestamps in the run directory), odor names (from `RuleSettings_*.jsonl`), and the ephys assembly from `instrument.json`.  The start time is taken from the earliest file ctime in the run directory.

### procedures.json

Parsed from the surgery notes `.docx` to extract the craniotomy and implant details.  The `ProbeConfig` (CCFv3 atlas coordinates, BREGMA_ARI coordinate system, chronic implant note) is extracted from `acquisition.json` automatically.

If surgery notes are not found, a minimal `Procedures` object with just the subject ID is written.

---

## Updating the acquisition end time

After the experiment ends, update `acquisition_end_time` in the already-written `acquisition.json`:

```bash
python -c "
from datetime import datetime, timezone
from metadata_generator.utils import update_acquisition_end_time
from pathlib import Path
update_acquisition_end_time(
    Path('/data/842456/2026-03-20T20-23-05/metadata'),
    datetime(2026, 3, 20, 22, 47, 0, tzinfo=timezone.utc),
)
"
```

When using the experiment conductor this step is handled automatically when you signal end-of-experiment.

---

## Supported experiment types

| `CURRENT_EXPERIMENT` | Subject | Instrument | Acquisition | Procedures |
|----------------------|:-------:|:----------:|:-----------:|:----------:|
| `delphi` | ✓ | ✓ | ✓ (odors) | ✓ |
| `pirouette` | ✓ | ✓ | ✓ (probe) | ✓ (ProbeConfig) |
| `delphi_pirouette` | ✓ | ✓ | ✓ (both) | ✓ (ProbeConfig) |
