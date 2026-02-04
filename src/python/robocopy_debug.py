import os
import sys
import subprocess
import argparse
from pathlib import Path

#parser = argparse.ArgumentParser(description="Moves data files from the specified source folder to a remote storage folder.")
#parser.add_argument('source', type=str, help="The path where the local data is stored.")
#parser.add_argument('destination', type=str, help="The remote path where data is to be stored.")
#args = parser.parse_args()
source = Path(r"C:\\Users\\delphi_01\\Delphi\\HIW\\robocopytest")
destination = Path(r"C:\\Users\\delphi_basement\\Northwestern University\\finklab_sharepoint - Delphi_data\\robocopytest")
robocopy_parameters = ["/E", "/MOVE", "/J", "/R:2", "/W:30", "/NP"]
process = subprocess.run(
    ["robocopy", source, destination] + robocopy_parameters,
    shell=True)
sys.exit(process.returncode)