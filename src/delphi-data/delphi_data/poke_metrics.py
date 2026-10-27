"""Behavioral metrics for poke-port experiments.

Provides functions for computing poke rates, inter-poke intervals, cumulative
counts, fold changes, and poke-duration statistics from event-time data.
"""

from __future__ import annotations

import pathlib
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.signal import find_peaks, savgol_filter
from scipy.stats import shapiro, t as t_dist, ttest_rel, wilcoxon


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_csvs_with_subject_id(root_dir: str) -> pd.DataFrame:
    """Load and concatenate all CSV files found under *root_dir*.

    The first directory level below *root_dir* is used as the ``subject_id``.
    An additional ``source_path`` column records the absolute path of each
    source file.

    Parameters
    ----------
    root_dir:
        Root directory to search recursively for ``*.csv`` files.

    Returns
    -------
    pd.DataFrame
        Concatenated dataframe with ``subject_id`` and ``source_path`` columns
        appended.  Returns an empty ``DataFrame`` when no CSV files are found.
    """
    root = pathlib.Path(root_dir)
    dfs = []

    for csv_path in root.rglob("*.csv"):
        subject_id = csv_path.relative_to(root).parts[0]
        df = pd.read_csv(csv_path)
        df["subject_id"] = subject_id
        df["source_path"] = str(csv_path)
        dfs.append(df)

    if not dfs:
        return pd.DataFrame()

    return pd.concat(dfs, ignore_index=True)


# ---------------------------------------------------------------------------
# Odor-change detection
# ---------------------------------------------------------------------------


def odor_change_events(
    data: pd.DataFrame,
    subject: str,
    source_path: str,
    odor_mapping: Optional[dict] = None,
    odor_switch_times: Optional[list] = None,
) -> Tuple[list, list, list]:
    """Find poke times at which the delivered odor changed for one session.

    Parameters
    ----------
    data:
        Full concatenated dataframe containing all subjects and sessions.
    subject:
        Subject identifier to filter on ``subject_id``.
    source_path:
        Absolute path of the session CSV to filter on ``source_path``.
    odor_mapping:
        Dict mapping integer odor codes (as stored in the ``odor`` column) to
        human-readable labels, e.g. ``{100: "Air", 1000: "Pinene"}``.
        Defaults to an empty dict (codes are not labelled).
    odor_switch_times:
        Known true odor-switch times (absolute seconds).  When provided,
        these are returned as ``odor_switch_t`` relative to session start.
        Defaults to ``None`` (empty list returned).

    Returns
    -------
    odor_change_pokes : list of float
        Poke-onset times (relative to session start) at which the odor changed.
    odor_ids : list
        Mapped odor labels for each change poke (via *odor_mapping*).
    odor_switch_t : list of float
        Known switch times relative to session start, or ``[]``.
    """
    if odor_mapping is None:
        odor_mapping = {}

    data = data.dropna(subset=["poke_onset", "odor"])
    subject_data = data[data["subject_id"] == subject]
    source_data = subject_data[subject_data["source_path"] == source_path].dropna(
        subset=["poke_onset", "odor", "beam_break_onset"]
    )

    source_data = source_data.copy()
    source_data["odor_change"] = source_data["odor"].diff() != 0

    t0 = min(source_data["beam_break_onset"])
    odor_change_pokes = source_data[source_data["odor_change"]]["poke_onset"] - t0

    if odor_switch_times is None:
        odor_switch_t = []
    else:
        odor_switch_t = [t - t0 for t in odor_switch_times]

    odor_ids = (
        source_data[source_data["odor_change"]]["odor"]
        .astype(int)
        .map(odor_mapping)
        .tolist()
    )

    return odor_change_pokes.tolist(), odor_ids, odor_switch_t


# ---------------------------------------------------------------------------
# Poke-rate estimators
# ---------------------------------------------------------------------------


