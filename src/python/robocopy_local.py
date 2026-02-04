import os
import sys
import subprocess
import argparse
from pathlib import Path
# =========================
# DEFINE YOUR PATHS HERE
# =========================

SOURCE_PATH = r"C:\Users\delphi_basement\Delphi\HIW\2026012901"
DESTINATION_PATH = r"C:\Users\delphi_basement\Northwestern University\finklab_sharepoint - Delphi_data\delphi_02\2026012901"
# Example UNC path:
# DESTINATION_PATH = r"\\Server01\Backup\data"

# =========================

source = Path(SOURCE_PATH).resolve()
destination = Path(DESTINATION_PATH).resolve()

command = (
    f'robocopy "{source}" "{destination}" '
    '/E /MOVE /J /R:2 /W:30 /NP'
)

print("Running command:")
print(command)
print("-" * 60)

result = subprocess.run(command, shell=True)

# Robocopy uses non-standard exit codes:
# 0–7 = success
if result.returncode <= 7:
    print("Robocopy completed successfully.")
    sys.exit(0)
else:
    print(f"Robocopy failed with exit code {result.returncode}")
    sys.exit(result.returncode)
