from pathlib import Path

from delphi_data.curation import consolidate_session_runs
from delphi_data.ingestion import ingest


def build_dataset(
    session_dir: Path,
    firmware: str,
    consolidate_runs: bool = True,
):
    """
    Build Delphi dataset and save CSV to behavior folder.

    Args:
        session_dir: path to session directory
        firmware: firmware version string (e.g. "0.3.0")
        consolidate_runs: whether to merge multiple run dirs
    """

    # -----------------------------
    # OPTIONAL: CONSOLIDATE RUNS
    # -----------------------------
    run_dirs = [p for p in session_dir.iterdir() if p.is_dir()]

    # Heuristic: multiple subdirectories likely means multiple runs
    multiple_runs_detected = len(run_dirs) > 1

    if multiple_runs_detected:
        print(f"Detected {len(run_dirs)} run directories in session: {session_dir}")
    else:
        print(f"Single run detected in session: {session_dir}")

    if multiple_runs_detected and consolidate_runs:
        print("Consolidating run directories...")
        consolidate_session_runs(session_dir)
        print("✅ Consolidation complete.")
    elif multiple_runs_detected and not consolidate_runs:
        print("⚠️ Multiple runs detected but consolidation is disabled.")
    else:
        print("✅ No consolidation needed.")

    # -----------------------------
    # INGEST DATA
    # -----------------------------
    df = ingest(
        data_root_path=session_dir,
        firmware=firmware,
    )

    # -----------------------------
    # ENSURE OUTPUT DIRECTORY
    # -----------------------------
    behavior_dir = session_dir / "behavior"
    behavior_dir.mkdir(exist_ok=True)

    # -----------------------------
    # SAVE CSV
    # -----------------------------
    output_path = behavior_dir / "delphi_dataset.csv"
    df.to_csv(output_path, index=False)

    print(f"✅ Dataset saved to: {output_path}")

    return df


# -----------------------------
# EXAMPLE USAGE
# -----------------------------
if __name__ == "__main__":
    session_dir = Path(
        r"C:\Users\brandon.pratt\Desktop\data\test_data\842456\2026-03-20T20-23-05_test\2026-03-20T20-23-37"
    )

    df = build_dataset(
        session_dir=session_dir,
        firmware="0.1.0",  # None will default to latest firmware config, which is usually what you want for new datasets
        consolidate_runs=True,  # toggle here
    )
