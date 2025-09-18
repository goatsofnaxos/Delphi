import os
import sys
import subprocess
import argparse
from pathlib import Path

############################################################
# Comment when running an experiment; uncomment when testing
#
parser = argparse.ArgumentParser(description="Moves data files from the specified source folder to a remote storage folder.")
parser.add_argument('source', type=str, help="The path where the local data is stored.")
parser.add_argument('destination', type=str, help="The remote path where data is to be stored.")
args = parser.parse_args()
source = Path(args.source)
destination = Path(args.destination)
#
# Comment when running an experiment; uncomment when testing
############################################################

############################################################
# Comment when testing; uncomment when running an experiment
#
#source = Path("C:/Users/bvw8415/Delphi/HIW3/")
#destination = Path("C:/Users/bvw8415/OneDrive - Northwestern University/Andrew Jacob Pixley Fink - FinkLab-SharedDrive/DATA/Behavior/Delphi/RobocopyHIW/")
#
# Comment when testing; uncomment when running an experiment
############################################################


robocopy_parameters = ["/E", "/MOVE", "/J", "/R:2", "/W:30", "/NP"]
process = subprocess.run(
    ["robocopy", source, destination] + robocopy_parameters,
    shell=True)
sys.exit(process.returncode)