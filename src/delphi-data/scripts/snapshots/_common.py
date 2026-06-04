"""Shared utilities for all experiment snapshot scripts.

Provides data loading, subject-ID inference, odor-mapping construction,
poke-statistics computation, and figure-saving helpers used across every
experiment-specific snapshot module.
"""

from __future__ import annotations

import pathlib
from typing import Any, Dict, Tuple

import matplotlib.pyplot as plt
import pandas as pd


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_dataset(data_root: pathlib.Path) -> pd.DataFrame:
    """Load ``behavior/delphi_dataset.csv`` from *data_root*.

    Adds a ``source_path`` column and creates a ``poke_duration`` alias from
    ``poke_to_beam_offset_duration`` when the former column is absent.

    Parameters
    ----------
    data_root:
        Run-level directory that contains ``behavior/delphi_dataset.csv``.

    Returns
    -------
    pd.DataFrame

    Raises
    ------
    FileNotFoundError
        If the CSV does not exist under *data_root*.
    """
    csv_path = data_root / "behavior" / "delphi_dataset.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Dataset CSV not found: {csv_path}\n"
            "Run 'delphi-data build-dataset' first."
        )
    df = pd.read_csv(csv_path)
    df["source_path"] = str(csv_path)
    if "poke_duration" not in df.columns and "poke_to_beam_offset_duration" in df.columns:
        df["poke_duration"] = df["poke_to_beam_offset_duration"]
    return df


def infer_subject_id(data_root: pathlib.Path) -> str:
    """Infer subject ID from the standard ``<subject>/<session>/<run>`` hierarchy.

    Parameters
    ----------
    data_root:
        Run-level directory (innermost timestamp directory).

    Returns
    -------
    str
        The subject directory name, or the run-directory name as fallback when
        the hierarchy is shallower than three levels.
    """
    parts = data_root.resolve().parts
    return parts[-3] if len(parts) >= 3 else data_root.name


def build_odor_mapping(df: pd.DataFrame) -> dict:
    """Build an integer-keyed odor mapping from ``odor`` and ``odor_name`` columns.

    Parameters
    ----------
    df:
        Dataframe containing ``odor`` (numeric) and ``odor_name`` columns.
        Only rows where ``poke_registered == True`` are used.

    Returns
    -------
    dict
        ``{int(odor_value): odor_name}`` for every unique registered odor.
        Returns an empty dict when either column is absent.
    """
    if "odor_name" not in df.columns or "odor" not in df.columns:
        return {}
    reg = df[df.get("poke_registered", pd.Series(True, index=df.index)) == True]
    pairs = reg[["odor", "odor_name"]].dropna().drop_duplicates()
    return {int(row["odor"]): row["odor_name"] for _, row in pairs.iterrows()}


# ---------------------------------------------------------------------------
# Poke-stats helpers
# ---------------------------------------------------------------------------


