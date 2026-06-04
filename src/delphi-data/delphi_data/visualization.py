"""Visualization functions for poke-port behavioral data.

All functions return a ``matplotlib.figure.Figure`` object so callers can
further customise or save the figure.  No figure is displayed or saved inside
these functions — that is left to the caller.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Optional, Tuple

import matplotlib.cm as cm
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import numpy as np

from delphi_data.poke_metrics import estimate_robust_ipi_threshold


# ---------------------------------------------------------------------------
# Legend helpers
# ---------------------------------------------------------------------------


def build_day_legend(
    pre_days: int,
    post_days: int,
    cmap_name: str = "viridis",
) -> list:
    """Build a list of ``Line2D`` legend handles colour-coded by day offset.

    Day 0 is always drawn in black at full linewidth; other days are drawn
    from *cmap_name*.

    Parameters
    ----------
    pre_days:
        Number of days before the change (positive integer).
    post_days:
        Number of days after the change.
    cmap_name:
        Matplotlib colourmap name for non-zero days.

    Returns
    -------
    list of matplotlib.lines.Line2D
        Legend handles ordered from ``-pre_days`` to ``+post_days``.
    """
    cmap = cm.get_cmap(cmap_name)
    days = list(range(-pre_days, post_days + 1))
    nonzero_days = [d for d in days if d != 0]
    color_map = dict(zip(nonzero_days, cmap(np.linspace(0, 1, len(nonzero_days)))))

    handles = []
    for d in days:
        if d == 0:
            handles.append(
                mlines.Line2D([], [], color="black", linewidth=3, label="Day 0")
            )
        else:
            handles.append(
                mlines.Line2D(
                    [],
                    [],
                    color=color_map[d],
                    linewidth=1.5,
                    alpha=0.8,
                    label=f"Day {d}",
                )
            )
    return handles


# ---------------------------------------------------------------------------
# Full-session poke-rate overview
# ---------------------------------------------------------------------------


def plot_poke_rate_timeseries(
    poke_stats: Dict[Tuple[str, str], Dict[str, Any]],
    time_unit: str = "seconds",
) -> plt.Figure:
    """Plot the smoothed poke-rate time series for every session.

    Each subplot shows one session.  Vertical dashed lines mark odor-change
    poke events, colour-coded by change index.

    Parameters
    ----------
    poke_stats:
        Output of :func:`~delphi_data.poke_metrics.compute_poke_stats`.
    time_unit:
        X-axis unit: ``"seconds"``, ``"minutes"``, ``"hours"``, or ``"days"``.
        Defaults to ``"seconds"``.

    Returns
    -------
    matplotlib.figure.Figure
        Figure with one row per session.

    Raises
    ------
    ValueError
        If *time_unit* is not one of the accepted values.
    """
    unit_scale = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0, "days": 86400.0}
    if time_unit not in unit_scale:
        raise ValueError("time_unit must be 'seconds', 'minutes', 'hours', or 'days'")
    scale = unit_scale[time_unit]
    time_label = {"seconds": "s", "minutes": "min", "hours": "h", "days": "days"}[time_unit]

    cmap = plt.get_cmap("Set1")
    fig, axes = plt.subplots(
        len(poke_stats), 1,
        figsize=(12, 4 * len(poke_stats)),
        sharex=False,
    )
    if len(poke_stats) == 1:
        axes = [axes]

    for i, ((subject, source_path), stats) in enumerate(poke_stats.items()):
        ax = axes[i]
        t_sample = stats["t"]
        r_poke = stats["poke_rate"]
        t_rel = (t_sample - np.min(t_sample)) / scale
        odor_change_evts = list(zip(stats["first_poke_t"], stats["odor_id"]))

        for j, (event_time, new_odor) in enumerate(odor_change_evts):
            ax.axvline(
                x=(event_time - np.min(t_sample)) / scale,
                color=cmap(j % 9),
                linestyle="--",
                label=f"Odor → {new_odor}",
            )

        ax.plot(t_rel, r_poke, color="black", lw=1)
        ax.set_xlabel(f"Time ({time_label})")
        ax.set_ylabel("Poke Rate (Hz)")
        ax.set_title(f"Poke Rate – Subject {subject}")
        ax.grid(True, ls="--", alpha=0.5)
        ax.legend(fontsize=8)

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Multi-day windows around odor changes (poke-event aligned)
# ---------------------------------------------------------------------------


def plot_multiday_poke_rate_windows(
    windows: Dict[Tuple[str, str, str, str], Dict[str, Any]],
    pre_days: int = 3,
    post_days: int = 1,
) -> plt.Figure:
    """Plot poke-rate windows around odor changes, colour-coded by day.

    Layout: rows = subject/session, columns = odor change.  Each panel
    overlays all days as coloured lines (day 0 in black).

    Parameters
    ----------
    windows:
        Output of :func:`~delphi_data.poke_metrics.extract_poke_rate_windows`.
    pre_days:
        Number of baseline days before the change (used for legend).
    post_days:
        Number of post-change days (used for legend).

    Returns
    -------
    matplotlib.figure.Figure
    """
    grouped: Dict[Any, Any] = defaultdict(lambda: defaultdict(list))
    for (subject, source_path, odor_change, day), data in windows.items():
        session_key = (subject, source_path)
        grouped[session_key][odor_change].append((day, data))

    sessions = list(grouped.keys())
    odor_changes = sorted({oc for s in grouped.values() for oc in s.keys()})
    n_rows, n_cols = len(sessions), len(odor_changes)

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(2.5 * n_cols, 4 * n_rows),
        sharex=True, sharey=True,
        squeeze=False,
    )

    cmap = plt.get_cmap("viridis")

    for r, session in enumerate(sessions):
        subject, source_path = session
        for c, odor_change in enumerate(odor_changes):
            ax = axes[r, c]
            if odor_change not in grouped[session]:
                ax.axis("off")
                continue

            entries = grouped[session][odor_change]
            entries.sort(key=lambda x: int(x[0].split("_")[1]))
            days = [int(day.split("_")[1]) for day, _ in entries]
            day_norm = np.linspace(0, 1, len(days))

            for (day, data), color in zip(entries, cmap(day_norm)):
                day_num = int(day.split("_")[1])
                if day_num == 0:
                    ax.plot(
                        data["t_window"], data["r_window"],
                        color="black", linewidth=2, zorder=10, label="Day 0",
                    )
                else:
                    ax.plot(
                        data["t_window"], data["r_window"],
                        color=color, linewidth=1.5, alpha=0.8, label=day,
                    )

            ax.axvline(0, color="k", linestyle="--", linewidth=1)
            if r == 0:
                ax.set_title(
                    f"{odor_change}\n{data['prev_odor']} → {data['new_odor']}"
                )
            if c == 0:
                ax.set_ylabel(f"Subject {subject}\nPoke rate (Hz)")
            if r == n_rows - 1:
                ax.set_xlabel("Time from odor change (s)")

    handles = build_day_legend(pre_days=abs(pre_days), post_days=post_days)
    fig.legend(
        handles=handles,
        title="Day relative to odor change",
        loc="upper right",
        bbox_to_anchor=(1.15, 0.9),
        frameon=False,
    )
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# IPI distributions
# ---------------------------------------------------------------------------


def plot_ipi_distributions(
    poke_stats: Dict[Tuple[str, str], Dict[str, Any]],
) -> plt.Figure:
    """Plot inter-poke interval (IPI) distributions with estimated bout threshold.

    For each session, a log-spaced histogram is drawn alongside the smoothed
    density curve and a vertical line at the robust IPI threshold (if
    available in ``poke_stats[key]["robust_ipi"]``; otherwise it is estimated
    on-the-fly).

    Parameters
    ----------
    poke_stats:
        Output of :func:`~delphi_data.poke_metrics.compute_poke_stats`.  If
        ``"robust_ipi"`` is not present in each session, it will be computed
        automatically.

    Returns
    -------
    matplotlib.figure.Figure
        Figure with one row per session.
    """
    n_sessions = len(poke_stats)
    fig, axes = plt.subplots(n_sessions, 1, figsize=(6, 3 * n_sessions), squeeze=False)

    for ax, ((subject, source_path), stats) in zip(axes[:, 0], poke_stats.items()):
        poke_times = np.asarray(stats["poke_events"])
        ipis = np.diff(np.sort(poke_times))
        ipis = ipis[ipis > 0]

        cutoff = stats.get("robust_ipi")
        if cutoff is None:
            cutoff, centers, density_smooth = estimate_robust_ipi_threshold(ipis)
        else:
            _, centers, density_smooth = estimate_robust_ipi_threshold(ipis)

        ax.hist(
            ipis,
            bins=np.logspace(np.log10(ipis.min()), np.log10(ipis.max()), 200),
            density=True, alpha=0.35, color="gray", label="IPI histogram",
        )

        if centers is not None and density_smooth is not None:
            ax.plot(centers, density_smooth, color="black", lw=2, label="Smoothed density")

        if np.isfinite(cutoff):
            ax.axvline(
                cutoff, color="red", linestyle="--", lw=2,
                label=f"Robust IPI ≈ {cutoff:.2f} s",
            )
        else:
            ax.text(
                0.05, 0.95, "No reliable IPI threshold",
                transform=ax.transAxes, ha="left", va="top", fontsize=8, color="gray",
            )

        ax.set_xscale("log")
        ax.set_xlabel("Inter-poke interval (s)")
        ax.set_ylabel("Density")
        ax.set_title(f"Subject {subject}")
        ax.legend(fontsize=8)

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Odor-switch-aligned poke-rate plots (day 0 vs baseline)
# ---------------------------------------------------------------------------


def plot_odor_switch_aligned_poke_rates(
    odor_switch_aligned: Dict[Any, Dict[str, Any]],
    pre_days: int = 3,
    time_unit: str = "minutes",
) -> plt.Figure:
    """Plot poke-rate windows aligned to the 2nd poke after an odor switch.

    Per panel: gray thin lines = baseline days; black thick line = baseline
    mean; gray shaded band = baseline 95 % CI; red thick line = day 0.

    Parameters
    ----------
    odor_switch_aligned:
        Output of
        :func:`~delphi_data.poke_metrics.extract_poke_rate_relative_to_odor_switch`.
    pre_days:
        Number of baseline days (controls which day labels are drawn in gray).
    time_unit:
        X-axis unit: ``"seconds"``, ``"minutes"``, or ``"hours"``.

    Returns
    -------
    matplotlib.figure.Figure

    Raises
    ------
    ValueError
        If *time_unit* is not one of the accepted values.
    """
    unit_scale = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}
    if time_unit not in unit_scale:
        raise ValueError("time_unit must be 'seconds', 'minutes', or 'hours'")
    scale = unit_scale[time_unit]
    time_label = {"seconds": "s", "minutes": "min", "hours": "h"}[time_unit]
    xlim = (-3600 / scale, 3600 / scale)

    sessions: Dict[Any, Any] = defaultdict(lambda: defaultdict(dict))
    baseline_by_session: Dict[Any, Any] = {}

    for key, data in odor_switch_aligned.items():
        if len(key) == 3 and key[2] == "baseline_timeseries":
            subject, source_path, _ = key
            baseline_by_session[(subject, source_path)] = data
        else:
            subject, source_path, odor_change, day = key
            sessions[(subject, source_path)][odor_change][day] = data

    session_keys = list(sessions.keys())
    odor_changes = sorted(
        {oc for s in sessions.values() for oc in s.keys()},
        key=lambda x: int(x.split("_")[2]),
    )
    n_rows, n_cols = len(session_keys), len(odor_changes)

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(2.5 * n_cols, 3 * n_rows),
        sharex=True, sharey=True,
        squeeze=False,
    )

    for r, session in enumerate(session_keys):
        subject, source_path = session
        baseline = baseline_by_session.get(session)

        for c, odor_change in enumerate(odor_changes):
            ax = axes[r, c]
            if odor_change not in sessions[session]:
                ax.axis("off")
                continue

            entries = sessions[session][odor_change]

            for day in range(-pre_days, 0):
                label = f"day_{day}"
                if label in entries:
                    ax.plot(
                        entries[label]["t_window"] / scale,
                        entries[label]["r_window"],
                        color="gray", alpha=0.75, linewidth=1, zorder=1,
                    )

            if baseline is not None and baseline["mean_rate"] is not None:
                t_b = baseline["t_window"] / scale
                ax.fill_between(
                    t_b,
                    baseline["mean_rate"] - baseline["ci_95"],
                    baseline["mean_rate"] + baseline["ci_95"],
                    color="gray", alpha=0.5, zorder=2,
                )
                ax.plot(t_b, baseline["mean_rate"], color="black", linewidth=1.5, zorder=3, alpha=0.75)

            if "day_0" in entries:
                ax.plot(
                    entries["day_0"]["t_window"] / scale,
                    entries["day_0"]["r_window"],
                    color="red", linewidth=1.5, zorder=4, alpha=0.75,
                )

            ax.axvline(0, color="k", linestyle="--", linewidth=1)
            ax.set_xlim(*xlim)
            ax.set_ylim(-0.01, 0.025)

            if r == 0:
                example = entries.get("day_0", next(iter(entries.values())))
                ax.set_title(
                    f"{example.get('prev_odor', '?')} → {example.get('new_odor', '?')}",
                    fontsize=10,
                )
            if c == 0:
                ax.set_ylabel(f"Subject {subject}\nPoke rate (Hz)")
            if r == n_rows - 1 and c == n_cols // 2:
                ax.set_xlabel(
                    f"Time from 2nd poke after odor change ({time_label})", fontsize=10
                )

    legend_handles = [
        plt.Line2D([], [], color="gray", linewidth=1, alpha=0.4, label="Baseline days"),
        plt.Line2D([], [], color="black", linewidth=2.5, label="Baseline mean"),
        plt.Line2D([], [], color="gray", linewidth=6, alpha=0.3, label="Baseline 95% CI"),
        plt.Line2D([], [], color="red", linewidth=2.5, label="Day 0"),
    ]
    axes[0, 0].legend(handles=legend_handles, loc="upper left", frameon=False, fontsize=8)
    fig.tight_layout()
    return fig


def plot_odor_switch_aligned_rate_difference(
    odor_switch_with_fc: Dict[Any, Dict[str, Any]],
    time_unit: str = "minutes",
) -> plt.Figure:
    """Plot time-resolved Δ-rate (day 0 minus baseline mean) per odor change.

    Per panel: black line = mean Δ-rate; gray shaded band = 95 % CI; dashed
    vertical at *t = 0*.

    Parameters
    ----------
    odor_switch_with_fc:
        Output of
        :func:`~delphi_data.poke_metrics.compute_fold_change_day0_vs_baseline`.
    time_unit:
        X-axis unit: ``"seconds"``, ``"minutes"``, or ``"hours"``.

    Returns
    -------
    matplotlib.figure.Figure

    Raises
    ------
    ValueError
        If *time_unit* is not one of the accepted values.
    """
    unit_scale = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}
    if time_unit not in unit_scale:
        raise ValueError("time_unit must be 'seconds', 'minutes', or 'hours'")
    scale = unit_scale[time_unit]
    time_label = {"seconds": "s", "minutes": "min", "hours": "h"}[time_unit]
    xlim = (-1200 / scale, 3600 / scale)

    sessions: Dict[Any, Any] = defaultdict(dict)
    for key, data in odor_switch_with_fc.items():
        if len(key) == 4 and key[3] == "baseline_difference_timeseries":
            subject, source_path, odor_change, _ = key
            sessions[(subject, source_path)][odor_change] = data

    session_keys = list(sessions.keys())
    odor_changes = sorted(
        {oc for s in sessions.values() for oc in s.keys()},
        key=lambda x: int(x.split("_")[2]),
    )
    n_rows, n_cols = len(session_keys), len(odor_changes)

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(2.5 * n_cols, 3 * n_rows),
        sharex=True, sharey=True,
        squeeze=False,
    )

    for r, (subject, source_path) in enumerate(session_keys):
        for c, odor_change in enumerate(odor_changes):
            ax = axes[r, c]
            if odor_change not in sessions[(subject, source_path)]:
                ax.axis("off")
                continue

            fc = sessions[(subject, source_path)][odor_change]
            t_ax = fc["t_window"] / scale
            mean_fc = fc["mean_delta"]
            ci = fc["ci_95"]

            ax.fill_between(t_ax, mean_fc - ci, mean_fc + ci, color="gray", alpha=0.4, zorder=1)
            ax.plot(t_ax, mean_fc, color="black", linewidth=2, zorder=2)
            ax.axvline(0, color="k", linestyle="--", linewidth=1)
            ax.set_xlim(*xlim)
            ax.set_ylim(-0.01, 0.02)

            if r == 0:
                ax.set_title(
                    f"{fc.get('prev_odor', '?')} → {fc.get('new_odor', '?')}", fontsize=10
                )
            if c == 0:
                ax.set_ylabel(f"Subject {subject}\nΔRate (Day 0 − baseline)", fontsize=9)
            if r == n_rows - 1 and c == n_cols // 2:
                ax.set_xlabel(
                    f"Time from 2nd poke after odor change ({time_label})", fontsize=10
                )

    legend_handles = [
        plt.Line2D([], [], color="black", linewidth=2, label="Mean Δ-rate"),
        plt.Line2D([], [], color="gray", linewidth=6, alpha=0.4, label="95% CI"),
        plt.Line2D([], [], color="black", linestyle="--", linewidth=1, label="Reference (t=0)"),
    ]
    axes[0, 0].legend(handles=legend_handles, loc="upper left", frameon=False, fontsize=8)
    fig.tight_layout()
    return fig


def plot_odor_switch_aligned_fold_change(
    odor_switch_fc: Dict[Any, Dict[str, Any]],
    time_unit: str = "minutes",
) -> plt.Figure:
    """Plot time-resolved raw ratio fold change (day 0 vs baseline) per odor change.

    Per panel: black line = mean fold-change ratio; gray shaded band = 95 % CI;
    dashed vertical at *t = 0*.

    Parameters
    ----------
    odor_switch_fc:
        Output of
        :func:`~delphi_data.poke_metrics.compute_fold_change_day0_vs_baseline_ratio`.
    time_unit:
        X-axis unit: ``"seconds"``, ``"minutes"``, or ``"hours"``.

    Returns
    -------
    matplotlib.figure.Figure

    Raises
    ------
    ValueError
        If *time_unit* is not one of the accepted values.
    """
    unit_scale = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}
    if time_unit not in unit_scale:
        raise ValueError("time_unit must be 'seconds', 'minutes', or 'hours'")
    scale = unit_scale[time_unit]
    time_label = {"seconds": "s", "minutes": "min", "hours": "h"}[time_unit]
    xlim = (-1200 / scale, 3600 / scale)

    sessions: Dict[Any, Any] = defaultdict(dict)
    for key, data in odor_switch_fc.items():
        if len(key) == 4 and key[3] == "baseline_fold_change_timeseries":
            subject, source_path, odor_change, _ = key
            sessions[(subject, source_path)][odor_change] = data

    session_keys = list(sessions.keys())
    odor_changes = sorted(
        {oc for s in sessions.values() for oc in s.keys()},
        key=lambda x: int(x.split("_")[2]),
    )
    n_rows, n_cols = len(session_keys), len(odor_changes)

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(2.5 * n_cols, 3 * n_rows),
        sharex=True, sharey=True,
        squeeze=False,
    )

    for r, (subject, source_path) in enumerate(session_keys):
        for c, odor_change in enumerate(odor_changes):
            ax = axes[r, c]
            if odor_change not in sessions[(subject, source_path)]:
                ax.axis("off")
                continue

            fc = sessions[(subject, source_path)][odor_change]
            t_ax = fc["t_window"] / scale
            mean_fc = fc["mean_fc_ratio"]
            ci = fc["ci_95_ratio"]

            ax.fill_between(t_ax, mean_fc - ci, mean_fc + ci, color="gray", alpha=0.4, zorder=1)
            ax.plot(t_ax, mean_fc, color="black", linewidth=2, zorder=2)
            ax.axvline(0, color="k", linestyle="--", linewidth=1)
            ax.set_xlim(*xlim)

            if r == 0:
                ax.set_title(
                    f"{fc.get('prev_odor', '?')} → {fc.get('new_odor', '?')}", fontsize=10
                )
            if c == 0:
                ax.set_ylabel(
                    f"Subject {subject}\nFold Change Ratio (Day 0 vs baseline)", fontsize=9
                )
            if r == n_rows - 1 and c == n_cols // 2:
                ax.set_xlabel(
                    f"Time from 2nd poke after odor change ({time_label})", fontsize=10
                )

    fig.tight_layout()
    return fig


def plot_odor_switch_aligned_fold_change_log(
    odor_switch_fc: Dict[Any, Dict[str, Any]],
    time_unit: str = "minutes",
) -> plt.Figure:
    """Plot time-resolved log₂ fold change (day 0 vs baseline) per odor change.

    Per panel: black line = mean log₂ fold change; gray shaded band = 95 % CI;
    dashed vertical at *t = 0* and horizontal at zero.

    Parameters
    ----------
    odor_switch_fc:
        Output of
        :func:`~delphi_data.poke_metrics.compute_fold_change_day0_vs_baseline_ratio`.
    time_unit:
        X-axis unit: ``"seconds"``, ``"minutes"``, or ``"hours"``.

    Returns
    -------
    matplotlib.figure.Figure

    Raises
    ------
    ValueError
        If *time_unit* is not one of the accepted values.
    """
    unit_scale = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}
    if time_unit not in unit_scale:
        raise ValueError("time_unit must be 'seconds', 'minutes', or 'hours'")
    scale = unit_scale[time_unit]
    time_label = {"seconds": "s", "minutes": "min", "hours": "h"}[time_unit]
    xlim = (-1200 / scale, 3600 / scale)

    sessions: Dict[Any, Any] = defaultdict(dict)
    for key, data in odor_switch_fc.items():
        if len(key) == 4 and key[3] == "baseline_fold_change_timeseries":
            subject, source_path, odor_change, _ = key
            sessions[(subject, source_path)][odor_change] = data

    session_keys = list(sessions.keys())
    odor_changes = sorted(
        {oc for s in sessions.values() for oc in s.keys()},
        key=lambda x: int(x.split("_")[2]),
    )
    n_rows, n_cols = len(session_keys), len(odor_changes)

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(2.5 * n_cols, 3 * n_rows),
        sharex=True, sharey=True,
        squeeze=False,
    )

    for r, (subject, source_path) in enumerate(session_keys):
        for c, odor_change in enumerate(odor_changes):
            ax = axes[r, c]
            if odor_change not in sessions[(subject, source_path)]:
                ax.axis("off")
                continue

            fc = sessions[(subject, source_path)][odor_change]
            t_ax = fc["t_window"] / scale
            mean_fc = fc["mean_fc_log"]
            ci = fc["ci_95_log"]

            ax.fill_between(t_ax, mean_fc - ci, mean_fc + ci, color="gray", alpha=0.4, zorder=1)
            ax.plot(t_ax, mean_fc, color="black", linewidth=2, zorder=2)
            ax.axvline(0, color="k", linestyle="--", linewidth=1)
            ax.axhline(0, color="k", linestyle="--", linewidth=1)
            ax.set_xlim(*xlim)

            if r == 0:
                ax.set_title(
                    f"{fc.get('prev_odor', '?')} → {fc.get('new_odor', '?')}", fontsize=10
                )
            if c == 0:
                ax.set_ylabel(
                    f"Subject {subject}\nlog₂ Fold Change (Day 0 vs baseline)", fontsize=9
                )
            if r == n_rows - 1 and c == n_cols // 2:
                ax.set_xlabel(
                    f"Time from 2nd poke after odor change ({time_label})", fontsize=10
                )

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Bout-centric exponential fold-change plots
# ---------------------------------------------------------------------------


def plot_odor_switch_aligned_poke_rates_from_fc_exp(
    odor_switch_fc_exp: Dict[Any, Dict[str, Any]],
    time_unit: str = "minutes",
) -> plt.Figure:
    """Plot bout-centric baseline and day-0 poke-rate time series.

    Per panel: thin purple lines = individual baseline bouts; black thick line
    = baseline mean; gray band = baseline 95 % CI; red thick line = day 0.

    Parameters
    ----------
    odor_switch_fc_exp:
        Output of
        :func:`~delphi_data.poke_metrics.compute_timeseries_fold_change_exponential`.
    time_unit:
        X-axis unit: ``"seconds"``, ``"minutes"``, or ``"hours"``.

    Returns
    -------
    matplotlib.figure.Figure

    Raises
    ------
    ValueError
        If *time_unit* is not one of the accepted values.
    """
    unit_scale = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}
    if time_unit not in unit_scale:
        raise ValueError("time_unit must be 'seconds', 'minutes', or 'hours'")
    scale = unit_scale[time_unit]
    time_label = {"seconds": "s", "minutes": "min", "hours": "h"}[time_unit]

    sessions: Dict[Any, Any] = defaultdict(dict)
    for key, data in odor_switch_fc_exp.items():
        if len(key) == 4 and key[3] == "timeseries_fold_change":
            subject, source_path, odor_change, _ = key
            sessions[(subject, source_path)][odor_change] = data

    session_keys = list(sessions.keys())
    odor_changes = sorted(
        {oc for s in sessions.values() for oc in s.keys()},
        key=lambda x: int(x.split("_")[2]),
    )
    n_rows, n_cols = len(session_keys), len(odor_changes)

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(2.6 * n_cols, 3.2 * n_rows),
        sharex=True, sharey=True,
        squeeze=False,
    )

    for r, (subject, source_path) in enumerate(session_keys):
        for c, odor_change in enumerate(odor_changes):
            ax = axes[r, c]
            if odor_change not in sessions[(subject, source_path)]:
                ax.axis("off")
                continue

            data = sessions[(subject, source_path)][odor_change]
            t_ax = data["t_window"] / scale
            baseline_stack = data["baseline_poke_rate_timeseries"]
            baseline_mean = data["baseline_mean_rate"]
            baseline_ci = data["baseline_ci_95"]
            day0_rate = data["day0_rate"]

            for trace in baseline_stack:
                ax.plot(t_ax, trace, color="purple", linewidth=1, alpha=0.75, zorder=1)

            ax.fill_between(
                t_ax,
                baseline_mean - baseline_ci,
                baseline_mean + baseline_ci,
                color="gray", alpha=0.5, zorder=2,
            )
            ax.plot(t_ax, baseline_mean, color="black", linewidth=2, zorder=3, label="Baseline mean")
            ax.plot(t_ax, day0_rate, color="red", linewidth=2, zorder=4, label="Day 0")
            ax.axvline(0, color="k", linestyle="--", linewidth=1)

            if r == 0:
                ax.set_title(
                    f"{data.get('prev_odor', '?')} → {data.get('new_odor', '?')}", fontsize=10
                )
            if c == 0:
                ax.set_ylabel(f"Subject {subject}\nPoke rate (Hz)", fontsize=9)
            if r == n_rows - 1 and c == n_cols // 2:
                ax.set_xlabel(
                    f"Time from 2nd poke after odor change ({time_label})", fontsize=10
                )

    legend_handles = [
        plt.Line2D([], [], color="purple", linewidth=1, alpha=0.75, label="Baseline days"),
        plt.Line2D([], [], color="black", linewidth=2, label="Baseline mean"),
        plt.Line2D([], [], color="gray", linewidth=6, alpha=0.5, label="Baseline 95% CI"),
        plt.Line2D([], [], color="red", linewidth=2, label="Day 0"),
    ]
    axes[0, 0].legend(handles=legend_handles, loc="upper left", frameon=False, fontsize=8)
    fig.tight_layout()
    return fig


def plot_odor_switch_aligned_fold_change_exponential(
    odor_switch_fc_exp: Dict[Any, Dict[str, Any]],
    fold_change_type: str = "log",
    time_unit: str = "minutes",
) -> plt.Figure:
    """Plot bout-centric fold-change time series (raw ratio or log₂).

    Per panel: black line = mean fold change; gray shaded band = 95 % CI;
    annotation shows number of baseline bouts used.

    Parameters
    ----------
    odor_switch_fc_exp:
        Output of
        :func:`~delphi_data.poke_metrics.compute_timeseries_fold_change_exponential`.
    fold_change_type:
        ``"log"`` for log₂ fold change or ``"raw"`` for raw ratio fold change.
    time_unit:
        X-axis unit: ``"seconds"``, ``"minutes"``, or ``"hours"``.

    Returns
    -------
    matplotlib.figure.Figure

    Raises
    ------
    ValueError
        If *time_unit* is not one of the accepted values.
    """
    unit_scale = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}
    if time_unit not in unit_scale:
        raise ValueError("time_unit must be 'seconds', 'minutes', or 'hours'")
    scale = unit_scale[time_unit]
    time_label = {"seconds": "s", "minutes": "min", "hours": "h"}[time_unit]

    sessions: Dict[Any, Any] = defaultdict(dict)
    for key, data in odor_switch_fc_exp.items():
        if len(key) == 4 and key[3] == "timeseries_fold_change":
            subject, source_path, odor_change, _ = key
            sessions[(subject, source_path)][odor_change] = data

    session_keys = list(sessions.keys())
    odor_changes = sorted(
        {oc for s in sessions.values() for oc in s.keys()},
        key=lambda x: int(x.split("_")[2]),
    )
    n_rows, n_cols = len(session_keys), len(odor_changes)

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(2.5 * n_cols, 3 * n_rows),
        sharex=True, sharey=True,
        squeeze=False,
    )

    for r, session in enumerate(session_keys):
        subject, source_path = session
        for c, odor_change in enumerate(odor_changes):
            ax = axes[r, c]
            if odor_change not in sessions[session]:
                ax.axis("off")
                continue

            data = sessions[session][odor_change]
            t_ax = data["t_window"] / scale

            if fold_change_type == "log":
                mean_fc = data["mean_fc_log"]
                ci = data["ci_95_fc_log"]
                ylabel = "log₂ Fold Change"
            else:
                mean_fc = data["mean_fc_raw"]
                ci = data["ci_95_fc_raw"]
                ylabel = "Raw Fold Change"

            ax.fill_between(t_ax, mean_fc - ci, mean_fc + ci, color="gray", alpha=0.4)
            ax.plot(t_ax, mean_fc, color="black", linewidth=2)
            ax.axvline(0, color="k", linestyle="--", linewidth=1)
            ax.axhline(0, color="k", linestyle="--", linewidth=1)

            n_pokes = data.get("n_baseline_bouts", 0)
            ax.text(
                0.02, 0.95, f"Baseline bouts: N={n_pokes}",
                transform=ax.transAxes, ha="left", va="top", fontsize=8,
            )

            if r == 0:
                ax.set_title(
                    f"{data['prev_odor']} → {data['new_odor']}", fontsize=10
                )
            if c == 0:
                ax.set_ylabel(f"Subject {subject}\n{ylabel}", fontsize=9)
            if r == n_rows - 1 and c == n_cols // 2:
                ax.set_xlabel(
                    f"Time from 2nd poke after odor change ({time_label})", fontsize=10
                )

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Cumulative poke counts
# ---------------------------------------------------------------------------


def plot_cumulative_poke_counts(
    cumulative_counts: Dict[Any, Dict[str, Any]],
) -> plt.Figure:
    """Plot cumulative poke counts in a 24 h window around odor changes.

    Per panel: thin gray lines = baseline days; black thick line = baseline
    mean; gray shaded band = baseline 95 % CI; red thick line = day 0.

    Parameters
    ----------
    cumulative_counts:
        Output of
        :func:`~delphi_data.poke_metrics.compute_windowed_cumulative_poke_count`.

    Returns
    -------
    matplotlib.figure.Figure
    """
    sessions: Dict[Any, Any] = defaultdict(lambda: defaultdict(dict))
    for key, data in cumulative_counts.items():
        subject, source_path, odor_change, label = key
        sessions[(subject, source_path)][odor_change][label] = data

    session_keys = list(sessions.keys())
    odor_changes = sorted(
        {oc for s in sessions.values() for oc in s.keys()},
        key=lambda x: int(x.split("_")[2]),
    )
    n_rows, n_cols = len(session_keys), len(odor_changes)

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(3 * n_cols, 3 * n_rows),
        sharex=True, sharey=True,
        squeeze=False,
    )

    for r, session in enumerate(session_keys):
        for c, odor_change in enumerate(odor_changes):
            ax = axes[r, c]
            entries = sessions[session].get(odor_change)
            if entries is None:
                ax.axis("off")
                continue

            for k, v in entries.items():
                if k.startswith("day_-"):
                    ax.plot(
                        v["t_window"] / 3600, v["cumulative_count"],
                        color="gray", alpha=0.6, linewidth=1,
                    )

            base = entries.get("baseline_cumulative_stats")
            if base is not None:
                t_b = base["t_window"] / 3600
                mean = base["mean_cumulative"]
                ci = base["ci_95"]
                ax.fill_between(t_b, mean - ci, mean + ci, color="gray", alpha=0.3)
                ax.plot(t_b, mean, color="black", linewidth=2)

            if "day_0" in entries:
                ax.plot(
                    entries["day_0"]["t_window"] / 3600,
                    entries["day_0"]["cumulative_count"],
                    color="red", linewidth=2,
                )

            if r == 0 and base is not None:
                ax.set_title(
                    f"{base['prev_odor']} → {base['new_odor']}", fontsize=10
                )
            if c == 0:
                ax.set_ylabel(f"Subject {session[0]}\nCumulative pokes")
            if r == n_rows - 1:
                ax.set_xlabel("Time since 2nd poke (hours)")

    legend_handles = [
        plt.Line2D([], [], color="gray", linewidth=1, label="Baseline days"),
        plt.Line2D([], [], color="black", linewidth=2, label="Baseline mean"),
        plt.Line2D([], [], color="gray", linewidth=6, alpha=0.3, label="Baseline 95% CI"),
        plt.Line2D([], [], color="red", linewidth=2, label="Day 0"),
    ]
    axes[0, 0].legend(handles=legend_handles, loc="upper left", frameon=False, fontsize=8)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Poke-duration comparison
# ---------------------------------------------------------------------------


def plot_poke_duration_comparison(
    results: Dict[Any, Dict[str, Any]],
) -> plt.Figure:
    """Plot poke-duration distributions with paired statistics per odor change.

    Layout: rows = subject/session, columns = odor change.  Each panel shows
    jittered scatter plots per day with mean ± std and significance annotations
    drawn in figure coordinates.

    Parameters
    ----------
    results:
        Output of
        :func:`~delphi_data.poke_metrics.compute_poke_duration_comparison`.

    Returns
    -------
    matplotlib.figure.Figure
    """
    sessions: Dict[Any, Any] = defaultdict(dict)
    for (subject, source_path, odor_change, _), data in results.items():
        sessions[(subject, source_path)][odor_change] = data

    session_keys = list(sessions.keys())
    odor_changes = sorted(
        {oc for s in sessions.values() for oc in s.keys()},
        key=lambda x: int(x.split("_")[2]),
    )
    n_rows, n_cols = len(session_keys), len(odor_changes)

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(3.5 * n_cols, 4.0 * n_rows),
        sharey=True,
        squeeze=False,
    )

    for r, session in enumerate(session_keys):
        subject, _ = session
        for c, odor_change in enumerate(odor_changes):
            ax = axes[r, c]
            if odor_change not in sessions[session]:
                ax.axis("off")
                continue

            data = sessions[session][odor_change]
            durations = data["durations"]
            days = sorted(
                durations.keys(),
                key=lambda d: 0 if d == "day_0" else int(d.split("_")[1]),
            )

            for i, day in enumerate(days):
                y = durations.get(day, [])
                if len(y) == 0:
                    continue
                color = "red" if day == "day_0" else "gray"
                jitter = np.random.normal(0, 0.06, size=len(y))
                ax.scatter(i + jitter, y, s=14, color=color, alpha=0.6, zorder=2)
                mean = np.mean(y)
                std = np.std(y)
                ax.errorbar(i, mean, yerr=std, color="black", lw=1.6, capsize=4, zorder=3)
                ax.hlines(mean, i - 0.15, i + 0.15, linestyles="solid", color="black", linewidth=1.2)
                ax.text(i, mean + std + 0.02, f"N={len(y)}", ha="center", fontsize=7)

            ax.set_xticks(np.arange(len(days)))
            ax.set_xticklabels([d.replace("day_", "Day ") for d in days], rotation=45, fontsize=8)

            if r == 0:
                ax.set_title(
                    f"{data['prev_odor']} → {data['new_odor']}", fontsize=10
                )
            if c == 0:
                ax.set_ylabel(f"Subject {subject}\nPoke duration (s)")

    # Significance annotations in figure coordinates
    fig.canvas.draw()

    for r, session in enumerate(session_keys):
        for c, odor_change in enumerate(odor_changes):
            ax = axes[r, c]
            if odor_change not in sessions[session]:
                continue

            data = sessions[session][odor_change]
            stats = data["stats"]
            durations = data["durations"]

            days = sorted(
                durations.keys(),
                key=lambda d: 0 if d == "day_0" else int(d.split("_")[1]),
            )
            if "day_0" not in days:
                continue

            x_day0 = days.index("day_0")
            bbox = ax.get_position()
            left, right = bbox.x0, bbox.x1
            top = bbox.y1
            y_step = 0.035
            y_line = top - 0.1

            for i, day in enumerate(days):
                if day == "day_0":
                    continue
                stat = stats.get(day, {})
                p = stat.get("p")
                if p is None or np.isnan(p):
                    label = "n/a"
                elif p < 0.05:
                    label = f"* (p={p:.2g})"
                else:
                    label = "n.s."

                n_cats = len(days)
                x1 = left + (right - left) * (i + 0.1) / (n_cats - 1)
                x2 = left + (right - left) * (x_day0 - 0.1) / (n_cats - 1)

                fig.lines.append(
                    plt.Line2D(
                        [x1, x2], [y_line, y_line],
                        transform=fig.transFigure, color="black", lw=1.0,
                    )
                )
                fig.text(
                    (x1 + x2) / 2, y_line + 0.006, label,
                    ha="center", va="bottom", fontsize=7,
                )
                y_line += y_step

    return fig


# ---------------------------------------------------------------------------
# Session-level snapshot plots
# ---------------------------------------------------------------------------


def plot_poke_duration_by_odor(
    df: "pd.DataFrame",
    duration_col: str = "poke_to_beam_offset_duration",
    odor_col: str = "odor_name",
    max_duration: float = 5.0,
) -> plt.Figure:
    """Plot poke-duration distributions as violin plots, grouped by odor identity.

    Parameters
    ----------
    df:
        Per-poke event dataframe.  Must contain *duration_col*, *odor_col*, and
        a ``poke_registered`` column.
    duration_col:
        Column name holding poke durations (seconds).
    odor_col:
        Column name holding odor labels.
    max_duration:
        Upper clip value (seconds) for display – extreme hardware artefacts are
        excluded.

    Returns
    -------
    matplotlib.figure.Figure
    """
    import pandas as pd

    reg = df[df["poke_registered"] == True].copy()
    reg = reg.dropna(subset=[duration_col, odor_col])
    reg = reg[reg[duration_col] <= max_duration]

    odors = sorted(reg[odor_col].unique())
    data_by_odor = [reg[reg[odor_col] == o][duration_col].to_numpy() for o in odors]

    fig, ax = plt.subplots(figsize=(max(4, 1.8 * len(odors)), 4))

    parts = ax.violinplot(
        data_by_odor,
        positions=range(len(odors)),
        showmedians=True,
        showextrema=False,
    )
    for pc in parts["bodies"]:
        pc.set_alpha(0.7)

    for i, (odor, data) in enumerate(zip(odors, data_by_odor)):
        ax.scatter(
            np.random.normal(i, 0.06, size=min(len(data), 500)),
            data[: 500] if len(data) > 500 else data,
            s=4,
            alpha=0.3,
            color="black",
            zorder=2,
        )
        ax.text(
            i, -0.15,
            f"N={len(data)}",
            ha="center", va="top", fontsize=8,
            transform=ax.get_xaxis_transform(),
        )

    ax.set_xticks(range(len(odors)))
    ax.set_xticklabels(odors)
    ax.set_xlabel("Odor")
    ax.set_ylabel("Poke duration (s)")
    ax.set_title("Poke Duration by Odor")
    ax.grid(True, axis="y", ls="--", alpha=0.4)
    fig.tight_layout()
    return fig


def plot_daily_poke_count(
    df: "pd.DataFrame",
    odor_col: str = "odor_name",
) -> plt.Figure:
    """Plot total daily poke count, colour-coded by dominant odor.

    Each bar represents one calendar day.  The bar colour reflects the odor
    with the most pokes on that day.

    Parameters
    ----------
    df:
        Per-poke event dataframe.  Must contain ``poke_registered``,
        ``beam_break_onset``, and *odor_col* columns.
    odor_col:
        Column name holding odor labels.

    Returns
    -------
    matplotlib.figure.Figure
    """
    import pandas as pd

    reg = df[df["poke_registered"] == True].copy()
    reg["datetime_dt"] = pd.to_datetime(reg["datetime"], utc=True, errors="coerce")
    reg["date"] = reg["datetime_dt"].dt.date

    daily = reg.groupby("date").size().reset_index(name="count")

    cmap = plt.get_cmap("Set1")
    odors = sorted(reg[odor_col].dropna().unique())
    odor_color = {o: cmap(i % 9) for i, o in enumerate(odors)}

    # Dominant odor per day
    dom = (
        reg.dropna(subset=[odor_col])
        .groupby("date")[odor_col]
        .agg(lambda x: x.value_counts().index[0])
    )
    daily["dominant_odor"] = daily["date"].map(dom)
    daily["color"] = daily["dominant_odor"].map(odor_color).fillna("gray")

    fig, ax = plt.subplots(figsize=(max(8, 0.4 * len(daily)), 4))
    ax.bar(
        range(len(daily)),
        daily["count"],
        color=daily["color"].tolist(),
        edgecolor="white",
        linewidth=0.5,
    )
    ax.set_xticks(range(len(daily)))
    ax.set_xticklabels(
        [str(d) for d in daily["date"]],
        rotation=45, ha="right", fontsize=7,
    )
    ax.set_xlabel("Date")
    ax.set_ylabel("Poke count")
    ax.set_title("Daily Poke Count (colour = dominant odor)")
    ax.grid(True, axis="y", ls="--", alpha=0.4)

    legend_handles = [
        plt.Rectangle((0, 0), 1, 1, color=odor_color[o], label=o) for o in odors
    ]
    ax.legend(handles=legend_handles, loc="upper left", frameon=False, fontsize=8)
    fig.tight_layout()
    return fig
