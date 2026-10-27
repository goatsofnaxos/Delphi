"""Extract poke-triggered video clips from Delphi session data.

Thin script wrapper around :mod:`delphi_data.video_processing`.
All logic lives in the package module; this file exists so the script can be
run directly from the ``scripts/`` directory.

Usage::

    python scripts/create_poke_clips.py /data/my_experiment
    python scripts/create_poke_clips.py /data/my_experiment --output /scratch/clips
    python scripts/create_poke_clips.py /data/my_experiment --no-delete --workers 8

This script is also available via the installed ``delphi-data`` CLI::

    delphi-data create-clips /data/my_experiment

System requirements
-------------------
``ffmpeg`` and ``ffprobe`` must be on ``PATH``.

Python requirements
-------------------
``opencv-python`` must be installed::

    pip install delphi-data[video]
"""

from __future__ import annotations

import pathlib
import sys

# Ensure the package root is importable when running the script directly.
_script_dir = pathlib.Path(__file__).resolve().parent
_pkg_root = _script_dir.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

from delphi_data.video_processing import main

if __name__ == "__main__":
    main()