def poke_rate_exponential_decay_binned(
    t_events: list,
    tau: float = 10.0,
    dt: float = 1800.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Estimate poke rate via an impulse train with exponential decay, binned.

    Each event increments a state variable by 1; the state decays
    exponentially between bins.  The instantaneous rate is ``state / tau``.

    Parameters
    ----------
    t_events:
        Event times in seconds.
    tau:
        Decay time constant in seconds.
    dt:
        Bin width in seconds.

    Returns
    -------
    t_grid : np.ndarray
        Bin-edge times (seconds).
    r_grid : np.ndarray
        Estimated poke rate (Hz) at each bin.
    """
    t_events = np.asarray(t_events, dtype=float)
    if t_events.size == 0:
        return np.array([]), np.array([])

    t_events = np.sort(t_events)
    t0, t1 = t_events[0], t_events[-1]
    t_grid = np.arange(t0, t1 + dt, dt)

    s = 0.0
    r_grid = np.zeros_like(t_grid)
    e_idx = 0

    for i, tg in enumerate(t_grid):
        if i > 0:
            s *= np.exp(-dt / tau)
        while e_idx < len(t_events) and t_events[e_idx] <= tg:
            s += 1.0
            e_idx += 1
        r_grid[i] = s / tau

    return t_grid, r_grid


def poke_rate_exponential_decay_sliding(
    t_events: list,
    tau: float = 10.0,
    window: float = 60.0,
    overlap: float = 0.5,
) -> Tuple[np.ndarray, np.ndarray]:
    """Estimate poke rate using a causal exponential kernel over sliding windows.

    Parameters
    ----------
    t_events:
        Poke event times in seconds (assumed ≥ 0).
    tau:
        Exponential decay time constant in seconds.
    window:
        Window length in seconds.
    overlap:
        Fractional overlap between consecutive windows in ``[0, 1)``.
        ``0.0`` = no overlap; ``0.5`` = 50 % overlap.

    Returns
    -------
    t_centers : np.ndarray
        Window-centre times in seconds.
    rate : np.ndarray
        Poke rate (Hz) at each window centre.

    Raises
    ------
    ValueError
        If *overlap* is not in ``[0, 1)``.
    """
    if not 0 <= overlap < 1:
        raise ValueError("overlap must be in the range [0, 1).")

    t_events = np.asarray(t_events, dtype=float)
    if t_events.size == 0:
        return np.array([]), np.array([])

    t_events = np.sort(t_events)
    step = window * (1.0 - overlap)
    t_centers = np.arange(t_events[0], t_events[-1] + step, step)
    rates = np.zeros_like(t_centers)

    for i, t_c in enumerate(t_centers):
        mask = (t_events <= t_c) & (t_events >= t_c - window)
        relevant = t_events[mask]
        if relevant.size == 0:
            continue
        dt = t_c - relevant
        rates[i] = np.sum(np.exp(-dt / tau)) / tau

    return t_centers, rates


def poke_rate_exponential_decay_convolution(
    t_events: list,
    tau: float = 10.0,
    window: float = 60.0,
    overlap: float = 0.5,
    dt_kernel: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """Estimate poke rate by convolving an impulse train with a causal exponential kernel.

    Parameters
    ----------
    t_events:
        Poke event times in seconds (absolute or relative).
    tau:
        Exponential decay time constant in seconds.
    window:
        Sliding-window length in seconds.
    overlap:
        Fractional overlap between consecutive windows ``[0, 1)``.
    dt_kernel:
        Temporal resolution of the impulse train and convolution in seconds.

    Returns
    -------
    t_centers : np.ndarray
        Window-centre times in seconds.
    rate : np.ndarray
        Poke rate (Hz) at each window centre.

    Raises
    ------
    ValueError
        If *overlap* is not in ``[0, 1)``.
    """
    if not 0 <= overlap < 1:
        raise ValueError("overlap must be in the range [0, 1).")

    t_events = np.asarray(t_events, dtype=float)
    if t_events.size == 0:
        return np.array([]), np.array([])

    t_events = np.sort(t_events)
    t_start = t_events.min()
    t_end = t_events.max() + window
    t_grid = np.arange(t_start, t_end, dt_kernel)

    impulse = np.zeros_like(t_grid)
    indices = ((t_events - t_start) / dt_kernel).astype(int)
    indices = indices[indices < len(impulse)]
    np.add.at(impulse, indices, 1.0)

    kernel_t = np.arange(0, window, dt_kernel)
    kernel = np.exp(-kernel_t / tau) / tau

    rate_full = np.convolve(impulse, kernel, mode="full")[: len(t_grid)]

    step = window * (1 - overlap)
    t_centers = np.arange(t_events.min(), t_events.max(), step)
    center_idx = ((t_centers - t_start) / dt_kernel).astype(int)
    valid = (center_idx >= 0) & (center_idx < len(rate_full))

    return t_centers[valid], rate_full[center_idx[valid]]


# ---------------------------------------------------------------------------
# Session-level poke statistics
# ---------------------------------------------------------------------------


def compute_poke_stats(
    df: pd.DataFrame,
    odor_events: Dict[Tuple[str, str], Dict[str, list]],
    tau: float = 10.0,
    dt: float = 1800.0,
    overlap: float = 0.5,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Compute exponentially smoothed poke rates for every subject/session.

    Poke events are normalised to the session start (minimum
    ``beam_break_onset``).  Rate estimation uses
    :func:`poke_rate_exponential_decay_convolution`.

    Parameters
    ----------
    df:
        DataFrame with columns ``['subject_id', 'source_path', 'poke_onset',
        'beam_break_onset', 'poke_duration']``.
    odor_events:
        Dict keyed by ``(subject_id, source_path)`` containing:

        - ``"first_poke_t"`` – poke times of odor changes (relative, seconds)
        - ``"odor_id"`` – odor labels at each change
        - ``"odor_switch_t"`` – known true switch times (relative, seconds)
    tau:
        Decay time constant for the exponential kernel (seconds).
    dt:
        Window length passed to the convolution estimator (seconds).
    overlap:
        Fractional window overlap passed to the convolution estimator.

    Returns
    -------
    dict
        Keyed by ``(subject_id, source_path)``.  Each value contains:

        - ``"t"`` – time grid (np.ndarray)
        - ``"poke_rate"`` – estimated rate in Hz (np.ndarray)
        - ``"poke_events"`` – session-relative poke times (np.ndarray)
        - ``"poke_durations"`` – poke durations in seconds (np.ndarray)
        - ``"odor_id"`` – odor labels at change events
        - ``"first_poke_t"`` – times of first pokes after each odor change
        - ``"odor_switch_t"`` – known true switch times
    """
    poke_stats: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for (subject, source_path), odor_data in odor_events.items():
        session_df = df[
            (df["subject_id"] == subject) & (df["source_path"] == source_path)
        ]

        poketimes = session_df["poke_onset"].dropna().to_numpy()
        if poketimes.size == 0:
            continue

        poke_events = poketimes - np.min(session_df["beam_break_onset"])

        dur_col = (
            "poke_duration"
            if "poke_duration" in session_df.columns
            else "poke_to_beam_offset_duration"
        )
        poke_durations = session_df[dur_col].dropna().to_numpy()

        t, r = poke_rate_exponential_decay_convolution(
            poke_events,
            tau=tau,
            window=dt,
            overlap=overlap,
        )

        poke_stats[(subject, source_path)] = {
            "t": t,
            "poke_rate": r,
            "poke_events": poke_events,
            "poke_durations": poke_durations,
            "odor_id": odor_data["odor_id"],
            "first_poke_t": odor_data["first_poke_t"],
            "odor_switch_t": odor_data["odor_switch_t"],
        }

    return poke_stats


# ---------------------------------------------------------------------------
# Windowed poke-rate extraction
# ---------------------------------------------------------------------------


def extract_poke_rate_windows(
    poke_stats: Dict[Tuple[str, str], Dict[str, Any]],
    pre_w: float = 3600.0,
    post_w: float = 3600.0,
    pre_days: int = 3,
    post_days: int = 1,
) -> Dict[Tuple[str, str, str, str], Dict[str, Any]]:
    """Extract poke-rate time windows centred on odor-change poke events.

    For each odor change (skipping the first) and each day offset in
    ``[-pre_days, post_days]``, a window of ``[event − pre_w, event + post_w]``
    (aligned to the change time) is extracted.

    Parameters
    ----------
    poke_stats:
        Output of :func:`compute_poke_stats`.
    pre_w:
        Seconds before the change event to include.
    post_w:
        Seconds after the change event to include.
    pre_days:
        Number of baseline days before the change to include.
    post_days:
        Number of post-change days to include.

    Returns
    -------
    dict
        Keyed by ``(subject, source_path, "odor_change_N", "day_D")``.
        Each value contains ``"t_window"``, ``"r_window"``, ``"event_time"``,
        ``"new_odor"``, and ``"prev_odor"``.
    """
    windows: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}

    for (subject, source_path), stats in poke_stats.items():
        t_sample = stats["t"]
        r_poke = stats["poke_rate"]
        odor_changes = list(zip(stats["first_poke_t"], stats["odor_id"]))

        for i, (event_time, new_odor) in enumerate(odor_changes[1:]):
            _, prev_odor = odor_changes[i]
            for day in range(-pre_days, post_days + 1):
                shifted = event_time + day * 24 * 3600
                start_time = shifted - pre_w
                end_time = shifted + post_w

                mask = (t_sample >= start_time) & (t_sample <= end_time)
                t_window = t_sample[mask] - shifted
                r_window = r_poke[mask]

                windows[
                    (subject, source_path, f"odor_change_{i}", f"day_{day}")
                ] = {
                    "t_window": t_window,
                    "r_window": r_window,
                    "event_time": event_time,
                    "new_odor": new_odor,
                    "prev_odor": prev_odor,
                }

    return windows


