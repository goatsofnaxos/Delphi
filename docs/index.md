# Delphi

Delphi is an olfactory operant-conditioning apparatus and software ecosystem for freely-moving mice. This site documents the Python packages that support the experiment.

---

## Packages

| Package | Purpose |
|---------|---------|
| [delphi-data](delphi-data/index.md) | Ingest, curate, analyse, and visualise behavioral data from Delphi and Pirouette sessions |
| [metadata-generator](metadata-generator/index.md) | Generate AIND v2 metadata (subject, procedures, instrument, acquisition) |
| [launcher](launcher/index.md) | Interactive multi-experiment Bonsai workflow launcher with subject recall and session logging |
| [experiment-conductor](experiment-conductor/index.md) | End-to-end orchestrator: launch → process → metadata → S3 upload |

---

## experiment-conductor

The `experiment-conductor` ties all four packages into a single supervised process so that an experimenter only needs to start one program at the beginning of a session.

```
LAUNCHING ──► RUNNING ──► ENDING ──► DONE
```

| Stage | What happens |
|-------|-------------|
| **Launch** | Starts the Bonsai launcher interactively; waits for workflows to appear |
| **Running** | Runs the `delphi-data` pipeline on a configurable cadence (every N minutes, or at a fixed minute past every hour); generates AIND metadata after the first consolidation; submits upload jobs to S3 |
| **Ending** | Updates the acquisition end time; verifies `probe.json` for Pirouette experiments; runs a final cycle |
| **Done** | Optionally deletes large local files (video, ephys) only after confirming their chunks are present on S3 |

Upload jobs are submitted to `aind-data-transfer-service` in small configurable batches.
In-flight chunks are tracked so the same data is never submitted twice, and local files
are never deleted until S3 confirms the transfer is complete.

Three hotkeys are available while an experiment is running:

| Hotkey | Action |
|--------|--------|
| `Ctrl+Shift+P` | Trigger a pipeline cycle immediately |
| `Ctrl+Shift+U` | Pause / resume upload between batches |
| `Ctrl+Shift+E` | Signal experiment end |

See the [experiment-conductor docs](experiment-conductor/index.md) for the full configuration reference and API.

---

## Repository

Source code: [github.com/goatsofnaxos/Delphi](https://github.com/goatsofnaxos/Delphi)
