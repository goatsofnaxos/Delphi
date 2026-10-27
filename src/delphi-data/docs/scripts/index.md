# Scripts

Standalone scripts and experiment-specific snapshot modules.

Scripts live under `scripts/` in the repository root and are **not** part of
the installed `delphi_data` package, but they are importable when running from
the project root.

---

| Script | Description |
|--------|-------------|
| [full_processing_pipeline](full_processing_pipeline.md) | **Master pipeline**: build-dataset -> create-clips -> snapshot in one command |
| [build_dataset](build_dataset.md) | Ingest a raw session and write `delphi_dataset.csv` |
| [data_snapshot](data_snapshot.md) | Router that dispatches to experiment-specific snapshots |
| [create_poke_clips](create_poke_clips.md) | Extract video clips centred on poke events |

### Snapshot modules (`scripts/snapshots/`)

| Module | Experiment |
|--------|------------|
| [bonhoeffer](snapshots/bonhoeffer.md) | Bonhoeffer olfactory learning paradigm |
| [_common](snapshots/common.md) | Shared loading, computation, and saving helpers |

### Adding a new experiment snapshot

1. Create `scripts/snapshots/<name>.py` with `run_snapshot()` and `_parse_args()`.
2. Add `"<name>": "snapshots.<name>"` to `REGISTRY` in `scripts/snapshots/__init__.py`.
3. Add a docs page under `docs/scripts/snapshots/<name>.md`.
4. Run `python scripts/update_api_docs.py --build` to rebuild the site.
