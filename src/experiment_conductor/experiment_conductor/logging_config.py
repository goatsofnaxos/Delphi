"""Logging helpers for the experiment conductor.

Defines a custom ``VERBOSE`` level (15) that sits between ``DEBUG`` (10) and
``INFO`` (20).  Use it for step-level detail that is more useful than DEBUG
byte-shuffling but noisier than the INFO summary lines shown by default.

Usage::

    from experiment_conductor.logging_config import VERBOSE
    log.log(VERBOSE, "Found %d run dirs; earliest: %s", n, earliest)

The ``setup_logging`` helper configures the root logger once from the
conductor's ``--verbose`` count flag.
"""
from __future__ import annotations

import logging
import sys

# Custom level — sits between DEBUG (10) and INFO (20)
VERBOSE: int = 15
logging.addLevelName(VERBOSE, "VERBOSE")

# Map from -v count to log level
_VERBOSITY_MAP: dict[int, int] = {
    0: logging.INFO,     # default  — phase transitions, success/failure
    1: VERBOSE,          # -v       — step details, counts, offsets
    2: logging.DEBUG,    # -vv      — per-file ops, subprocess args, raw values
}


def setup_logging(verbosity: int, *, stream=None) -> None:
    """Configure root logging from a verbosity count (0, 1, or 2+).

    Parameters
    ----------
    verbosity:
        Number of ``-v`` flags passed on the CLI (clamped to 0–2).
    stream:
        Output stream (default ``sys.stdout``).
    """
    level = _VERBOSITY_MAP.get(min(verbosity, 2), logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=stream or sys.stdout,
        force=True,
    )
