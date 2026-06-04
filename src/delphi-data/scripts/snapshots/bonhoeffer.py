"""Behavioral snapshot for Bonhoeffer olfactory learning experiments.

Generates a full suite of figures characterising how poke behavior changes
across odor transitions in the Bonhoeffer poke-port paradigm:

Session overview
~~~~~~~~~~~~~~~~
- Poke-rate time series over the full session (x-axis in days)
- Inter-poke interval distributions with estimated bout threshold
- Poke-duration violin plots per odor identity
- Daily poke count bar chart, colour-coded by dominant odor

Odor-change windows (poke-event aligned)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- Poke-rate windows around each detected odor change, one line per day

Switch-aligned analyses (2nd-poke alignment)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- Raw poke rate: baseline days (gray) vs day 0 (red)
- Δ-rate (day 0 − baseline mean) with 95 % CI
- Raw ratio fold change with 95 % CI
- Log₂ fold change with 95 % CI

Bout-centric fold change (exponential baseline bouts)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- Day-0 vs pooled baseline poke-rate time series
- Raw fold change (bout-centric baseline)
- Log₂ fold change (bout-centric baseline)

Cumulative counts and poke duration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- 24 h cumulative poke count around each odor switch
- Poke-duration distributions: day 0 vs baseline days

Usage (standalone)::

    python bonhoeffer.py --data-root /path/to/run_dir

Usage via the router::

    python data_snapshot.py --data-root /path/to/run_dir --experiment bonhoeffer

Usage via the installed CLI::

    delphi-data snapshot --data-root /path/to/run_dir --experiment bonhoeffer
"""

from __future__ import annotations

import argparse
import pathlib
import sys

