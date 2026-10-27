# Tutorial: delphi-data

`delphi-data` ingests raw Harp event streams, computes behavioral metrics, and generates figures.  It can be driven from the CLI for one-shot processing or imported as a library for interactive analysis.

---

## Prerequisites

```bash
cd src/delphi-data
uv sync          # or: pip install -e .
# For video clip extraction:
pip install -e ".[video]"
```

---

## Typical session directory layout

```
2026-03-20T20-23-05/                ← session root (data_root)
  2026-03-20T20-23-37/              ← run sub-directory (may be several)
    behavior/
      DelphiController/             ← raw Harp binary streams
      HardwareSettings_*.jsonl
      RuleSettings_*.jsonl
    behavior-videos/
      TopCamera/
        chunk_0/
        chunk_1/
    ecephys/
      probe.json                    ← Neuropixels serial number (pirouette)
```

When multiple run sub-directories exist (e.g. after a crash-and-restart), run `consolidate` first.

---

## Step 1 — Consolidate run sub-directories (if needed)

```bash
delphi-data consolidate \
    --data-root /data/842456/2026-03-20T20-23-05
```

This merges all run sub-directories into the earliest one.  If there is only one run directory this step is a no-op.

---

## Step 2 — Build the behavioral dataset

```bash
delphi-data build-dataset \
    --data-root /data/842456/2026-03-20T20-23-05/2026-03-20T20-23-37 \
    --firmware 0.1.0
```

Output: `behavior/delphi_dataset.csv` — one row per nose-poke event with timing, port, odor, and reward columns.

Pass `--append` to merge new rows into an existing CSV (deduplicates on `beam_break_onset`).  This is used automatically by the experiment conductor during live recording.

---

## Step 3 — Generate a snapshot

```bash
delphi-data snapshot \
    --experiment bonhoeffer \
    --data-root /data/842456/2026-03-20T20-23-05/2026-03-20T20-23-37 \
    --subject-id 842456
```

Output: PNG figures saved to `behavior/results/`.

---

## Step 4 — Run the full pipeline in one command

Steps 1–3 can be combined:

```bash
delphi-data pipeline \
    --data-root /data/842456/2026-03-20T20-23-05 \
    --experiment bonhoeffer \
    --firmware 0.1.0 \
    --subject-id 842456
```

| Flag | Effect |
|------|--------|
| `--append` | Merge new Harp rows into existing CSV |
| `--skip-build` | Skip dataset construction (CSV already exists) |
| `--skip-snapshot` | Skip figure generation |
| `--skip-clips` | Skip video clip extraction (default: skipped) |

---

## Step 5 — Extract poke clips (optional)

```bash
delphi-data create-clips \
    --data-root /data/842456/2026-03-20T20-23-05/2026-03-20T20-23-37
```

Requires the `[video]` extra.  Clips are saved alongside the raw video files.

---

## Python API example

```python
from pathlib import Path
from delphi_data.poke_metrics import (
    load_csvs_with_subject_id,
    odor_change_events,
    compute_poke_stats,
)
from delphi_data.visualization import plot_poke_rate_timeseries

data_root = Path("/data/842456")
df = load_csvs_with_subject_id(data_root)

# Build odor-change event dict from the dataset
odor_events = odor_change_events(df)

# Compute smoothed poke rates (600 s Gaussian kernel, 60 s bins)
poke_stats = compute_poke_stats(df, odor_events, tau=600, dt=60)

fig = plot_poke_rate_timeseries(poke_stats, time_unit="days")
fig.savefig("poke_rate.png", dpi=150, bbox_inches="tight")
```

---

## Environment variable overrides

All CLI defaults can be set via environment variables.  See `delphi_data.settings` for the full list.

| Variable | Default | Effect |
|----------|---------|--------|
| `DELPHI_DATASET_APPEND` | `false` | Append rather than skip when CSV exists |
| `DELPHI_SKIP_CLIPS` | `true` | Skip clip extraction by default |