def extract_poke_rate_relative_to_odor_switch(
    poke_stats: Dict[Tuple[str, str], Dict[str, Any]],
    pre_days: int = 3,
    pre_w: float = 3600.0,
    post_w: float = 3600.0,
) -> Dict[Any, Dict[str, Any]]:
    """Extract poke-rate windows aligned to the second poke after an odor switch.

    For each odor change and each day offset (from ``-pre_days`` to ``0``),
    a window is centred on the second poke after the switch time for that day.
    A baseline time-series mean and 95 % CI across the baseline days is also
    computed and stored.

    Parameters
    ----------
    poke_stats:
        Output of :func:`compute_poke_stats` (requires ``"odor_switch_t"``).
    pre_days:
        Number of baseline days before the change.
    pre_w:
        Seconds before alignment point to include.
    post_w:
        Seconds after alignment point to include.

    Returns
    -------
    dict
        Per-day entries keyed by
        ``(subject, source_path, "odor_change_N", "day_D")`` and a baseline
        summary entry keyed by ``(subject, source_path, "baseline_timeseries")``.
    """
    odor_switch_aligned: Dict[Any, Dict[str, Any]] = {}

    for (subject, source_path), stats in poke_stats.items():
        t_sample = stats["t"]
        r_poke = stats["poke_rate"]
        poke_events = stats["poke_events"]
        odor_changes = list(zip(stats["odor_switch_t"], stats["odor_id"][1:]))

        for i, (event_time, new_odor) in enumerate(odor_changes):
            baseline_r_windows: List[np.ndarray] = []
            baseline_t_windows: List[np.ndarray] = []

            for day in range(-pre_days, 1):
                start_time = event_time + day * 24 * 3600
                poke_after = poke_events[poke_events > start_time]

                if len(poke_after) < 2:
                    continue

                second_poke_time = poke_after[1]
                w_start = second_poke_time - pre_w
                w_end = second_poke_time + post_w

                mask = (t_sample >= w_start) & (t_sample <= w_end)
                r_window = r_poke[mask]
                t_window = t_sample[mask] - second_poke_time

                if r_window.size == 0:
                    continue

                if day < 0:
                    baseline_r_windows.append(r_window)
                    baseline_t_windows.append(t_window)

                odor_switch_aligned[
                    (subject, source_path, f"odor_change_{i}", f"day_{day}")
                ] = {
                    "t_window": t_window,
                    "r_window": r_window,
                    "second_poke_time": second_poke_time,
                    "new_odor": new_odor,
                    "prev_odor": stats["odor_id"][i],
                }

            # Baseline time-series mean + 95 % CI
            if len(baseline_r_windows) >= 2:
                min_len = min(len(r) for r in baseline_r_windows)
                r_stack = np.vstack([r[:min_len] for r in baseline_r_windows])
                t_baseline = baseline_t_windows[0][:min_len]
                mean_rate = r_stack.mean(axis=0)
                n_days = r_stack.shape[0]
                sem = r_stack.std(axis=0, ddof=1) / np.sqrt(n_days)
                tcrit = t_dist.ppf(0.975, df=n_days - 1)
                ci_95 = tcrit * sem
            else:
                t_baseline = None
                mean_rate = None
                ci_95 = None
                n_days = len(baseline_r_windows)

            odor_switch_aligned[(subject, source_path, "baseline_timeseries")] = {
                "t_window": t_baseline,
                "mean_rate": mean_rate,
                "ci_95": ci_95,
                "n_days_used": n_days,
                "new_odor": new_odor,
                "prev_odor": stats["odor_id"][i],
            }

    return odor_switch_aligned


# ---------------------------------------------------------------------------
# Fold-change and delta-rate metrics
# ---------------------------------------------------------------------------


def raw_fold_change(
    poke_rate_day0: np.ndarray,
    poke_rate_baseline: np.ndarray,
    epsilon: float,
) -> np.ndarray:
    """Compute ε-regularised raw ratio fold change, zero-centred.

    Returns ``(day0 + ε) / (baseline + ε) − 1``.  The lower bound is ``-1``;
    negative values indicate day 0 is below baseline.

    Parameters
    ----------
    poke_rate_day0:
        Day-0 poke-rate time series.
    poke_rate_baseline:
        Baseline poke-rate time series (same length as *poke_rate_day0*).
    epsilon:
        Regularisation constant added to both numerator and denominator.

    Returns
    -------
    np.ndarray
        Zero-centred fold-change values.
    """
    return ((poke_rate_day0 + epsilon) / (poke_rate_baseline + epsilon)) - 1


