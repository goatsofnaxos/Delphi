# Snapshot modules

Experiment-specific snapshot modules live under `scripts/snapshots/`.

Each module exposes:

- `run_snapshot(data_root, ...)` — the main pipeline function
- `_parse_args(argv)` — CLI argument parser (called by the router)

Shared utilities (data loading, poke-stats computation, figure saving) are
provided by [`_common`](common.md) and imported by every experiment module.

---

| Module | Experiment |
|--------|------------|
| [bonhoeffer](bonhoeffer.md) | Bonhoeffer olfactory learning paradigm |
| [_common](common.md) | Shared helpers used by all snapshot modules |
