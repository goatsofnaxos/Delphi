# delphi-data

**delphi-data** is a Python package for ingesting, curating, analysing, and visualising behavioral data from the Delphi olfactory poke-port apparatus.

---

## Overview

The package provides a complete pipeline from raw hardware event streams to publication-quality figures:

```
Raw Harp streams
      │
      ▼
delphi_data.ingestion   ← parse poke state machine, build per-poke CSV
      │
      ▼
delphi_data.poke_metrics ← compute rates, IPI thresholds, fold changes, durations
      │
      ▼
delphi_data.visualization ← generate figures (all return matplotlib Figure objects)
      │
      ▼
scripts/snapshots/        ← experiment-specific snapshot scripts (save figures to disk)
```

---

## Quick start

### Build a dataset

```bash
delphi-data build-dataset \
    --data-root /data/842456/2026-03-20T20-23-05/2026-03-20T20-23-37 \
    --firmware 0.1.0
```

### Generate a snapshot

```bash
delphi-data snapshot \
    --experiment bonhoeffer \
    --data-root /data/842456/2026-03-20T20-23-05/2026-03-20T20-23-37
```

### Python API

```python
from delphi_data.poke_metrics import (
    load_csvs_with_subject_id,
    odor_change_events,
    compute_poke_stats,
)
from delphi_data.visualization import plot_poke_rate_timeseries

df = load_csvs_with_subject_id("/data/842456")
# ... build odor_events dict ...
poke_stats = compute_poke_stats(df, odor_events, tau=600, dt=60)
fig = plot_poke_rate_timeseries(poke_stats, time_unit="days")
fig.savefig("poke_rate.png", dpi=150, bbox_inches="tight")
```

---

## Installation

```bash
pip install -e .                    # core
pip install -e ".[video]"           # + video clip extraction
pip install -e ".[docs]"            # + documentation tools
pip install -e ".[all]"             # everything
```

---

## CLI reference

```
delphi-data <command> --help

Commands:
  build-dataset   Ingest raw session data → behavior/delphi_dataset.csv
  snapshot        Generate experiment-specific visualization snapshot
  consolidate     Merge multiple run sub-directories into the earliest one
  create-clips    Extract video clips around poke events  [requires video]
```

---

## Module index

| Module | Purpose |
|--------|---------|
| [`delphi_data.poke_metrics`](api/poke_metrics.md) | All behavioral computations |
| [`delphi_data.visualization`](api/visualization.md) | All plotting functions |
| [`delphi_data.ingestion`](api/ingestion.md) | Raw Harp data → per-poke DataFrame |
| [`delphi_data.curation`](api/curation.md) | Session directory consolidation |
| [`delphi_data.config`](api/config.md) | Firmware register definitions |
| [`delphi_data.cli`](api/cli.md) | CLI entry point |
