from pathlib import Path

from delphi_data.curation import consolidate_session_runs

session_dir = Path(
    r"C:\Users\brandon.pratt\Desktop\data\test_data\842456\2026-03-20T20-23-05"
)
consolidate_session_runs(session_dir)
