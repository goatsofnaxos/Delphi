"""Data quality control metrics and plots for Delphi behavioral sessions.

Provides functions for parsing valve-state transition logs, computing per-session
QC summary statistics, and generating a standard suite of QC figures that are
produced for every session regardless of experiment type.

The QC figures saved by :func:`run_qc_plots` are:

- ``qc_valve_state_durations.png`` — per-state duration histograms (log x-axis)
- ``qc_valve_state_frequencies.png`` — valve state appearance counts
- ``qc_poke_timing.png`` — beam-break duration, poke duration, state-machine
  duration, and inter-poke interval distributions
- ``qc_session_summary.png`` — single-panel text dashboard of key session counts
- ``qc_camera_frame_rate.png`` — per-camera inter-frame interval histogram and
  instantaneous FPS time series, with configured target FPS as reference
- ``qc_odor_transitions.png`` — annotated heatmap of odor-to-odor transition
  frequencies across all registered pokes

Typical usage::

    from delphi_data.quality_control import run_qc_plots
    import pandas as pd, pathlib

    df = pd.read_csv("behavior/delphi_dataset.csv")
    run_qc_plots(df, result_dir=pathlib.Path("behavior/results/qc"))
"""

from __future__ import annotations

import ast
import pathlib
import warnings
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Camera frame-rate default
# ---------------------------------------------------------------------------

def _resolve_default_fps() -> float:
    """Read DEFAULT_CAMERA_FPS from settings, falling back to 60.0."""
    try:
        from delphi_data.settings import settings as _s
        return _s.default_camera_fps
    except Exception:
        return 60.0


DEFAULT_CAMERA_FPS: float = _resolve_default_fps()
"""Default camera trigger frequency (Hz) used when no other source is found.

Resolution order in :func:`run_qc_plots`:

1. Manual override (``camera_fps_override`` argument)
2. Harp ``FrameRate`` register — hardware ground truth
3. ``AindBehaviorPirouetteRig.json`` — rig configuration file
4. ``HardwareSettings*.jsonl`` — software fallback (may be inaccurate)
5. This constant — last resort

Override at call time via the ``camera_fps_override`` argument of
:func:`run_qc_plots` or the ``--camera-fps`` CLI flag.
"""


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_valve_transitions(df: pd.DataFrame) -> pd.DataFrame:
    """Parse the ``valve_transition_values`` and ``valve_transition_durations`` columns.

    Both columns are stored as stringified Python lists
    (e.g. ``"[4, 5, 4, 2, 3, 0, 4]"``).  This function evaluates them and
    returns a long-format DataFrame with one row per individual valve-state
    transition.

    Valve transition lists follow a fixed structure per poke::

        [pre-dwell, ...active sequence..., post-dwell]

    The *first* entry (position 0) is the "odor-ready" dwell carried over from
    the end of the previous poke's valve sequence.  Its duration equals the
    elapsed time since the last valve sequence completed and is therefore an
    inter-poke interval.  The *last* entry is the same "odor-ready" state
    persisting until the *next* poke, with duration equal to the subsequent
    inter-poke interval.  Both boundary entries are tagged
    ``is_interpoke = True``; everything in between is the active poke-sequence
    (odor delivery, vacuum close, vacuum setup, final valve vacuum).

    Parameters
    ----------
    df:
        Per-poke event DataFrame as loaded from ``delphi_dataset.csv``.
        Must contain ``valve_transition_values``, ``valve_transition_durations``,
        and ``poke_registered`` columns.

    Returns
    -------
    pd.DataFrame
        Long-format DataFrame with columns:

        - ``poke_index`` — row index of the originating poke in *df*
        - ``transition_index`` — position within the poke's transition list (0-based)
        - ``valve_state`` — integer valve-state bitmask
        - ``duration_s`` — duration of this state (seconds)
        - ``is_interpoke`` — ``True`` for boundary dwell transitions (position 0
          and position -1); ``False`` for active poke-sequence steps
        - ``poke_registered`` — copied from the source row
    """
    records: List[Dict[str, Any]] = []

    for idx, row in df[["valve_transition_values", "valve_transition_durations", "poke_registered"]].iterrows():
        try:
            states = ast.literal_eval(row["valve_transition_values"])
            durs = ast.literal_eval(row["valve_transition_durations"])
        except (ValueError, SyntaxError, TypeError):
            continue

        if not states or not durs or len(states) != len(durs):
            continue

        n = len(states)
        for ti, (s, d) in enumerate(zip(states, durs)):
            # Both the first (pre-poke dwell) and last (post-poke dwell) are
            # inter-poke boundary states, not active poke-sequence steps.
            is_boundary = (ti == 0) or (ti == n - 1)
            records.append({
                "poke_index": idx,
                "transition_index": ti,
                "valve_state": int(s),
                "duration_s": float(d),
                "is_interpoke": is_boundary,
                "poke_registered": row["poke_registered"],
            })

    if not records:
        return pd.DataFrame(columns=[
            "poke_index", "transition_index", "valve_state",
            "duration_s", "is_interpoke", "poke_registered",
        ])

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------