def log_fold_change(
    poke_rate_day0: np.ndarray,
    poke_rate_baseline: np.ndarray,
    epsilon: float,
) -> np.ndarray:
    """Compute ε-regularised log₂ fold change, zero-centred and symmetric.

    Parameters
    ----------
    poke_rate_day0:
        Day-0 poke-rate time series.
    poke_rate_baseline:
        Baseline poke-rate time series (same length as *poke_rate_day0*).
    epsilon:
        Regularisation constant added to both numerator and denominator.

    Returns
    -------
    np.ndarray
        Log₂ fold-change values.
    """
    return np.log2((poke_rate_day0 + epsilon) / (poke_rate_baseline + epsilon))


def compute_fold_change_day0_vs_baseline(
    odor_switch_aligned: Dict[Any, Dict[str, Any]],
    pre_days: int = 3,
    min_baseline_rate: float = 1e-4,
) -> Dict[Any, Dict[str, Any]]:
    """Compute time-resolved Δ-rate and z-score between day 0 and baseline days.

    For each odor change, the mean Δ-rate and 95 % CI are estimated across
    baseline days at each timepoint.  A z-score relative to baseline
    variability is also stored.

    Parameters
    ----------
    odor_switch_aligned:
        Output of :func:`extract_poke_rate_relative_to_odor_switch`.
    pre_days:
        Number of baseline days before day 0.
    min_baseline_rate:
        Minimum baseline std used for z-score regularisation.

    Returns
    -------
    dict
        Input dict extended with entries keyed by
        ``(subject, source_path, "odor_change_N", "baseline_difference_timeseries")``.
        Each entry contains ``"t_window"``, ``"mean_delta"``, ``"ci_95"``,
        ``"z_score"``, ``"n_days_used"``, ``"prev_odor"``, ``"new_odor"``.
    """
    sessions: Dict[Any, Any] = defaultdict(lambda: defaultdict(dict))
    for key, data in odor_switch_aligned.items():
        if len(key) == 4:
            subject, source_path, odor_change, label = key
            sessions[(subject, source_path)][odor_change][label] = data

    results = dict(odor_switch_aligned)

    for (subject, source_path), odor_changes in sessions.items():
        for odor_change, entries in odor_changes.items():
            if "day_0" not in entries:
                continue

            r0 = entries["day_0"]["r_window"]
            t0 = entries["day_0"]["t_window"]

            baseline_rates = []
            for day in range(-pre_days, 0):
                label = f"day_{day}"
                if label not in entries:
                    continue
                r_base = entries[label]["r_window"]
                min_len = min(len(r0), len(r_base))
                if min_len == 0:
                    continue
                baseline_rates.append(r_base[:min_len])

            if len(baseline_rates) < 2:
                continue

            baseline_stack = np.vstack(baseline_rates)
            r0c = r0[: baseline_stack.shape[1]]
            n_days = baseline_stack.shape[0]

            baseline_mean = np.mean(baseline_stack, axis=0)
            baseline_std = np.std(baseline_stack, axis=0, ddof=1)

            diff_stack = r0c - baseline_stack
            mean_delta = np.mean(diff_stack, axis=0)
            sem_delta = np.std(diff_stack, axis=0, ddof=1) / np.sqrt(n_days)
            tcrit = t_dist.ppf(0.975, n_days - 1)
            ci_95 = tcrit * sem_delta

            z = np.full_like(mean_delta, np.nan, dtype=float)
            valid = baseline_std > min_baseline_rate
            z[valid] = (r0c[valid] - baseline_mean[valid]) / baseline_std[valid]

            results[
                (subject, source_path, odor_change, "baseline_difference_timeseries")
            ] = {
                "t_window": t0[: len(mean_delta)],
                "mean_delta": mean_delta,
                "ci_95": ci_95,
                "z_score": z,
                "n_days_used": n_days,
                "prev_odor": entries["day_0"]["prev_odor"],
                "new_odor": entries["day_0"]["new_odor"],
            }

    return results


def compute_fold_change_day0_vs_baseline_ratio(
    odor_switch_aligned: Dict[Any, Dict[str, Any]],
    pre_days: int = 3,
    min_baseline_rate: float = 1e-4,
) -> Dict[Any, Dict[str, Any]]:
    """Compute time-resolved raw and log₂ fold change (day 0 vs each baseline day).

    ε is estimated from the 5th percentile of all non-zero rates across all
    days for each odor change.

    Parameters
    ----------
    odor_switch_aligned:
        Output of :func:`extract_poke_rate_relative_to_odor_switch`.
    pre_days:
        Number of baseline days before day 0.
    min_baseline_rate:
        Unused; kept for API consistency.

    Returns
    -------
    dict
        Input dict extended with entries keyed by
        ``(subject, source_path, "odor_change_N", "baseline_fold_change_timeseries")``.
        Each entry contains ``"t_window"``, ``"mean_fc_ratio"``, ``"ci_95_ratio"``,
        ``"mean_fc_log"``, ``"ci_95_log"``, ``"epsilon_used"``, ``"n_days_used"``,
        ``"prev_odor"``, ``"new_odor"``.
    """
    sessions: Dict[Any, Any] = defaultdict(lambda: defaultdict(dict))
    for key, data in odor_switch_aligned.items():
        if len(key) == 4:
            subject, source_path, odor_change, day = key
            sessions[(subject, source_path)][odor_change][day] = data

    results = dict(odor_switch_aligned)

    for (subject, source_path), odor_changes in sessions.items():
        for odor_change, entries in odor_changes.items():
            if "day_0" not in entries:
                continue

            r0 = entries["day_0"]["r_window"]
            t0 = entries["day_0"]["t_window"]

            all_rates = [r0]
            for day in range(-pre_days, 0):
                label = f"day_{day}"
                if label in entries:
                    all_rates.append(entries[label]["r_window"])

            all_rates_cat = np.concatenate(all_rates)
            nonzero = all_rates_cat[all_rates_cat > 0]
            if nonzero.size == 0:
                continue
            epsilon = np.percentile(nonzero, 5)

            fold_change_days_raw = []
            fold_change_days_log = []

            for day in range(-pre_days, 0):
                label = f"day_{day}"
                if label not in entries:
                    continue
                r_base = entries[label]["r_window"]
                n = min(len(r0), len(r_base))
                if n == 0:
                    continue
                fold_change_days_raw.append(raw_fold_change(r0[:n], r_base[:n], epsilon))
                fold_change_days_log.append(log_fold_change(r0[:n], r_base[:n], epsilon))

            if len(fold_change_days_raw) < 2:
                continue

            fc_stack_raw = np.vstack(fold_change_days_raw)
            fc_stack_log = np.vstack(fold_change_days_log)
            n_days, n_time = fc_stack_raw.shape

            mean_fc_raw = np.mean(fc_stack_raw, axis=0)
            ci_95_raw = 1.960 * np.std(fc_stack_raw, axis=0, ddof=1) / np.sqrt(n_days)

            mean_fc_log = np.mean(fc_stack_log, axis=0)
            ci_95_log = 1.960 * np.std(fc_stack_log, axis=0, ddof=1) / np.sqrt(n_days)

            results[
                (subject, source_path, odor_change, "baseline_fold_change_timeseries")
            ] = {
                "t_window": t0[:n_time],
                "mean_fc_ratio": mean_fc_raw,
                "ci_95_ratio": ci_95_raw,
                "mean_fc_log": mean_fc_log,
                "ci_95_log": ci_95_log,
                "epsilon_used": epsilon,
                "n_days_used": n_days,
                "prev_odor": entries["day_0"]["prev_odor"],
                "new_odor": entries["day_0"]["new_odor"],
            }

    return results


