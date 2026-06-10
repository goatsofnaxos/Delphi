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
   - Runs the full `delphi-data` pipeline (consolidate → build-dataset → snapshot).
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

## Scheduling modes

| Mode | `.env` key | CLI flag | Behaviour |
|------|-----------|----------|-----------|
| Cadence (default) | `PIPELINE_CADENCE_MINUTES=60` | `--pipeline-cadence-minutes N` | Fires every N minutes |
| On-the-hour | `SCHEDULE_MINUTE_OF_HOUR=45` | `--schedule-minute-of-hour N` | Fires at :NN past every hour |

Both keys are mutually exclusive; `SCHEDULE_MINUTE_OF_HOUR` takes priority when set.

---

## Hotkeys

| Hotkey | Action |
|--------|--------|
| `Ctrl+Shift+P` | Trigger a pipeline cycle immediately |
| `Ctrl+Shift+U` | Pause / resume upload between batches |
| `Ctrl+Shift+E` | Signal experiment end |

Hotkeys are configurable via `.env` (`HOTKEY_PIPELINE`, `HOTKEY_UPLOAD_PAUSE`,
`HOTKEY_END_EXPERIMENT`).

---

## Quick start

```bash
cd src/experiment_conductor
cp .env.example .env   # fill in DATA_ROOT, SUBJECT_ID, …
uv run scripts/run_conductor.py

# or with CLI overrides:
uv run scripts/run_conductor.py --data-root /data/subject/session --dry-run
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
