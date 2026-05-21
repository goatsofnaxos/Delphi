# metadata_generator/cli.py
import argparse
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Generate AIND metadata files")

    parser.add_argument("--subject-id", help="Subject ID")
    parser.add_argument("--protocol-id", help="Protocol ID")
    parser.add_argument(
        "--current-experiment",
        choices=["delphi", "pirouette", "delphi_pirouette"],
        help="Experiment type",
    )

    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--metadata-output-path", type=Path)
    parser.add_argument("--surgery-notes-path", type=Path)
    parser.add_argument("--instrument-id")
    parser.add_argument("--experiment-room")
    parser.add_argument("--acquisition-type")
    parser.add_argument("--delphi-computer-id")

    parser.add_argument("--experimenters", help="Comma-separated list")
    parser.add_argument("--surgeons", help="Comma-separated list")

    # Flags to disable steps
    parser.add_argument("--skip-subject", action="store_true")
    parser.add_argument("--skip-instrument", action="store_true")
    parser.add_argument("--skip-procedures", action="store_true")
    parser.add_argument("--skip-acquisition", action="store_true")

    return parser.parse_args()