def build_poke_stats(
    df: pd.DataFrame,
    subject_id: str,
    source_path: str,
    odor_mapping: dict,
    tau: float = 600.0,
    dt: float = 60.0,
    overlap: float = 0.5,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Build the ``poke_stats`` dict for a single subject/session.

    Calls :func:`~delphi_data.poke_metrics.odor_change_events`,
    :func:`~delphi_data.poke_metrics.compute_poke_stats`, and
    :func:`~delphi_data.poke_metrics.compute_ipi_thresholds`.

    When no external odor-switch times are available, the poke-detected change
    times (``first_poke_t[1:]``) are used as a proxy for ``odor_switch_t`` so
    that switch-aligned analyses can run without manual metadata.

    Parameters
    ----------
    df:
        Per-poke event dataframe with ``subject_id`` and ``source_path``
        columns already set.
    subject_id:
        Subject identifier.
    source_path:
        Absolute path to the session CSV (used as the dict key).
    odor_mapping:
        ``{int(odor_code): label}`` mapping.
    tau:
        Exponential decay time constant for the rate estimator (seconds).
    dt:
        Window length for the rate estimator (seconds).
    overlap:
        Fractional window overlap ``[0, 1)``.

    Returns
    -------
    dict
        ``poke_stats`` dict keyed by ``(subject_id, source_path)``.
    """
    from delphi_data.poke_metrics import (
        compute_ipi_thresholds,
        compute_poke_stats,
        odor_change_events,
    )

    changes, odor_ids, odor_switch_t = odor_change_events(
        df, subject_id, source_path, odor_mapping, odor_switch_times=None,
    )

    odor_events = {
        (subject_id, source_path): {
            "first_poke_t": changes,
            "odor_id": odor_ids,
            "odor_switch_t": odor_switch_t,
        }
    }

    poke_stats = compute_poke_stats(
        df=df, odor_events=odor_events, tau=tau, dt=dt, overlap=overlap,
    )

    compute_ipi_thresholds(poke_stats)

    # Inject proxy odor_switch_t when none were externally supplied.
    for stats in poke_stats.values():
        if not stats["odor_switch_t"] and len(stats["first_poke_t"]) > 1:
            stats["odor_switch_t"] = list(stats["first_poke_t"][1:])

    return poke_stats


# ---------------------------------------------------------------------------
# QC pipeline
# ---------------------------------------------------------------------------


def run_qc(
    df: pd.DataFrame,
    result_dir: pathlib.Path,
    subject_id: str = "unknown",
    data_root: pathlib.Path | None = None,
    camera_fps_override: float | None = None,
) -> None:
    """Generate all standard QC figures for a session.

    Delegates to :func:`delphi_data.quality_control.run_qc_plots`.
    Figures are saved to ``result_dir/qc/``.

    Camera frame rate resolution (highest priority first)
    -----------------------------------------------------
    1. *camera_fps_override* — explicit value from the caller / CLI flag.
    2. Harp ``FrameRate`` register (hardware ground truth).
    3. ``AindBehaviorPirouetteRig.json`` — rig configuration file.
    4. ``HardwareSettings*.jsonl`` — software fallback.
    5. :data:`~delphi_data.quality_control.DEFAULT_CAMERA_FPS` (60 Hz).

    Parameters
    ----------
    df:
        Per-poke event DataFrame as loaded from ``delphi_dataset.csv``.
    result_dir:
        Parent results directory.  QC figures go into a ``qc/`` sub-folder.
    subject_id:
        Subject identifier used in figure suptitles.
    data_root:
        Run-level session directory (parent of ``behavior/`` and
        ``behavior-videos/``).  When provided, camera frame-rate QC is
        generated in addition to the poke and valve figures.
    camera_fps_override:
        Manually specified camera frame rate (Hz).  Overrides the Harp
        register, HardwareSettings, and the built-in default.
    """
    from delphi_data.quality_control import run_qc_plots

    qc_dir = result_dir / "qc"
    run_qc_plots(
        df,
        result_dir=qc_dir,
        subject_id=subject_id,
        data_root=data_root,
        camera_fps_override=camera_fps_override,
    )


# ---------------------------------------------------------------------------
# Figure saving
# ---------------------------------------------------------------------------


def save_figure(fig: plt.Figure, path: pathlib.Path, title: str) -> None:
    """Add a suptitle, tighten layout, save as PNG, and close *fig*.

    Parameters
    ----------
    fig:
        Figure to save.
    path:
        Destination PNG path.
    title:
        Super-title text placed above the figure.
    """
    fig.suptitle(title, fontsize=13, y=1.01)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path.name}")


def try_save(
    plot_fn,
    path: pathlib.Path,
    title: str,
    *args,
    **kwargs,
) -> None:
    """Call *plot_fn*, save the returned figure, and catch errors gracefully.

    A warning is printed and execution continues if *plot_fn* raises any
    exception, ensuring one failing plot never blocks the rest of the snapshot.

    Parameters
    ----------
    plot_fn:
        Callable that returns a ``matplotlib.figure.Figure``.
    path:
        Destination PNG path.
    title:
        Super-title for the figure.
    *args:
        Positional arguments forwarded to *plot_fn*.
    **kwargs:
        Keyword arguments forwarded to *plot_fn*.
    """
    try:
        fig = plot_fn(*args, **kwargs)
        save_figure(fig, path, title)
    except Exception as exc:
        print(f"  Skipped {path.name}: {exc}")