# ---------------------------------------------------------------------------
# Inter-poke interval (IPI) threshold estimation
# ---------------------------------------------------------------------------


def estimate_robust_ipi_threshold(
    ipis: np.ndarray,
    *,
    min_ipi: float = 0.2,
    max_ipi: float = 1e3,
    n_bins: int = 200,
    smooth_window: int = 11,
    polyorder: int = 2,
    taper_fraction: float = 0.75,
    slope_eps: float = 0.01,
    bimodal_valley_ratio_thresh: float = 0.7,
) -> Tuple[float, Optional[np.ndarray], Optional[np.ndarray]]:
    """Estimate a robust IPI threshold that separates within-bout from between-bout pokes.

    Uses a priority cascade:

    1. Bimodal valley (if bimodality is strong enough).
    2. Conservative right-tail taper or flat-slope criterion.

    Parameters
    ----------
    ipis:
        Array of inter-poke intervals in seconds.
    min_ipi:
        Lower bound for included IPIs.
    max_ipi:
        Upper bound for included IPIs.
    n_bins:
        Number of log-spaced histogram bins.
    smooth_window:
        Savitzky–Golay filter window length (samples, must be odd).
    polyorder:
        Savitzky–Golay polynomial order.
    taper_fraction:
        Fraction of peak density at which the right-tail taper is declared.
    slope_eps:
        Absolute slope threshold for the flat-slope criterion.
    bimodal_valley_ratio_thresh:
        Valley-to-peak ratio below which a bimodal valley is accepted.

    Returns
    -------
    threshold : float
        Estimated IPI threshold in seconds (``np.nan`` if none found).
    centers : np.ndarray or None
        Histogram bin centres (log-spaced).
    density_smooth : np.ndarray or None
        Smoothed density values at *centers*.
    """
    ipis = np.asarray(ipis)
    ipis = ipis[(ipis > min_ipi) & (ipis < max_ipi)]

    if ipis.size < 50:
        return np.nan, None, None

    bins = np.logspace(np.log10(ipis.min()), np.log10(ipis.max()), n_bins)
    density, edges = np.histogram(ipis, bins=bins, density=True)
    centers = np.sqrt(edges[:-1] * edges[1:])

    density_smooth = savgol_filter(
        density,
        window_length=smooth_window,
        polyorder=polyorder,
        mode="interp",
    )

    peak_idx = np.argmax(density_smooth)
    peak_val = density_smooth[peak_idx]

    # 1) Bimodal valley
    bimodal_candidate = None
    peaks, props = find_peaks(density_smooth, prominence=peak_val * 0.05)
    if len(peaks) >= 2:
        prominences = props["prominences"]
        top2 = peaks[np.argsort(prominences)[-2:]]
        left, right = np.sort(top2)
        valley_idx = left + np.argmin(density_smooth[left:right])
        valley_ratio = density_smooth[valley_idx] / min(
            density_smooth[left], density_smooth[right]
        )
        if valley_ratio < bimodal_valley_ratio_thresh:
            bimodal_candidate = centers[valley_idx]

    # 2) Right-tail taper
    taper_candidate = None
    for i in range(peak_idx + 1, len(density_smooth)):
        if density_smooth[i] < taper_fraction * peak_val:
            taper_candidate = centers[i]
            break

    # 3) Right-tail flat slope
    slope_candidate = None
    log_centers = np.log10(centers)
    d_density = np.gradient(density_smooth, log_centers)
    for i in range(peak_idx + 1, len(d_density)):
        if np.abs(d_density[i]) < slope_eps:
            slope_candidate = centers[i]
            break

    if bimodal_candidate is not None:
        return bimodal_candidate, centers, density_smooth

    conservative = [c for c in [taper_candidate, slope_candidate] if c is not None]
    if conservative:
        return max(conservative), centers, density_smooth

    return np.nan, centers, density_smooth


