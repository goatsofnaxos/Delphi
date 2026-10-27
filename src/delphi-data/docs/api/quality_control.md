# quality_control

Data quality control metrics and plots for Delphi behavioral sessions.

Generates a standard set of QC figures for every session regardless of
experiment type.  Call :func:`run_qc_plots` from any snapshot pipeline to
produce these figures automatically.

---

## Top-level pipeline

::: delphi_data.quality_control.run_qc_plots

---

## Parsing

::: delphi_data.quality_control.parse_valve_transitions

---

## Summary statistics

::: delphi_data.quality_control.compute_session_summary

::: delphi_data.quality_control.compute_valve_state_stats

---

## QC figures

::: delphi_data.quality_control.plot_valve_state_duration_histograms

::: delphi_data.quality_control.plot_valve_state_frequencies

::: delphi_data.quality_control.plot_poke_timing_overview

::: delphi_data.quality_control.plot_session_summary
