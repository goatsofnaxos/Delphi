"""Experiment-specific snapshot scripts for the Delphi behavioral pipeline.

Each module in this package defines a ``run_snapshot`` function that generates
a curated set of figures for one experiment type and saves them to
``<data_root>/behavior/results/``.

Available experiment snapshots
-------------------------------
bonhoeffer
    Odor-learning / Bonhoeffer olfactory experiments.  Includes full
    session overview, multi-day odor-change windows, switch-aligned poke-rate
    analyses, bout-centric fold changes, cumulative counts, and poke-duration
    comparisons.

Adding a new experiment type
-----------------------------
1. Create ``scripts/snapshots/<experiment>.py``.
2. Implement ``run_snapshot(data_root, ...)`` using helpers from
   :mod:`scripts.snapshots._common`.
3. Register the name in :data:`REGISTRY` below.
4. The ``delphi-data snapshot --experiment <name>`` CLI will pick it up
   automatically.
"""

from __future__ import annotations

# Registry maps experiment name → dotted module path of run_snapshot
REGISTRY: dict[str, str] = {
    "bonhoeffer": "snapshots.bonhoeffer",
}

__all__ = ["REGISTRY"]
