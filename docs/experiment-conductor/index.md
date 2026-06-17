# experiment-conductor

End-to-end orchestrator for Delphi chronic-recording experiments.

The conductor ties together four packages into a single supervised process:

| Stage | Package |
|-------|---------|
| Experiment launch | `launcher` |
| Data consolidation & snapshots | `delphi-data` |
| AIND-compliant metadata | `metadata-generator` |
| S3 upload | `aind-chronic-ephys-uploader` |

---

## Lifecycle

```
LAUNCHING ──► RUNNING ──► ENDING ──► DONE
```

1. **LAUNCHING** — spawns `launcher` interactively; the user selects the
   experiment profile, subject ID, and experimenter through the launcher
   prompts.  The conductor proceeds once Bonsai workflows are running.

2. **RUNNING** — a cadence scheduler fires a *cycle* every N minutes (or at
   a fixed minute past every hour).  Each cycle:
   - Runs the full `delphi-data` pipeline (consolidate → build-dataset → snapshot),
     always with `--append` so new Harp data accumulates in the existing CSV.
   - Moves Delphi metadata files to `behavior/metadata/` (once, after first
     consolidation).
   - Generates AIND metadata in `data_root/metadata/` (once, after metadata
     moved).
   - Submits upload jobs to `aind-data-transfer-service` once ≥ 3 chunks exist
     and all four metadata files are present.

3. **ENDING** — triggered by hotkey or Bonsai exit.  The user confirms the
   actual end time; `acquisition.json` is updated.  For Pirouette experiments,
   `probe.json` presence in `ecephys/` is verified.  A final cycle runs, then
   the upload is stopped cleanly.

4. **DONE** — optional large-file deletion (only for chunks confirmed on S3).

---

## Experiment types

| `EXPERIMENT_TYPE` | Delphi pipeline | Pirouette consolidation | Probe metadata |
|-------------------|:--------------:|:----------------------:|:--------------:|
| `delphi`          | ✓              |                        |                |
| `pirouette`       |                | ✓ (once)               | ✓              |
| `delphi_pirouette`| ✓              | ✓ (once)               | ✓              |

For `pirouette` and `delphi_pirouette`, a `probe.json` with the Neuropixels
serial number must be present in `ecephys/` before the experiment ends.
The full CCFv3 `ProbeConfig` (coordinates, BREGMA_ARI coordinate system,
chronic implant note) is extracted from `acquisition.json` and written into
`procedures.json` automatically.

---

## Scheduling modes

| Mode | `.env` key | CLI flag | Behaviour |
|------|-----------|----------|-----------|
| Cadence (default) | `PIPELINE_CADENCE_MINUTES=60` | `--pipeline-cadence-minutes N` | Fires every N minutes |
| On-the-hour | `SCHEDULE_MINUTE_OF_HOUR=45` | `--schedule-minute-of-hour N` | Fires at :NN past every hour |

Both keys are mutually exclusive; `SCHEDULE_MINUTE_OF_HOUR` takes priority when set.

---

## Hotkeys

| Hotkey | `.env` key | Default | Action |
|--------|-----------|---------|--------|
| `Ctrl+Shift+P` | `HOTKEY_PIPELINE` | `<ctrl>+<shift>+p` | Trigger a pipeline cycle immediately |
| `Ctrl+Shift+U` | `HOTKEY_UPLOAD_PAUSE` | `<ctrl>+<shift>+u` | Pause / resume upload between batches |
| `Ctrl+Shift+E` | `HOTKEY_END_EXPERIMENT` | `<ctrl>+<shift>+e` | Signal experiment end |
| `Ctrl+Shift+1` | `HOTKEY_TOGGLE_PIPELINE` | `<ctrl>+<shift>+1` | Toggle pipeline on / off |
| `Ctrl+Shift+2` | `HOTKEY_TOGGLE_METADATA` | `<ctrl>+<shift>+2` | Toggle metadata generation on / off |
| `Ctrl+Shift+3` | `HOTKEY_TOGGLE_UPLOAD` | `<ctrl>+<shift>+3` | Toggle upload on / off |
| `Ctrl+Shift+T` | `HOTKEY_UPDATE_END_TIME` | `<ctrl>+<shift>+t` | Update the session end time manually |
| `Ctrl+Shift+R` | `HOTKEY_RETRY_METADATA` | `<ctrl>+<shift>+r` | Re-run metadata generation immediately |

All hotkeys are configurable via `.env` or CLI flags.

---

## Data root resolution

The conductor needs to know where the session data lives on the **local server**
(not the acquisition computer).  Data flows:

```
Acquisition computer  ──robocopy──►  Local server  ──uploader──►  S3
(Bonsai records here)                (pipeline runs here)
```

Configure one of two modes — not both:

### Server-relative mode (recommended)

Set `SERVER_ROOT` to the root of the server where data is robocopied.
This corresponds to `remote_transfer_root_path` in the hardware schema
(e.g. `\allen\aind\stage\chronic`).

The conductor computes `data_root` as `SERVER_ROOT / SUBJECT_ID / SESSION_DATETIME`
after the launcher exits.  If `SESSION_DATETIME` is left blank, the conductor
automatically detects the newest `YYYY-MM-DDTHH-MM-SS` directory under
`SERVER_ROOT / SUBJECT_ID`, polling every 30 s until robocopy creates it
(up to 10 minutes).

```ini
SERVER_ROOT=\allen\aind\stage\chronic
SESSION_DATETIME=          # leave blank to auto-detect
SUBJECT_ID=12345
```

### Direct mode

Set `DATA_ROOT` to the full session path when the directory is already known:

```ini
DATA_ROOT=\allen\aind\stage\chronic\12345\2026-03-20T20-23-05
```

---

## Quick start

```bash
cd src/experiment_conductor
cp .env.example .env   # set SERVER_ROOT + SUBJECT_ID (or DATA_ROOT directly)
uv run scripts/run_conductor.py

# or with CLI overrides:
uv run scripts/run_conductor.py --server-root \allen\aind\stage\chronic --subject-id 12345 --dry-run
```

---

## Upload safety

- **No duplicate submissions** — a module-level `_SUBMITTED_CHUNKS` set tracks
  every chunk POSTed to the transfer service.  In-flight chunks are skipped on
  subsequent cycles even before S3 confirms them.
- **Confirmed-before-delete** — local files are only removed after a live S3
  query confirms the chunk is present.  If the query fails, deletion is aborted.
- **Keep-local patterns** — `behavior/delphi_dataset.csv`,
  `behavior/DelphiController/**`, `behavior/results/**`, and `metadata/**`
  are never deleted regardless of the delete setting.

---

## Configuration reference

All options can be set in `.env` or overridden on the CLI (CLI wins).

See `.env.example` in `src/experiment_conductor/` for the full list with
descriptions.