def compute_ipi_thresholds(
    poke_stats: Dict[Tuple[str, str], Dict[str, Any]],
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Estimate a robust IPI threshold for each session and store it in-place.

    Calls :func:`estimate_robust_ipi_threshold` on the sorted poke-event times
    for each session and stores the result as ``poke_stats[key]["robust_ipi"]``.

    Parameters
    ----------
    poke_stats:
        Output of :func:`compute_poke_stats`.  Modified in-place.

    Returns
    -------
    dict
        The modified *poke_stats* dict (same object).
    """
    for stats in poke_stats.values():
        poke_times = np.asarray(stats["poke_events"])
        ipis = np.diff(np.sort(poke_times))
        ipis = ipis[ipis > 0]
        cutoff, _, _ = estimate_robust_ipi_threshold(ipis)
        stats["robust_ipi"] = cutoff

    return poke_stats


# ---------------------------------------------------------------------------
# Bout-centric baseline fold change
# ---------------------------------------------------------------------------


def filter_baseline_pokes_by_ipi(
    poke_times: np.ndarray,
    *,
    t_center: float,
    analysis_before: float,
    analysis_after: float,
    baseline_selection_before: float,
    baseline_selection_after: float,
    min_ipi: float,
) -> np.ndarray:
    """Return poke times that satisfy both an IPI and an analysis-window criterion.

    A poke at time *t* qualifies if:

    - It lies within ``[t_center − analysis_before, t_center + analysis_after]``.
    - It is preceded by an IPI ≥ *min_ipi*.
    - Its preceding poke lies within the baseline-selection window relative to
      *t_center*.

    Parameters
    ----------
    poke_times:
        Sorted array of absolute poke times in seconds.
    t_center:
        Reference time (e.g., second poke after a switch) in seconds.
    analysis_before:
        Seconds before *t_center* defining the analysis window.
    analysis_after:
        Seconds after *t_center* defining the analysis window.
    baseline_selection_before:
        Seconds before *t_center* defining the allowed range for preceding pokes.
    baseline_selection_after:
        Seconds after *t_center* defining the allowed range for preceding pokes.
    min_ipi:
        Minimum required IPI (seconds) to qualify as a bout start.

    Returns
    -------
    np.ndarray
        Array of qualifying poke times (absolute seconds).
    """
    qualified = []
    for i in range(1, len(poke_times)):
        t = poke_times[i]
        t_prev = poke_times[i - 1]
        if not (t_center - analysis_before <= t <= t_center + analysis_after):
            continue
        if (t - t_prev) < min_ipi:
            continue
        t_prev_rel = t_prev - t_center
        if not (-baseline_selection_before <= t_prev_rel <= baseline_selection_after):
            continue
        qualified.append(t)
    return np.asarray(qualified)


def compute_timeseries_fold_change_exponential(
    poke_stats: Dict[Tuple[str, str], Dict[str, Any]],
    pre_days: int = 3,
    analysis_before: float = 60 * 60.0,
    analysis_after: float = 20 * 60.0,
    baseline_selection_before: float = 30 * 60.0,
    baseline_selection_after: float = 30 * 60.0,
    min_ipi: Optional[float] = None,
    epsilon_percentile: float = 5.0,
) -> Dict[Tuple[str, str, str, str], Dict[str, Any]]:
    """Compute time-resolved fold change using bout-centric baseline bouts.

    For each odor change and baseline day, qualifying poke-bout starts are
    identified via :func:`filter_baseline_pokes_by_ipi`.  Raw and log₂ fold
    changes between day 0 and the pooled baseline bouts are then computed.

    Parameters
    ----------
    poke_stats:
        Output of :func:`compute_poke_stats` (ideally with ``"robust_ipi"`` set
        by :func:`compute_ipi_thresholds`).
    pre_days:
        Number of baseline days before the change.
    analysis_before:
        Seconds before each bout-start to include in the rate window.
    analysis_after:
        Seconds after each bout-start to include in the rate window.
    baseline_selection_before:
        Seconds before *t_center* within which a preceding poke is accepted.
    baseline_selection_after:
        Seconds after *t_center* within which a preceding poke is accepted.
    min_ipi:
        Override IPI threshold.  ``None`` uses ``poke_stats[key]["robust_ipi"]``.
    epsilon_percentile:
        Percentile of non-zero rates used to estimate ε for fold-change
        regularisation.

    Returns
    -------
    dict
        Keyed by ``(subject, source_path, "odor_change_N", "timeseries_fold_change")``.
        Each entry contains baseline and day-0 time series, fold-change means
        and 95 % CIs (raw and log₂), and metadata.
    """
    results: Dict[Any, Dict[str, Any]] = {}

    for (subject, source_path), stats in poke_stats.items():
        t_master = np.asarray(stats["t"])
        r_master = np.asarray(stats["poke_rate"])
        poke_t = np.asarray(stats["poke_events"])
        odor_changes = list(zip(stats["odor_switch_t"], stats["odor_id"][1:]))

        resolved_min_ipi = (
            stats.get("robust_ipi", np.nan) if min_ipi is None else min_ipi
        )
        if not np.isfinite(resolved_min_ipi):
            continue

        for i, (event_time, new_odor) in enumerate(odor_changes):
            baseline_rate_ts = []

            for day in range(-pre_days, 0):
                day_event = event_time + day * 24 * 3600
                idx = np.where(poke_t > day_event)[0]
                if len(idx) < 2:
                    continue

                t_center_day = poke_t[idx[1]]
                qualified_pokes = filter_baseline_pokes_by_ipi(
                    poke_times=poke_t,
                    t_center=t_center_day,
                    analysis_before=analysis_before,
                    analysis_after=analysis_after,
                    baseline_selection_before=baseline_selection_before,
                    baseline_selection_after=baseline_selection_after,
                    min_ipi=resolved_min_ipi,
                )

                for tp in qualified_pokes:
                    mask = (t_master >= tp - analysis_before) & (
                        t_master <= tp + analysis_after
                    )
                    if mask.sum() < 2:
                        continue
                    baseline_rate_ts.append(r_master[mask])

            if len(baseline_rate_ts) < 2:
                continue

            baseline_stack = np.vstack(baseline_rate_ts)
            baseline_mean = baseline_stack.mean(axis=0)
            baseline_std = baseline_stack.std(axis=0, ddof=1)
            ci_base = 1.960 * baseline_std / np.sqrt(baseline_stack.shape[0])

            idx0 = np.where(poke_t > event_time)[0]
            if len(idx0) < 2:
                continue

            t0_center = poke_t[idx0[1]]
            mask0 = (t_master >= t0_center - analysis_before) & (
                t_master <= t0_center + analysis_after
            )
            t_axis = t_master[mask0] - t0_center
            day0_rate = r_master[mask0]

            all_rates_cat = np.concatenate([baseline_stack.ravel(), day0_rate])
            nonzero = all_rates_cat[all_rates_cat > 0]
            if nonzero.size == 0:
                continue
            epsilon = np.percentile(nonzero, epsilon_percentile)

            raw_fc_bouts = ((day0_rate + epsilon) / (baseline_stack + epsilon)) - 1
            log_fc_bouts = np.log2((day0_rate + epsilon) / (baseline_stack + epsilon))

            n_bouts = baseline_stack.shape[0]
            mean_fc_raw = raw_fc_bouts.mean(axis=0)
            ci_fc_raw = 1.960 * raw_fc_bouts.std(axis=0, ddof=1) / np.sqrt(n_bouts)
            mean_fc_log = log_fc_bouts.mean(axis=0)
            ci_fc_log = 1.960 * log_fc_bouts.std(axis=0, ddof=1) / np.sqrt(n_bouts)

            results[
                (subject, source_path, f"odor_change_{i}", "timeseries_fold_change")
            ] = {
                "t_window": t_axis,
                "baseline_poke_rate_timeseries": baseline_stack,
                "baseline_mean_rate": baseline_mean,
                "baseline_ci_95": ci_base,
                "day0_rate": day0_rate,
                "mean_fc_raw": mean_fc_raw,
                "ci_95_fc_raw": ci_fc_raw,
                "mean_fc_log": mean_fc_log,
                "ci_95_fc_log": ci_fc_log,
                "epsilon_used": epsilon,
                "min_ipi_used": resolved_min_ipi,
                "n_baseline_bouts": n_bouts,
                "prev_odor": stats["odor_id"][i],
                "new_odor": new_odor,
            }

    return results


# ---------------------------------------------------------------------------
# Cumulative poke counts
# ---------------------------------------------------------------------------


def compute_windowed_cumulative_poke_count(
    poke_stats: Dict[Tuple[str, str], Dict[str, Any]],
    pre_days: int = 3,
    window_size: float = 24 * 3600.0,
) -> Dict[Any, Dict[str, Any]]:
    """Compute cumulative poke count in a fixed window for day 0 and baseline days.

    The window is anchored to the second poke after the switch time for each
    day.  A baseline mean and 95 % CI (time-resolved, interpolated to a common
    axis) are also stored.

    Parameters
    ----------
    poke_stats:
        Output of :func:`compute_poke_stats` (requires ``"odor_switch_t"``).
    pre_days:
        Number of baseline days before the change.
    window_size:
        Duration of the analysis window in seconds (default 24 h).

    Returns
    -------
    dict
        Keyed by ``(subject, source_path, "odor_change_N", "day_D")`` for
        individual days and
        ``(subject, source_path, "odor_change_N", "baseline_cumulative_stats")``
        for the baseline summary.
    """
    cumulative_counts: Dict[Any, Dict[str, Any]] = {}

    for (subject, source_path), stats in poke_stats.items():
        poke_events = stats["poke_events"]
        odor_changes = list(zip(stats["odor_switch_t"], stats["odor_id"][1:]))

        for i, (event_time, new_odor) in enumerate(odor_changes):
            baseline_counts = []
            baseline_times = []

            # Baseline days
            for day in range(-pre_days, 0):
                start_time = event_time + day * 24 * 3600
                poke_after = poke_events[poke_events > start_time]
                if len(poke_after) < 2:
                    continue

                second_poke_time = poke_after[1]
                pokes_in_window = poke_events[
                    (poke_events >= second_poke_time)
                    & (poke_events < second_poke_time + window_size)
                ]
                t_rel = pokes_in_window - second_poke_time
                cum_counts = np.arange(1, len(t_rel) + 1)

                baseline_counts.append(cum_counts)
                baseline_times.append(t_rel)

                cumulative_counts[
                    (subject, source_path, f"odor_change_{i}", f"day_{day}")
                ] = {
                    "t_window": t_rel,
                    "cumulative_count": cum_counts,
                    "new_odor": new_odor,
                    "prev_odor": stats["odor_id"][i],
                }

            # Day 0
            poke_after_day0 = poke_events[poke_events > event_time]
            if len(poke_after_day0) < 2:
                continue

            common_time = np.arange(0, window_size, 60.0)
            second_poke_time_day0 = poke_after_day0[1]
            pokes_day0 = poke_events[
                (poke_events >= second_poke_time_day0)
                & (poke_events < second_poke_time_day0 + window_size)
            ]
            t_day0 = pokes_day0 - second_poke_time_day0
            cum_day0 = np.arange(1, len(t_day0) + 1)
            day0_interp = np.interp(common_time, t_day0, cum_day0, left=0, right=cum_day0[-1])

            cumulative_counts[
                (subject, source_path, f"odor_change_{i}", "day_0")
            ] = {
                "t_window": common_time,
                "cumulative_count": day0_interp,
                "new_odor": new_odor,
                "prev_odor": stats["odor_id"][i],
            }

            # Baseline mean + CI (add day_0 counts for the stats)
            baseline_counts.append(cum_day0)
            baseline_times.append(t_day0)

            baseline_interp = np.zeros((len(baseline_counts), len(common_time)))
            for j, (counts, times) in enumerate(zip(baseline_counts, baseline_times)):
                if len(counts) == 0:
                    baseline_interp[j] = np.zeros_like(common_time)
                else:
                    baseline_interp[j] = np.interp(
                        common_time, times, counts, left=0, right=counts[-1]
                    )

            mean_base = np.nanmean(baseline_interp, axis=0)
            n_days = np.sum(~np.isnan(baseline_interp), axis=0)
            std = np.nanstd(baseline_interp, axis=0, ddof=1)
            ci_95 = 1.960 * std / np.sqrt(n_days)

            cumulative_counts[
                (subject, source_path, f"odor_change_{i}", "baseline_cumulative_stats")
            ] = {
                "t_window": common_time,
                "mean_cumulative": mean_base,
                "ci_95": ci_95,
                "n_days_used": n_days,
                "new_odor": new_odor,
                "prev_odor": stats["odor_id"][i],
            }

    return cumulative_counts


# ---------------------------------------------------------------------------
# Poke-duration analysis
# ---------------------------------------------------------------------------


def extract_durations(
    start_time: float,
    poke_t: np.ndarray,
    durations: np.ndarray,
    mode: str = "n_pokes",
    n_pokes: int = 25,
    window_size: float = 2 * 3600.0,
    outlier_k: float = 3.0,
) -> np.ndarray:
    """Extract poke durations relative to the second poke after *start_time*.

    Extreme outliers (likely hardware artefacts) are replaced with the nearest
    valid poke duration.

    Parameters
    ----------
    start_time:
        Reference time (absolute seconds); durations start from the second
        poke after this time.
    poke_t:
        Sorted array of absolute poke-onset times (seconds).
    durations:
        Array of poke durations aligned to *poke_t*.
    mode:
        ``"n_pokes"`` – take the next *n_pokes* durations;
        ``"time_window"`` – take all durations within *window_size* seconds.
    n_pokes:
        Number of pokes to extract when ``mode="n_pokes"``.
    window_size:
        Duration of the extraction window when ``mode="time_window"``.
    outlier_k:
        IQR multiplier for the upper outlier threshold.

    Returns
    -------
    np.ndarray
        Cleaned poke durations (seconds).  Empty array if fewer than two pokes
        follow *start_time*.
    """
    idx = np.where(poke_t > start_time)[0]
    if len(idx) < 2:
        return np.array([])

    second_poke_idx = idx[1]
    second_poke_time = poke_t[second_poke_idx]

    if mode == "n_pokes":
        raw = durations[second_poke_idx : second_poke_idx + n_pokes].copy()
    else:
        end_time = second_poke_time + window_size
        in_window = (poke_t >= second_poke_time) & (poke_t <= end_time)
        raw = durations[in_window].copy()

    if raw.size == 0:
        return raw
    if raw.size < 4:
        return raw

    q1 = np.percentile(raw, 25)
    q3 = np.percentile(raw, 75)
    iqr = q3 - q1
    upper_thresh = q3 + outlier_k * iqr
    is_outlier = raw > upper_thresh

    if not np.any(is_outlier):
        return raw

    cleaned = raw.copy()
    for i in np.where(is_outlier)[0]:
        for j in range(i + 1, len(raw)):
            if raw[j] <= upper_thresh:
                cleaned[i] = raw[j]
                break
        else:
            valid_before = cleaned[:i][~is_outlier[:i]]
            if valid_before.size > 0:
                cleaned[i] = valid_before[-1]

    return cleaned


def compute_poke_duration_comparison(
    poke_stats: Dict[Tuple[str, str], Dict[str, Any]],
    pre_days: int = 3,
    mode: str = "n_pokes",
    n_pokes: int = 25,
    window_size: float = 2 * 3600.0,
) -> Dict[Any, Dict[str, Any]]:
    """Compute poke-duration distributions and paired statistics for each odor change.

    For each session and odor-change event, durations are extracted for day 0
    and each of the *pre_days* baseline days using :func:`extract_durations`.
    A paired Wilcoxon or paired t-test (chosen via Shapiro–Wilk normality
    check) is performed comparing day 0 to each baseline day.

    Parameters
    ----------
    poke_stats:
        Output of :func:`compute_poke_stats` (requires ``"odor_switch_t"`` and
        ``"poke_durations"``).
    pre_days:
        Number of baseline days before the change.
    mode:
        Extraction mode passed to :func:`extract_durations`
        (``"n_pokes"`` or ``"time_window"``).
    n_pokes:
        Number of pokes per day when ``mode="n_pokes"``.
    window_size:
        Window duration when ``mode="time_window"`` (seconds).

    Returns
    -------
    dict
        Keyed by ``(subject, source_path, "odor_change_N", "poke_duration_comparison")``.
        Each entry contains ``"durations"`` (dict of arrays per day),
        ``"stats"`` (p-value and test name per baseline day), ``"prev_odor"``,
        ``"new_odor"``.
    """
    results: Dict[Any, Dict[str, Any]] = {}

    for (subject, source_path), stats in poke_stats.items():
        poke_t = stats["poke_events"]
        durations = stats["poke_durations"] if len(stats["poke_durations"]) > 0 else np.array([])
        odor_changes = list(zip(stats["odor_switch_t"], stats["odor_id"][1:]))

        for i, (event_time, new_odor) in enumerate(odor_changes):
            day_data: Dict[str, np.ndarray] = {}

            for day in range(-pre_days, 0):
                start = event_time + day * 24 * 3600
                durs = extract_durations(
                    start, poke_t, durations, mode=mode,
                    n_pokes=n_pokes, window_size=window_size,
                )
                day_data[f"day_{day}"] = durs

            day0_durs = extract_durations(
                event_time, poke_t, durations, mode=mode,
                n_pokes=n_pokes, window_size=window_size,
            )
            day_data["day_0"] = day0_durs

            stats_out: Dict[str, Dict[str, Any]] = {}
            for key, base_durs in day_data.items():
                if key == "day_0":
                    continue
                n = min(len(base_durs), len(day0_durs))
                if n < 5:
                    stats_out[key] = {"p": np.nan, "test": None}
                    continue
                d0 = day0_durs[:n]
                db = base_durs[:n]
                normal = shapiro(d0).pvalue > 0.05 and shapiro(db).pvalue > 0.05
                if normal:
                    _, p = ttest_rel(d0, db)
                    test = "paired t-test"
                else:
                    _, p = wilcoxon(d0, db)
                    test = "wilcoxon"
                stats_out[key] = {"p": p, "test": test}

            results[
                (subject, source_path, f"odor_change_{i}", "poke_duration_comparison")
            ] = {
                "durations": day_data,
                "stats": stats_out,
                "prev_odor": stats["odor_id"][i],
                "new_odor": new_odor,
            }

    return results