def compute_session_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute high-level session statistics for the QC dashboard.

    Parameters
    ----------
    df:
        Per-poke event DataFrame as loaded from ``delphi_dataset.csv``.

    Returns
    -------
    dict
        Dictionary with the following keys:

        - ``total_rows`` — total rows in *df*
        - ``pokes_registered`` — number of registered poke events
        - ``pokes_unregistered`` — number of beam breaks not counted as pokes
        - ``registration_rate_pct`` — percentage of beam breaks that were pokes
        - ``session_duration_hr`` — session duration in hours (beam-break span)
        - ``odors_detected`` — sorted list of unique odor names
        - ``n_odor_changes`` — number of detected odor transitions
        - ``median_poke_duration_s`` — median poke duration (seconds)
        - ``median_beam_break_duration_s`` — median beam break duration (seconds)
        - ``median_ipi_s`` — median inter-poke interval (seconds)
    """
    reg = df[df["poke_registered"] == True]
    total = len(df)
    n_reg = len(reg)
    n_unreg = total - n_reg
    rate = 100.0 * n_reg / total if total > 0 else 0.0

    # Session duration
    if "beam_break_onset" in df.columns and df["beam_break_onset"].notna().any():
        t0 = df["beam_break_onset"].min()
        t1 = df["beam_break_onset"].max()
        session_hr = (t1 - t0) / 3600.0
    else:
        session_hr = float("nan")

    # Odors
    odors: List[str] = []
    n_odor_changes = 0
    if "odor_name" in df.columns:
        odors = sorted(reg["odor_name"].dropna().unique().tolist())
        odor_series = reg["odor_name"].dropna()
        n_odor_changes = int((odor_series != odor_series.shift()).sum()) - 1

    # Poke durations
    dur_col = (
        "poke_duration"
        if "poke_duration" in reg.columns
        else "poke_to_beam_offset_duration"
    )
    med_poke = float(reg[dur_col].dropna().median()) if dur_col in reg.columns else float("nan")
    med_bb = float(reg["beam_break_duration"].dropna().median()) if "beam_break_duration" in reg.columns else float("nan")

    # IPI
    poke_onsets = reg["poke_onset"].dropna().sort_values().to_numpy()
    med_ipi = float(np.median(np.diff(poke_onsets))) if len(poke_onsets) > 1 else float("nan")

    return {
        "total_rows": total,
        "pokes_registered": n_reg,
        "pokes_unregistered": n_unreg,
        "registration_rate_pct": rate,
        "session_duration_hr": session_hr,
        "odors_detected": odors,
        "n_odor_changes": max(n_odor_changes, 0),
        "median_poke_duration_s": med_poke,
        "median_beam_break_duration_s": med_bb,
        "median_ipi_s": med_ipi,
    }


def compute_valve_state_stats(transitions: pd.DataFrame) -> pd.DataFrame:
    """Compute per-valve-state duration statistics from parsed transition data.

    Parameters
    ----------
    transitions:
        Long-format transitions DataFrame as returned by
        :func:`parse_valve_transitions`.

    Returns
    -------
    pd.DataFrame
        One row per unique valve state with columns ``valve_state``, ``count``,
        ``median_s``, ``mean_s``, ``std_s``, ``min_s``, ``max_s``,
        ``p5_s``, and ``p95_s`` (5th and 95th percentile durations).
    """
    if transitions.empty:
        return pd.DataFrame()

    rows = []
    for state, grp in transitions.groupby("valve_state"):
        d = grp["duration_s"].values
        rows.append({
            "valve_state": int(state),
            "count": len(d),
            "median_s": float(np.median(d)),
            "mean_s": float(np.mean(d)),
            "std_s": float(np.std(d, ddof=1)) if len(d) > 1 else 0.0,
            "min_s": float(d.min()),
            "max_s": float(d.max()),
            "p5_s": float(np.percentile(d, 5)),
            "p95_s": float(np.percentile(d, 95)),
        })

    return pd.DataFrame(rows).sort_values("valve_state").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Shared plot helper
# ---------------------------------------------------------------------------


def _prob_hist(
    ax: plt.Axes,
    data: np.ndarray,
    bins: int | np.ndarray = 40,
    log_x: bool = False,
    color: str = "steelblue",
    title: str = "",
    xlabel: str = "",
    vlines: Optional[List[Tuple[float, str]]] = None,
) -> None:
    """Draw a probability-normalised histogram with a jitter scatter band.

    The y-axis shows proportion per bin (sum of all bars = 1).  A thin
    scatter band of individual data points is drawn just above the tallest
    bar to reveal outliers and density fine-structure.

    Parameters
    ----------
    ax:
        Matplotlib ``Axes`` to draw on.
    data:
        1-D array of values.  NaN values are dropped automatically.
    bins:
        Number of histogram bins, or an explicit bin-edge array.
    log_x:
        When ``True``, use log-spaced bins and a log x-axis.
    color:
        Bar fill colour.
    title:
        Axes title.
    xlabel:
        X-axis label.
    vlines:
        Optional list of ``(x_value, label)`` tuples to draw as vertical
        reference lines.
    """
    data = data[np.isfinite(data)]
    if data.size == 0:
        ax.set_title(f"{title} (no data)")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("Proportion per bin")
        return

    weights = np.ones_like(data, dtype=float) / data.size

    if log_x and data.min() > 0:
        lo, hi = np.log10(data.min()), np.log10(data.max())
        # Use linear bins if range is very narrow (< 0.5 decades) to avoid empty bins
        if hi - lo < 0.5:
            bin_edges = np.linspace(data.min(), data.max(), int(bins) if isinstance(bins, int) else len(bins))
            ax.set_xscale("linear")
        else:
            bin_edges = np.logspace(lo, hi, int(bins) if isinstance(bins, int) else len(bins))
            ax.set_xscale("log")
    else:
        bin_edges = bins

    probs, _, _ = ax.hist(
        data,
        bins=bin_edges,
        weights=weights,
        color=color,
        alpha=0.75,
        edgecolor="white",
        linewidth=0.4,
    )

    # Jitter scatter above tallest bar
    y_top = probs.max() if probs.size else 0.0
    y_scatter = y_top * 1.08 if y_top > 0 else 0.05
    ax.scatter(data, np.full(data.size, y_scatter), s=3, color="black", alpha=0.3, zorder=3)

    if vlines:
        for xv, label in vlines:
            ax.axvline(xv, color="crimson", linestyle="--", linewidth=1.2, label=label)
        ax.legend(fontsize=7, frameon=False)

    ax.set_title(title, fontsize=9)
    ax.set_xlabel(xlabel, fontsize=8)
    ax.set_ylabel("Proportion per bin", fontsize=8)
    ax.set_ylim(0, min(1.0, y_top * 1.35) if y_top > 0 else 0.1)
    ax.tick_params(labelsize=7)
    # Limit major tick density on log axes to prevent label collisions
    if ax.get_xscale() == "log":
        ax.xaxis.set_major_locator(plt.LogLocator(base=10, numticks=6))
        ax.xaxis.set_minor_locator(plt.NullLocator())
    ax.tick_params(axis="x", rotation=30, labelsize=7)


# ---------------------------------------------------------------------------
# QC figures
# ---------------------------------------------------------------------------


def plot_valve_state_duration_histograms(
    transitions: pd.DataFrame,
    max_within_poke_s: float = 10.0,
    bins: int = 40,
) -> plt.Figure:
    """Plot per-valve-state duration histograms.

    One subplot is drawn for each unique valve state.  Within-poke transitions
    (``is_interpoke == False``) are shown in blue; the corresponding inter-poke
    dwell transitions (``is_interpoke == True``) are excluded from individual
    panels and shown together in a dedicated "inter-poke dwell" panel.

    The x-axis uses a log scale to accommodate both sub-millisecond and
    multi-second durations in the same panel.

    Parameters
    ----------
    transitions:
        Long-format transitions DataFrame from :func:`parse_valve_transitions`.
    max_within_poke_s:
        Upper clip for within-poke durations on the individual panels.
        Outliers above this threshold are still plotted but on the log axis.
    bins:
        Number of histogram bins per panel.

    Returns
    -------
    matplotlib.figure.Figure
    """
    if transitions.empty:
        fig, ax = plt.subplots()
        ax.text(0.5, 0.5, "No transition data", ha="center", va="center", transform=ax.transAxes)
        return fig

    within = transitions[~transitions["is_interpoke"]]
    interpoke = transitions[transitions["is_interpoke"]]

    # Only plot states that have enough observations to form a meaningful histogram.
    MIN_COUNT = 5
    state_counts = within["valve_state"].value_counts()
    states = sorted(state_counts[state_counts >= MIN_COUNT].index.tolist())

    n_panels = len(states) + (1 if not interpoke.empty else 0)
    ncols = min(4, n_panels)
    nrows = int(np.ceil(n_panels / ncols))

    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 3 * nrows), squeeze=False)
    ax_flat = axes.flatten()

    for i, state in enumerate(states):
        d = within[within["valve_state"] == state]["duration_s"].to_numpy()
        _prob_hist(
            ax_flat[i],
            d,
            bins=bins,
            log_x=True,
            color="steelblue",
            title=f"State {state}  (n={len(d):,})",
            xlabel="Duration (s)",
        )

    if not interpoke.empty:
        d_ip = interpoke["duration_s"].to_numpy()
        _prob_hist(
            ax_flat[len(states)],
            d_ip,
            bins=bins,
            log_x=True,
            color="slategray",
            title=f"Inter-poke dwell  (n={len(d_ip):,})",
            xlabel="Duration (s)",
        )

    # Hide unused panels
    for j in range(n_panels, len(ax_flat)):
        ax_flat[j].set_visible(False)

    fig.tight_layout()
    return fig


def plot_valve_state_frequencies(
    transitions: pd.DataFrame,
    exclude_interpoke: bool = True,
) -> plt.Figure:
    """Plot a bar chart of valve-state appearance counts.

    Parameters
    ----------
    transitions:
        Long-format transitions DataFrame from :func:`parse_valve_transitions`.
    exclude_interpoke:
        When ``True`` (default), the inter-poke dwell transition (last in each
        poke's list) is excluded so the chart reflects within-poke state usage.

    Returns
    -------
    matplotlib.figure.Figure
    """
    if transitions.empty:
        fig, ax = plt.subplots()
        ax.text(0.5, 0.5, "No transition data", ha="center", va="center", transform=ax.transAxes)
        return fig

    data = transitions[~transitions["is_interpoke"]] if exclude_interpoke else transitions
    counts = data["valve_state"].value_counts().sort_index()

    fig, ax = plt.subplots(figsize=(max(5, len(counts) * 0.8), 4))
    ax.bar(counts.index.astype(str), counts.values, color="steelblue", edgecolor="white", linewidth=0.5)
    ax.set_xlabel("Valve state (bitmask)", fontsize=9)
    ax.set_ylabel("Count", fontsize=9)
    ax.set_title(
        "Valve State Transition Frequencies"
        + (" (within-poke only)" if exclude_interpoke else ""),
        fontsize=10,
    )
    for x, y in zip(range(len(counts)), counts.values):
        ax.text(x, y + counts.max() * 0.01, f"{y:,}", ha="center", va="bottom", fontsize=7)
    ax.tick_params(labelsize=8)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig


def plot_poke_timing_overview(
    df: pd.DataFrame,
    bins: int = 40,
) -> plt.Figure:
    """Plot distributions of key poke-timing metrics.

    Four panels:

    1. Beam-break duration
    2. Poke duration (``poke_to_beam_offset_duration`` or ``poke_duration``)
    3. State-machine duration (time from poke onset to valve sequence completion)
    4. Inter-poke interval (difference between consecutive poke onsets)

    Parameters
    ----------
    df:
        Per-poke event DataFrame as loaded from ``delphi_dataset.csv``.
    bins:
        Number of histogram bins per panel.

    Returns
    -------
    matplotlib.figure.Figure
    """
    reg = df[df["poke_registered"] == True] if "poke_registered" in df.columns else df
    dur_col = "poke_duration" if "poke_duration" in reg.columns else "poke_to_beam_offset_duration"

    # Inter-poke interval
    poke_onsets = reg["poke_onset"].dropna().sort_values().to_numpy()
    ipi = np.diff(poke_onsets) if len(poke_onsets) > 1 else np.array([])

    # Configured minimum poke time reference line
    vline_poke: List[Tuple[float, str]] = []
    if "MinimumPokeTimeUS" in df.columns:
        min_poke_s = df["MinimumPokeTimeUS"].dropna().iloc[0] if df["MinimumPokeTimeUS"].notna().any() else None
        if min_poke_s is not None and min_poke_s < 1:
            vline_poke = [(min_poke_s, f"Min poke time ({min_poke_s:.3f} s)")]

    fig, axes = plt.subplots(2, 2, figsize=(11, 7))

    _prob_hist(
        axes[0, 0],
        reg["beam_break_duration"].dropna().to_numpy(),
        bins=bins,
        log_x=True,
        color="steelblue",
        title="Beam-Break Duration",
        xlabel="Duration (s)",
    )
    _prob_hist(
        axes[0, 1],
        reg[dur_col].dropna().to_numpy(),
        bins=bins,
        log_x=True,
        color="steelblue",
        title="Poke Duration",
        xlabel="Duration (s)",
        vlines=vline_poke or None,
    )
    _prob_hist(
        axes[1, 0],
        reg["state_machine_duration"].dropna().to_numpy() if "state_machine_duration" in reg.columns else np.array([]),
        bins=bins,
        log_x=True,
        color="mediumseagreen",
        title="State-Machine Duration",
        xlabel="Duration (s)",
    )
    _prob_hist(
        axes[1, 1],
        ipi,
        bins=bins,
        log_x=True,
        color="darkorange",
        title="Inter-Poke Interval",
        xlabel="Interval (s)",
    )

    fig.tight_layout()
    return fig


def plot_session_summary(
    summary: Dict[str, Any],
    transitions_stats: Optional[pd.DataFrame] = None,
) -> plt.Figure:
    """Render a text-based QC summary dashboard for a single session.

    Parameters
    ----------
    summary:
        Dict returned by :func:`compute_session_summary`.
    transitions_stats:
        Optional DataFrame returned by :func:`compute_valve_state_stats`.
        When provided, a compact valve-state timing table is appended.

    Returns
    -------
    matplotlib.figure.Figure
    """
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.axis("off")

    lines = [
        "SESSION QUALITY CONTROL SUMMARY",
        "─" * 45,
        f"Total rows:              {summary['total_rows']:,}",
        f"Pokes registered:        {summary['pokes_registered']:,}",
        f"Pokes unregistered:      {summary['pokes_unregistered']:,}",
        f"Registration rate:       {summary['registration_rate_pct']:.1f} %",
        f"Session duration:        {summary['session_duration_hr']:.2f} h",
        f"Odors detected:          {', '.join(summary['odors_detected']) or 'n/a'}",
        f"Odor changes detected:   {summary['n_odor_changes']}",
        "─" * 45,
        f"Median poke duration:    {summary['median_poke_duration_s']:.4f} s",
        f"Median beam-break dur.:  {summary['median_beam_break_duration_s']:.4f} s",
        f"Median IPI:  {summary['median_ipi_s']:.4f} s",
    ]

    if transitions_stats is not None and not transitions_stats.empty:
        lines += ["─" * 45, "Valve-state duration summary (within-poke):"]
        header = f"  {'State':>6}  {'n':>6}  {'median':>9}  {'mean':>9}  {'p5':>9}  {'p95':>9}"
        lines.append(header)
        for _, r in transitions_stats.iterrows():
            lines.append(
                f"  {int(r['valve_state']):>6}  {int(r['count']):>6}  "
                f"{r['median_s']:>9.4f}  {r['mean_s']:>9.4f}  "
                f"{r['p5_s']:>9.4f}  {r['p95_s']:>9.4f}"
            )

    ax.text(
        0.03, 0.97,
        "\n".join(lines),
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontfamily="monospace",
        fontsize=9,
        linespacing=1.55,
    )
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Camera frame-rate QC
# ---------------------------------------------------------------------------


def discover_cameras(behavior_videos_dir: str | pathlib.Path) -> List[Dict[str, Any]]:
    """Discover camera sub-directories inside *behavior_videos_dir*.

    Each immediate sub-directory that contains at least one ``*.csv`` file is
    treated as a camera folder.  The camera name is the sub-directory name.

    Parameters
    ----------
    behavior_videos_dir:
        ``behavior-videos/`` directory of the session.

    Returns
    -------
    list of dict
        One dict per camera with keys ``"name"`` (str) and ``"dir"``
        (``pathlib.Path``).  Empty list when *behavior_videos_dir* does not
        exist or contains no camera sub-directories.
    """
    bv_dir = pathlib.Path(behavior_videos_dir)
    if not bv_dir.is_dir():
        return []
    cameras = []
    for sub in sorted(bv_dir.iterdir()):
        if sub.is_dir() and any(sub.glob("*.csv")):
            cameras.append({"name": sub.name, "dir": sub})
    return cameras


def load_camera_frame_timestamps(
    camera_dir: pathlib.Path,
    camera_name: str,
) -> Optional[np.ndarray]:
    """Load all frame timestamps for one camera as a sorted array of seconds.

    Uses :func:`delphi_data.ingestion.load_camera_frames` (which calls the
    Aeon ``Video`` reader) and converts the resulting ``DatetimeIndex`` to
    Harp seconds via :func:`swc.aeon.io.api.to_seconds`.

    Parameters
    ----------
    camera_dir:
        Directory containing the camera's ``*.csv`` frame-index files.
    camera_name:
        Camera name prefix used to glob the index files.

    Returns
    -------
    np.ndarray or None
        1-D array of frame timestamps in seconds (Harp clock), sorted
        ascending.  ``None`` when no data are found.
    """
    from delphi_data.ingestion import load_camera_frames
    from swc.aeon.io import api as aeon_api

    data = load_camera_frames(camera_dir, camera_name)
    if data is None or data.empty:
        return None
    return np.sort(aeon_api.to_seconds(data.index).to_numpy())


def compute_camera_frame_rate_stats(
    timestamps_s: np.ndarray,
    target_fps: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute frame-rate QC statistics from a sorted array of frame timestamps.

    Parameters
    ----------
    timestamps_s:
        Sorted 1-D array of frame timestamps in seconds.
    target_fps:
        Configured target frame rate (Hz).  When provided, a dropout fraction
        (proportion of inter-frame intervals more than 50 % above the target
        interval) is also computed.

    Returns
    -------
    dict
        Statistics dictionary with keys:

        - ``n_frames`` — total number of frames
        - ``duration_s`` — recording span in seconds
        - ``mean_fps`` — mean frame rate (1 / mean IFI)
        - ``median_fps`` — median frame rate (1 / median IFI)
        - ``std_ifi_ms`` — standard deviation of inter-frame intervals (ms)
        - ``min_ifi_ms`` — minimum inter-frame interval (ms)
        - ``max_ifi_ms`` — maximum inter-frame interval (ms)
        - ``target_fps`` — configured target frame rate (or ``None``)
        - ``dropout_pct`` — proportion of IFIs > 1.5× target interval (pct),
          or ``None`` when *target_fps* is not provided
    """
    if timestamps_s.size < 2:
        return {
            "n_frames": len(timestamps_s),
            "duration_s": 0.0,
            "mean_fps": float("nan"),
            "median_fps": float("nan"),
            "std_ifi_ms": float("nan"),
            "min_ifi_ms": float("nan"),
            "max_ifi_ms": float("nan"),
            "target_fps": target_fps,
            "dropout_pct": None,
        }

    ifi = np.diff(timestamps_s)
    dropout_pct = None
    if target_fps is not None and target_fps > 0:
        target_ifi = 1.0 / target_fps
        dropout_pct = 100.0 * np.mean(ifi > 1.5 * target_ifi)

    return {
        "n_frames": len(timestamps_s),
        "duration_s": float(timestamps_s[-1] - timestamps_s[0]),
        "mean_fps": float(1.0 / np.mean(ifi)),
        "median_fps": float(1.0 / np.median(ifi)),
        "std_ifi_ms": float(np.std(ifi) * 1000),
        "min_ifi_ms": float(ifi.min() * 1000),
        "max_ifi_ms": float(ifi.max() * 1000),
        "target_fps": target_fps,
        "dropout_pct": dropout_pct,
    }


def plot_camera_frame_rate_qc(
    camera_data: List[Dict[str, Any]],
    bins: int = 60,
) -> plt.Figure:
    """Plot per-camera frame-rate QC: IFI histogram, FPS histogram, and FPS time series.

    Layout: one row per camera with three panels.

    - **Left** — probability-normalised histogram of inter-frame intervals
      (IFI, ms).  A dashed red line marks the target IFI.
    - **Centre** — probability-normalised histogram of instantaneous frame
      rates (FPS).  A dashed red line marks the target FPS.
    - **Right** — instantaneous FPS plotted over elapsed session time, with
      the target FPS as a horizontal reference.

    Parameters
    ----------
    camera_data:
        List of dicts, each describing one camera.  Required keys:

        - ``"name"`` — camera display name
        - ``"timestamps_s"`` — sorted np.ndarray of frame timestamps (seconds)
        - ``"target_fps"`` — configured frame rate in Hz (or ``None``)
    bins:
        Number of histogram bins for both the IFI and FPS distribution panels.

    Returns
    -------
    matplotlib.figure.Figure
    """
    n_cams = len(camera_data)
    if n_cams == 0:
        fig, ax = plt.subplots(figsize=(6, 2))
        ax.text(0.5, 0.5, "No camera data found", ha="center", va="center",
                transform=ax.transAxes, fontsize=10)
        ax.axis("off")
        return fig

    fig, axes = plt.subplots(
        n_cams, 3,
        figsize=(18, 4 * n_cams),
        squeeze=False,
        gridspec_kw={"width_ratios": [1, 1, 2]},
    )

    for row, cam in enumerate(camera_data):
        name = cam["name"]
        ts = cam["timestamps_s"]
        target_fps: Optional[float] = cam.get("target_fps")
        ax_ifi, ax_fps_hist, ax_ts = axes[row, 0], axes[row, 1], axes[row, 2]

        if ts is None or len(ts) < 2:
            for ax in (ax_ifi, ax_fps_hist, ax_ts):
                ax.text(0.5, 0.5, "No frames", ha="center", va="center",
                        transform=ax.transAxes)
                ax.set_title(name)
            continue

        ifi_ms = np.diff(ts) * 1000.0       # inter-frame intervals in ms
        inst_fps = 1000.0 / ifi_ms          # instantaneous FPS
        t_elapsed_h = (ts[:-1] - ts[0]) / 3600.0

        target_ifi_ms = (1000.0 / target_fps) if target_fps else None

        # --- Panel 1: IFI histogram ---
        _prob_hist(
            ax_ifi,
            ifi_ms,
            bins=bins,
            log_x=False,
            color="steelblue",
            title=f"{name}\nIFI distribution",
            xlabel="Inter-frame interval (ms)",
            vlines=[(target_ifi_ms, f"Target ({target_ifi_ms:.2f} ms)")]
            if target_ifi_ms else None,
        )

        # --- Panel 2: FPS distribution histogram ---
        _prob_hist(
            ax_fps_hist,
            inst_fps,
            bins=bins,
            log_x=False,
            color="mediumseagreen",
            title=f"{name}\nFPS distribution",
            xlabel="Frame rate (Hz)",
            vlines=[(target_fps, f"Target ({target_fps:.1f} Hz)")]
            if target_fps else None,
        )

        # --- Panel 3: FPS time series ---
        ax_ts.plot(t_elapsed_h, inst_fps, lw=0.4, color="steelblue", alpha=0.7)
        if target_fps:
            ax_ts.axhline(
                target_fps, color="crimson", linestyle="--", linewidth=1.2,
                label=f"Target {target_fps:.0f} Hz",
            )
            ax_ts.legend(fontsize=8, frameon=False)

        stats = compute_camera_frame_rate_stats(ts, target_fps)
        info = (
            f"n={stats['n_frames']:,}   "
            f"median={stats['median_fps']:.2f} Hz   "
            f"σ_IFI={stats['std_ifi_ms']:.4f} ms"
        )
        if stats["dropout_pct"] is not None:
            info += f"   dropouts={stats['dropout_pct']:.2f}%"
        ax_ts.set_title(f"{name} — FPS over time\n{info}", fontsize=9)
        ax_ts.set_xlabel("Elapsed time (h)", fontsize=8)
        ax_ts.set_ylabel("FPS", fontsize=8)
        ax_ts.tick_params(labelsize=7)
        ax_ts.grid(axis="y", linestyle="--", alpha=0.4)

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Odor transition QC
# ---------------------------------------------------------------------------


def compute_odor_transition_matrix(
    df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Compute odor-to-odor transition counts and frequencies from registered pokes.

    A transition is defined as the pair ``(odor_name at poke N, odor_name at poke N+1)``
    across consecutive *registered* pokes.  Self-transitions (same odor repeated)
    are included in both the count and frequency matrices.

    Parameters
    ----------
    df:
        Per-poke event DataFrame as loaded from ``delphi_dataset.csv``.
        Must contain ``odor_name`` and ``poke_registered`` columns.

    Returns
    -------
    counts : pd.DataFrame
        Square integer matrix of raw transition counts.  Rows are the
        "from" odor, columns are the "to" odor.  Empty when fewer than
        two registered pokes with a known odor name are present.
    freq : pd.DataFrame
        Square float matrix of transition frequencies (fraction of all
        transitions, sums to 1 across the whole matrix).  Same shape and
        index/columns as *counts*.
    """
    if "odor_name" not in df.columns or "poke_registered" not in df.columns:
        return pd.DataFrame(), pd.DataFrame()

    # Keep only registered pokes with a resolved odor name
    reg_odors = (
        df.loc[df["poke_registered"] == True, "odor_name"]
        .dropna()
        .reset_index(drop=True)
    )
    if len(reg_odors) < 2:
        return pd.DataFrame(), pd.DataFrame()

    # All unique odors, sorted for stable axis ordering
    odors = sorted(reg_odors.unique().tolist())
    counts = pd.DataFrame(0, index=odors, columns=odors, dtype=int)

    # Tally consecutive (from, to) pairs
    from_odors = reg_odors.iloc[:-1].values
    to_odors = reg_odors.iloc[1:].values
    for from_o, to_o in zip(from_odors, to_odors):
        counts.loc[from_o, to_o] += 1

    total = int(counts.values.sum())
    freq: pd.DataFrame = counts / total if total > 0 else counts.astype(float)

    return counts, freq


def plot_odor_transition_matrix(
    freq_matrix: pd.DataFrame,
    counts_matrix: pd.DataFrame,
) -> plt.Figure:
    """Plot an annotated heatmap of odor-to-odor transition frequencies.

    Each cell shows the transition *frequency* (fraction of all transitions,
    0–1) with the raw count in parentheses.  Rows are "from" odors; columns
    are "to" odors.

    Parameters
    ----------
    freq_matrix:
        Square DataFrame of transition frequencies as returned by
        :func:`compute_odor_transition_matrix`.
    counts_matrix:
        Square DataFrame of raw transition counts (same shape).

    Returns
    -------
    matplotlib.figure.Figure
    """
    if freq_matrix.empty:
        fig, ax = plt.subplots(figsize=(5, 3))
        ax.text(
            0.5, 0.5, "No odor transition data",
            ha="center", va="center", transform=ax.transAxes, fontsize=10,
        )
        ax.axis("off")
        return fig

    n = len(freq_matrix)
    cell_size = max(1.6, 6.0 / max(n, 1))  # shrink gracefully for many odors
    figsize = (n * cell_size + 2.5, n * cell_size + 1.5)
    fig, ax = plt.subplots(figsize=figsize)

    data = freq_matrix.values.astype(float)
    vmax = data.max() if data.max() > 0 else 1.0

    im = ax.imshow(data, vmin=0, vmax=vmax, cmap="Blues", aspect="auto")

    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(freq_matrix.columns, rotation=45, ha="right", fontsize=9)
    ax.set_yticklabels(freq_matrix.index, fontsize=9)

    # Annotate every cell with frequency and count
    threshold = vmax * 0.55  # switch to white text above this intensity
    for i in range(n):
        for j in range(n):
            freq_val = float(freq_matrix.iloc[i, j])
            count_val = int(counts_matrix.iloc[i, j])
            if count_val == 0:
                continue
            text_color = "white" if freq_val > threshold else "black"
            ax.text(
                j, i,
                f"{freq_val:.3f}\n({count_val:,})",
                ha="center", va="center",
                color=text_color,
                fontsize=max(6, min(9, int(40 / max(n, 4)))),
            )

    cbar = plt.colorbar(im, ax=ax)
    cbar.set_label("Transition frequency", fontsize=9)
    ax.set_xlabel("To odor", fontsize=10)
    ax.set_ylabel("From odor", fontsize=10)
    ax.set_title("Odor Transition Frequency Matrix", fontsize=11)

    total_transitions = int(counts_matrix.values.sum())
    n_odors = len(freq_matrix)
    ax.text(
        0.01, -0.18,
        f"n = {total_transitions:,} transitions  |  {n_odors} odor(s)",
        transform=ax.transAxes,
        fontsize=8,
        color="dimgray",
    )

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Top-level pipeline
# ---------------------------------------------------------------------------


def run_qc_plots(
    df: pd.DataFrame,
    result_dir: str | pathlib.Path,
    subject_id: str = "unknown",
    data_root: Optional[str | pathlib.Path] = None,
    camera_fps_override: Optional[float] = None,
) -> None:
    """Generate all standard QC figures and save them to *result_dir*.

    This function is experiment-agnostic and should be called for every
    session snapshot regardless of experiment type.

    Camera frame rate resolution (highest priority first)
    -----------------------------------------------------
    1. *camera_fps_override* — explicit value supplied by the caller.
    2. Harp ``FrameRate`` register (address 76) — hardware ground truth.
    3. ``AindBehaviorPirouetteRig.json`` — rig configuration file.
    4. ``HardwareSettings*.jsonl`` in ``behavior/metadata/`` — software
       fallback; may not reflect the actual programmed rate.
    5. :data:`DEFAULT_CAMERA_FPS` — last-resort default (60 Hz).

    Figures written
    ---------------
    - ``qc_session_summary.png``
    - ``qc_poke_timing.png``
    - ``qc_valve_state_frequencies.png``
    - ``qc_valve_state_durations.png``
    - ``qc_odor_transitions.png`` *(when* ``odor_name`` *column is present)*
    - ``qc_camera_frame_rate.png`` *(when* ``session_dir`` *is provided and
      cameras are found)*

    Parameters
    ----------
    df:
        Per-poke event DataFrame as loaded from ``delphi_dataset.csv``.
    result_dir:
        Directory where QC figures are written.  Created if it does not exist.
    subject_id:
        Subject identifier shown in figure suptitles.
    data_root:
        Run-level session directory (parent of ``behavior/`` and
        ``behavior-videos/``).  When provided, camera frame-rate QC is also
        generated.
    camera_fps_override:
        Manually specified camera frame rate (Hz).  When set, this value
        overrides the Harp register, HardwareSettings, and the default.
        Useful when the register was never written or when testing a
        non-standard frame rate.
    """
    result_dir = pathlib.Path(result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)

    print("  Running QC pipeline …")

    # Parse valve transitions once
    transitions = parse_valve_transitions(df)
    within_transitions = (
        transitions[~transitions["is_interpoke"]] if not transitions.empty else transitions
    )

    # Summary stats
    summary = compute_session_summary(df)
    valve_stats = compute_valve_state_stats(within_transitions)

    def _save(fig: plt.Figure, fname: str, title: str) -> None:
        fig.suptitle(f"{title}  |  Subject: {subject_id}", fontsize=11, y=1.01)
        fig.tight_layout()
        fig.savefig(result_dir / fname, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"    Saved: {fname}")

    # 1 — Session summary
    fig = plot_session_summary(summary, valve_stats if not valve_stats.empty else None)
    _save(fig, "qc_session_summary.png", "Session QC Summary")

    # 2 — Poke timing
    fig = plot_poke_timing_overview(df)
    _save(fig, "qc_poke_timing.png", "Poke Timing Distributions")

    # 3 — Valve state frequencies
    if not transitions.empty:
        fig = plot_valve_state_frequencies(transitions)
        _save(fig, "qc_valve_state_frequencies.png", "Valve State Transition Frequencies")

        # 4 — Valve state duration histograms
        fig = plot_valve_state_duration_histograms(transitions)
        _save(fig, "qc_valve_state_durations.png", "Valve State Duration Histograms")
    else:
        print("    Skipped valve plots (no transition data parsed).")

    # 5 — Odor transition heatmap
    if "odor_name" in df.columns:
        counts_mat, freq_mat = compute_odor_transition_matrix(df)
        if not freq_mat.empty:
            fig = plot_odor_transition_matrix(freq_mat, counts_mat)
            _save(fig, "qc_odor_transitions.png", "Odor Transition Frequencies")
        else:
            print("    Skipped odor transition plot (fewer than 2 registered pokes with known odor).")
    else:
        print("    Skipped odor transition plot (no odor_name column).")

    # 6 — Camera frame-rate QC
    if data_root is not None:
        data_root = pathlib.Path(data_root)
        behavior_dir = data_root / "behavior"
        behavior_videos_dir = data_root / "behavior-videos"

        # Resolve target FPS with priority:
        #   1. manual override  2. Harp register  3. HardwareSettings  4. default
        fps_map: dict = {}
        fps_source: str = ""

        if camera_fps_override is not None:
            fps_map = {"_all": float(camera_fps_override)}
            fps_source = f"manual override ({camera_fps_override:.1f} Hz)"
        else:
            try:
                from delphi_data.ingestion import (
                    load_fps_from_harp,
                    load_fps_from_hardware_settings,
                    load_fps_from_rig_config,
                )
                harp_fps = load_fps_from_harp(behavior_dir)
                if harp_fps is not None:
                    fps_map = {"_all": harp_fps}
                    fps_source = f"Harp register ({harp_fps:.1f} Hz)"
                else:
                    rig_map = load_fps_from_rig_config(behavior_dir)
                    if rig_map:
                        fps_map = rig_map
                        fps_source = f"AindBehaviorPirouetteRig.json — {rig_map}"
                    else:
                        hw_map = load_fps_from_hardware_settings(behavior_dir)
                        if hw_map:
                            fps_map = hw_map
                            fps_source = f"HardwareSettings (fallback) — {hw_map}"
                        else:
                            fps_map = {"_all": DEFAULT_CAMERA_FPS}
                            fps_source = f"default ({DEFAULT_CAMERA_FPS:.1f} Hz)"
            except Exception as exc:
                fps_map = {"_all": DEFAULT_CAMERA_FPS}
                fps_source = f"default ({DEFAULT_CAMERA_FPS:.1f} Hz) — error: {exc}"

        print(f"    Target FPS source: {fps_source}")

        def _target_fps_for(camera_name: str) -> Optional[float]:
            """Resolve target FPS for *camera_name* using the resolved *fps_map*."""
            if camera_name in fps_map:
                return fps_map[camera_name]
            if "_all" in fps_map:
                return fps_map["_all"]
            return DEFAULT_CAMERA_FPS

        # Discover and load all cameras
        cameras = discover_cameras(behavior_videos_dir)
        if cameras:
            camera_data = []
            for cam in cameras:
                print(f"    Loading {cam['name']} frame timestamps …")
                ts = load_camera_frame_timestamps(cam["dir"], cam["name"])
                if ts is not None:
                    print(f"      {len(ts):,} frames")
                else:
                    print("      No frames found")
                camera_data.append({
                    "name": cam["name"],
                    "timestamps_s": ts,
                    "target_fps": _target_fps_for(cam["name"]),
                })

            fig = plot_camera_frame_rate_qc(camera_data)
            _save(fig, "qc_camera_frame_rate.png", "Camera Frame Rate QC")
        else:
            print("    Skipped camera QC (no behavior-videos cameras found).")
