# Delphi

Delphi is an olfactory operant-conditioning apparatus and software ecosystem for freely-moving mice. This site documents the Python packages that support the experiment.

---

## Packages

| Package | Purpose |
|---------|---------|
| [delphi-data](delphi-data/index.md) | Ingest, curate, analyse, and visualise behavioral data from Delphi and Pirouette sessions |
| [metadata-generator](metadata-generator/index.md) | Generate AIND v2 metadata (subject, procedures, instrument, acquisition) |
| [launcher](launcher/index.md) | Interactive multi-experiment Bonsai workflow launcher with subject recall and session logging |
| [experiment-conductor](experiment-conductor/index.md) | Network-drive watcher that orchestrates multi-session metadata generation, delphi-data processing, noise-floor estimation, and S3 upload |

---

## experiment-conductor

The `experiment-conductor` runs on a local workstation or VM with access to
the shared network drive where the acquisition computer saves data.  It
**does not** control Bonsai or run on the acquisition computer.

```
watch path scan → discover sessions → per-session cadence cycle
                                          │
                               ┌──────────▼──────────┐
                               │  CONSOLIDATING       │ merge run dirs
                               │  METADATA_CHECK      │ verify JSON files
                               │  METADATA_GENERATING │ generate if missing
                               │  BUILDING            │ delphi-data pipeline
                               │  NOISE_FLOOR         │ RMS per channel
                               │  UPLOADING           │ submit chunk jobs
                               └─────────────────────-┘
                               (repeats every cadence interval)
```

| Phase | What happens |
|-------|-------------|
| **Consolidating** | Merge Bonsai restart run dirs into the earliest; move JSONL metadata to `behavior/metadata/` |
| **Metadata check / generate** | Check for all four AIND JSON files; run `metadata_generator` if any are missing |
| **Building** | Run `delphi-data pipeline` if `DelphiController*.jsonl` is present (builds / appends `delphi_dataset.csv` and analysis figures) |
| **Noise floor** | *(optional)* Estimate RMS noise floor per channel from raw Open Ephys / SpikeGLX data; write `ecephys/noise_floor.json` |
| **Uploading** | Submit chunk upload jobs to the AIND data-transfer service via `aind-chronic-ephys-uploader` |

Multiple sessions are processed concurrently (up to 4 at a time).  State is
persisted to a JSON file so work survives conductor restarts.  Upload safety
guarantees:

- **No duplicate submissions** — in-flight chunks tracked across cadence cycles
- **Confirmed-before-delete** — local files only removed after S3 confirmation

See the [experiment-conductor docs](experiment-conductor/index.md) for the
full configuration reference and API.

---

## Repository

Source code: [github.com/goatsofnaxos/Delphi](https://github.com/goatsofnaxos/Delphi)
