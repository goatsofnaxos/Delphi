# metadata-generator

**metadata-generator** is a Python package for generating AIND v2 metadata files
(subject, procedures, instrument, and acquisition) for the Delphi and Pirouette
chronic electrophysiology experiments.

---

## Overview

The package wraps the `aind-data-schema` models and provides a pipeline that produces
four standard AIND metadata JSON files from a single session's raw inputs:

```
CLI / environment variables
           │
           ▼
metadata_generator.config     ← parse args + env → PipelineConfig
           │
     ┌─────┴──────┬───────────────┬──────────────┐
     ▼            ▼               ▼              ▼
  subject    instrument       procedures     acquisition
     │            │               │              │
     └────────────┴───────────────┴──────────────┘
                              │
                              ▼
                    subject.json, instrument.json,
                    procedures.json, acquisition.json
```

---

## Quick start

```bash
# Install with docs extras
pip install -e "src/metadata_generator[docs]"

# Run the pipeline (all fields can also be set via environment variables)
python -m metadata_generator \
    --subject-id 842456 \
    --current-experiment delphi_pirouette \
    --dataset-root /data/842456/2026-03-20 \
    --metadata-output-path /data/842456/2026-03-20/metadata \
    --instrument-id NP3_OPTO_1 \
    --experiment-room 447
```

---

## Installation

```bash
pip install -e .                 # core
pip install -e ".[docs]"         # + documentation tools
pip install -e ".[dev]"          # + dev/test tools
```

---

## Module index

| Module | Purpose |
|--------|---------|
| [`metadata_generator.cli`](api/cli.md) | CLI argument parser |
| [`metadata_generator.config`](api/config.md) | Pipeline configuration dataclass and builder |
| [`metadata_generator.subject`](api/subject.md) | Fetch and write AIND Subject metadata |
| [`metadata_generator.instrument`](api/instrument.md) | Build AIND Instrument metadata |
| [`metadata_generator.acquisition`](api/acquisition.md) | Build AIND Acquisition metadata |
| [`metadata_generator.procedures`](api/procedures.md) | Parse surgery notes and build AIND Procedures metadata |
| [`metadata_generator.utils`](api/utils.md) | Shared helpers for instrument introspection |