# Ensure both the package root and the scripts directory are importable when
# this file is run directly or via the router.
_here = pathlib.Path(__file__).resolve()
_scripts_dir = _here.parent.parent          # scripts/
_pkg_root = _scripts_dir.parent             # repo root
for _p in [str(_pkg_root), str(_scripts_dir)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from snapshots._common import (
    build_odor_mapping,
    build_poke_stats,
    infer_subject_id,
    load_dataset,
    run_qc,
    try_save,
)

from delphi_data.poke_metrics import (
    compute_fold_change_day0_vs_baseline,
    compute_fold_change_day0_vs_baseline_ratio,
    compute_poke_duration_comparison,
    compute_timeseries_fold_change_exponential,
    compute_windowed_cumulative_poke_count,
    extract_poke_rate_relative_to_odor_switch,
    extract_poke_rate_windows,
)
from delphi_data.visualization import (
    plot_cumulative_poke_counts,
    plot_daily_poke_count,
    plot_ipi_distributions,
    plot_multiday_poke_rate_windows,
    plot_odor_switch_aligned_fold_change,
    plot_odor_switch_aligned_fold_change_exponential,
    plot_odor_switch_aligned_fold_change_log,
    plot_odor_switch_aligned_poke_rates,
    plot_odor_switch_aligned_poke_rates_from_fc_exp,
    plot_odor_switch_aligned_rate_difference,
    plot_poke_duration_by_odor,
    plot_poke_duration_comparison,
    plot_poke_rate_timeseries,
)


def run_snapshot(
    data_root: str | pathlib.Path,
    subject_id: str | None = None,
    tau: float = 600.0,
    dt: float = 60.0,
    overlap: float = 0.5,
    pre_days: int = 3,
    post_days: int = 1,
    n_pokes_duration: int = 25,
    camera_fps: float | None = None,
) -> None:
    """Generate all Bonhoeffer experiment figures for a single session.

    Parameters
    ----------
    data_root:
        Run-level session directory.  Must contain
        ``behavior/delphi_dataset.csv`` (produced by
        ``delphi-data build-dataset``).
    subject_id:
        Subject identifier.  When ``None``, inferred from the
        ``<subject>/<session>/<run>`` path hierarchy.
    tau:
        Exponential decay time constant for the poke-rate estimator (seconds).
    dt:
        Window length for the poke-rate estimator (seconds).
    overlap:
        Fractional window overlap for the poke-rate estimator ``[0, 1)``.
    pre_days:
        Number of baseline days before each odor change used in windowed and
        switch-aligned analyses.
    post_days:
        Number of post-change days included in the windowed analysis.
    n_pokes_duration:
        Number of pokes extracted per day for the duration-comparison figure.
    camera_fps:
        Override the camera frame rate (Hz) used for QC plots.  When
        ``None``, the rate is resolved from the Harp register → HardwareSettings
        → :data:`~delphi_data.quality_control.DEFAULT_CAMERA_FPS`.
    """
    data_root = pathlib.Path(data_root)
    result_dir = data_root / "behavior" / "results"
    result_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Load
    # -----------------------------------------------------------------------
    print(f"Loading: {data_root / 'behavior' / 'delphi_dataset.csv'}")
    df = load_dataset(data_root)
    n_reg = (df["poke_registered"] == True).sum()
    print(f"  {len(df)} rows  ({n_reg} registered pokes)")

    if subject_id is None:
        subject_id = infer_subject_id(data_root)
    print(f"  Subject: {subject_id}")

    source_path = str(data_root / "behavior" / "delphi_dataset.csv")
    df["subject_id"] = subject_id

    odor_mapping = build_odor_mapping(df)
    print(f"  Odor mapping: {odor_mapping}")

    # -----------------------------------------------------------------------
    # QC (runs for every experiment type)
    # -----------------------------------------------------------------------
    run_qc(
        df,
        result_dir=result_dir,
        subject_id=subject_id,
        data_root=data_root,
        camera_fps_override=camera_fps,
    )

    # -----------------------------------------------------------------------
    # Poke statistics
    # -----------------------------------------------------------------------
    print("Computing poke statistics …")
    poke_stats = build_poke_stats(
        df=df,
        subject_id=subject_id,
        source_path=source_path,
        odor_mapping=odor_mapping,
        tau=tau,
        dt=dt,
        overlap=overlap,
    )
    if not poke_stats:
        print("No valid poke events found.  Exiting.")
        return

    n_changes = len(next(iter(poke_stats.values()))["first_poke_t"])
    n_switches = len(next(iter(poke_stats.values()))["odor_switch_t"])
    print(f"  {n_changes} odor-change pokes detected, {n_switches} switch times available")

    # -----------------------------------------------------------------------
    # Derived computations
    # -----------------------------------------------------------------------
    print("Running derived computations …")

    # Poke-event-aligned windows (uses first_poke_t)
    windows = None
    try:
        windows = extract_poke_rate_windows(
            poke_stats, pre_w=3600, post_w=3600,
            pre_days=pre_days, post_days=post_days,
        )
    except Exception as exc:
        print(f"  extract_poke_rate_windows: {exc}")

    # 2nd-poke aligned windows (uses odor_switch_t)
    odor_switch_aligned = None
    try:
        odor_switch_aligned = extract_poke_rate_relative_to_odor_switch(
            poke_stats, pre_days=pre_days, pre_w=3600, post_w=3600,
        )
    except Exception as exc:
        print(f"  extract_poke_rate_relative_to_odor_switch: {exc}")

    # Δ-rate
    poke_rate_diff = None
    if odor_switch_aligned:
        try:
            poke_rate_diff = compute_fold_change_day0_vs_baseline(
                odor_switch_aligned, pre_days=pre_days,
            )
        except Exception as exc:
            print(f"  compute_fold_change_day0_vs_baseline: {exc}")

    # Ratio / log₂ fold change (day-level)
    poke_rate_fc = None
    if odor_switch_aligned:
        try:
            poke_rate_fc = compute_fold_change_day0_vs_baseline_ratio(
                odor_switch_aligned, pre_days=pre_days,
            )
        except Exception as exc:
            print(f"  compute_fold_change_day0_vs_baseline_ratio: {exc}")

    # Bout-centric fold change
    poke_rate_fc_exp = None
    try:
        poke_rate_fc_exp = compute_timeseries_fold_change_exponential(
            poke_stats,
            pre_days=pre_days,
            analysis_before=20 * 60,
            analysis_after=60 * 60,
            baseline_selection_before=12 * 3600,
            baseline_selection_after=12 * 3600,
            min_ipi=0,
        )
    except Exception as exc:
        print(f"  compute_timeseries_fold_change_exponential: {exc}")

    # Cumulative poke counts
    cum_pokes = None
    try:
        cum_pokes = compute_windowed_cumulative_poke_count(
            poke_stats, pre_days=pre_days, window_size=24 * 3600,
        )
    except Exception as exc:
        print(f"  compute_windowed_cumulative_poke_count: {exc}")

    # Poke-duration comparison
    poke_dur_results = None
    try:
        poke_dur_results = compute_poke_duration_comparison(
            poke_stats, pre_days=pre_days, mode="n_pokes", n_pokes=n_pokes_duration,
        )
    except Exception as exc:
        print(f"  compute_poke_duration_comparison: {exc}")

    # -----------------------------------------------------------------------
    # Figures
    # -----------------------------------------------------------------------
    print("Generating figures …")

    dur_col = (
        "poke_duration" if "poke_duration" in df.columns
        else "poke_to_beam_offset_duration"
    )

    # --- Session overview ---
    try_save(
        plot_poke_rate_timeseries,
        result_dir / "poke_rate_timeseries.png",
        "Poke Rate Over Time (Exponential Decay Smoothing)",
        poke_stats, time_unit="days",
    )
    try_save(
        plot_ipi_distributions,
        result_dir / "ipi_distributions.png",
        "Inter-Poke Interval Distributions",
        poke_stats,
    )
    try_save(
        plot_poke_duration_by_odor,
        result_dir / "poke_duration_by_odor.png",
        "Poke Duration by Odor",
        df, dur_col,
    )
    if "datetime" in df.columns:
        try_save(
            plot_daily_poke_count,
            result_dir / "daily_poke_count.png",
            "Daily Poke Count",
            df,
        )

    # --- Poke-event-aligned windows ---
    if windows:
        try_save(
            plot_multiday_poke_rate_windows,
            result_dir / "multiday_poke_rate_windows.png",
            "Poke Rate Aligned to Poke-Detected Odor Changes",
            windows, pre_days, post_days,
        )

    # --- 2nd-poke aligned: raw rate ---
    if odor_switch_aligned:
        try_save(
            plot_odor_switch_aligned_poke_rates,
            result_dir / "poke_rate_odor_switch_aligned.png",
            "Poke Rate Aligned to 2nd Poke After Odor Change",
            odor_switch_aligned, pre_days, "minutes",
        )

    # --- 2nd-poke aligned: Δ-rate ---
    if poke_rate_diff:
        try_save(
            plot_odor_switch_aligned_rate_difference,
            result_dir / "poke_rate_difference_odor_switch.png",
            "Δ-Rate (Day 0 − Baseline) Aligned to 2nd Poke After Odor Change",
            poke_rate_diff, "minutes",
        )

    # --- 2nd-poke aligned: fold change ---
    if poke_rate_fc:
        try_save(
            plot_odor_switch_aligned_fold_change,
            result_dir / "poke_rate_fold_change_odor_switch.png",
            "Baseline Fold Change in Poke Rate (Day 0 vs Previous Days)",
            poke_rate_fc, "minutes",
        )
        try_save(
            plot_odor_switch_aligned_fold_change_log,
            result_dir / "poke_rate_fold_change_log_odor_switch.png",
            "Baseline Log₂ Fold Change in Poke Rate (Day 0 vs Previous Days)",
            poke_rate_fc, "minutes",
        )

    # --- Bout-centric fold change ---
    if poke_rate_fc_exp:
        try_save(
            plot_odor_switch_aligned_poke_rates_from_fc_exp,
            result_dir / "poke_rate_timeseries_odor_switch.png",
            "Poke Rate: Day 0 vs Baseline Bouts (2nd-Poke Aligned)",
            poke_rate_fc_exp, "minutes",
        )
        try_save(
            plot_odor_switch_aligned_fold_change_exponential,
            result_dir / "poke_rate_log_fold_change_exp.png",
            "Log₂ Fold Change in Poke Rate (Bout-Centric Baseline)",
            poke_rate_fc_exp, "log", "minutes",
        )
        try_save(
            plot_odor_switch_aligned_fold_change_exponential,
            result_dir / "poke_rate_raw_fold_change_exp.png",
            "Raw Fold Change in Poke Rate (Bout-Centric Baseline)",
            poke_rate_fc_exp, "raw", "minutes",
        )

    # --- Cumulative counts ---
    if cum_pokes:
        try_save(
            plot_cumulative_poke_counts,
            result_dir / "cumulative_poke_count_odor_switch.png",
            "Cumulative Poke Count in 24 h Window After 2nd Poke Post Odor Change",
            cum_pokes,
        )

    # --- Duration comparison ---
    if poke_dur_results:
        try_save(
            plot_poke_duration_comparison,
            result_dir / "poke_duration_comparison_odor_switch.png",
            f"Poke Duration: Day 0 vs Baseline (first {n_pokes_duration} pokes)",
            poke_dur_results,
        )

    print(f"\nDone.  Figures saved to: {result_dir}")


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------


def _parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments for the Bonhoeffer snapshot script.

    Parameters
    ----------
    argv:
        Argument list.  ``None`` reads from ``sys.argv[1:]``.

    Returns
    -------
    argparse.Namespace
        Parsed namespace with ``data_root``, ``subject_id``, ``tau``, ``dt``,
        ``overlap``, ``pre_days``, ``post_days``, and ``n_pokes_duration``
        attributes.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Generate Bonhoeffer experiment snapshot figures.\n\n"
            "Expects 'behavior/delphi_dataset.csv' inside DATA_ROOT and writes "
            "all figures to 'behavior/results/'."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    from delphi_data.settings import settings as _s

    parser.add_argument(
        "--data-root", default=_s.data_root, required=_s.data_root is None,
        help=f"Run-level session directory.  [env: DELPHI_DATA_ROOT, current: {_s.data_root!r}]",
    )
    parser.add_argument("--subject-id", default=None,
                        help="Subject ID (default: inferred from path).")
    parser.add_argument(
        "--tau", type=float, default=_s.tau,
        help=f"Decay time constant, seconds.  [env: DELPHI_TAU, current: {_s.tau}]",
    )
    parser.add_argument(
        "--dt", type=float, default=_s.dt,
        help=f"Rate-estimator window, seconds.  [env: DELPHI_DT, current: {_s.dt}]",
    )
    parser.add_argument(
        "--overlap", type=float, default=_s.overlap,
        help=f"Window overlap [0,1).  [env: DELPHI_OVERLAP, current: {_s.overlap}]",
    )
    parser.add_argument(
        "--pre-days", type=int, default=_s.pre_days,
        help=f"Baseline days before each odor change.  [env: DELPHI_PRE_DAYS, current: {_s.pre_days}]",
    )
    parser.add_argument(
        "--post-days", type=int, default=_s.post_days,
        help=f"Post-change days in windowed analysis.  [env: DELPHI_POST_DAYS, current: {_s.post_days}]",
    )
    parser.add_argument(
        "--n-pokes-duration", type=int, default=_s.n_pokes_duration,
        help=f"Pokes per day for duration comparison.  [env: DELPHI_N_POKES_DURATION, current: {_s.n_pokes_duration}]",
    )
    parser.add_argument(
        "--camera-fps", type=float, default=_s.camera_fps, dest="camera_fps",
        help=(
            "Override camera frame rate for QC plots (Hz).  "
            "Leave unset for auto-detect (Harp register → rig config → "
            f"HardwareSettings → default).  [env: DELPHI_CAMERA_FPS, current: {_s.camera_fps}]"
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    run_snapshot(
        data_root=args.data_root,
        subject_id=args.subject_id,
        tau=args.tau,
        dt=args.dt,
        overlap=args.overlap,
        pre_days=args.pre_days,
        post_days=args.post_days,
        n_pokes_duration=args.n_pokes_duration,
    )
