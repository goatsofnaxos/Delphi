"""Environment-based configuration for the ``delphi-data`` package.

Settings are loaded from a ``.env`` file (searched upward from the current
working directory) and from shell environment variables.  Command-line
arguments always take precedence over both.

Priority order (highest → lowest)
-----------------------------------
1. **CLI argument** — explicit flag passed to the command.
2. **Shell environment variable** — already exported in the calling shell.
3. **``.env`` file** — key=value pairs found in the nearest ``.env`` file.
4. **Code default** — the built-in fallback defined in this module.

Variable names
--------------
All Delphi settings use the ``DELPHI_`` prefix.  See ``.env.example`` at the
repository root for a complete annotated template.

Typical usage
-------------
In CLI argument parsers, replace hard-coded defaults with settings values so
that a ``.env`` file acts as a persistent default::

    from delphi_data.settings import settings

    parser.add_argument("--tau", type=float, default=settings.tau)

When the user passes ``--tau 300`` the CLI value wins.  When they don't,
the ``.env`` / env-var value is used.  When neither is set, the built-in
default (600.0) applies.
"""

from __future__ import annotations

import os
import pathlib
from typing import Optional

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------
# Search upward from the current working directory so that a .env at the
# project root is found regardless of where the command is invoked.
load_dotenv(override=False)  # shell env vars always win over the .env file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(key: str, default: str | None = None) -> str | None:
    """Return the raw string value of environment variable *key*.

    Parameters
    ----------
    key:
        Environment variable name (e.g. ``"DELPHI_TAU"``).
    default:
        Value to return when the variable is unset or empty.

    Returns
    -------
    str or None
        Raw string value, or *default* when absent or empty.
    """
    val = os.getenv(key, "").strip()
    return val if val else default


def _float(key: str, default: float) -> float:
    """Read a float setting from the environment.

    Parameters
    ----------
    key:
        Environment variable name.
    default:
        Fallback value when the variable is unset or unparseable.

    Returns
    -------
    float
    """
    raw = _get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int(key: str, default: int) -> int:
    """Read an integer setting from the environment.

    Parameters
    ----------
    key:
        Environment variable name.
    default:
        Fallback value when the variable is unset or unparseable.

    Returns
    -------
    int
    """
    raw = _get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    """Read a boolean setting from the environment.

    Truthy strings: ``"1"``, ``"true"``, ``"yes"``, ``"on"`` (case-insensitive).
    All other non-empty values are treated as ``False``.

    Parameters
    ----------
    key:
        Environment variable name.
    default:
        Fallback value when the variable is unset or empty.

    Returns
    -------
    bool
    """
    raw = _get(key)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _optional_float(key: str) -> Optional[float]:
    """Read an optional float setting from the environment.

    Parameters
    ----------
    key:
        Environment variable name.

    Returns
    -------
    float or None
        Parsed float, or ``None`` when the variable is unset or empty.
    """
    raw = _get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _optional_str(key: str) -> Optional[str]:
    """Read an optional string setting from the environment.

    Parameters
    ----------
    key:
        Environment variable name.

    Returns
    -------
    str or None
        String value, or ``None`` when the variable is unset or empty.
    """
    return _get(key)


def _optional_path(key: str) -> Optional[str]:
    """Read an optional file-system path setting from the environment.

    Normalises the value to forward slashes so that Windows paths written
    with backslashes in ``.env`` files (where ``python-dotenv`` would expand
    ``\\b``, ``\\t``, etc. as escape sequences inside double-quoted values)
    are handled safely.  Use **forward slashes** or **single quotes** in the
    ``.env`` file for Windows paths to avoid escape-sequence expansion
    entirely.

    Parameters
    ----------
    key:
        Environment variable name.

    Returns
    -------
    str or None
        Path string with backslashes converted to forward slashes, or
        ``None`` when the variable is unset or empty.
    """
    raw = _get(key)
    if raw is None:
        return None
    # Normalise to forward slashes; pathlib.Path handles these on Windows.
    return raw.replace("\\", "/")


# ---------------------------------------------------------------------------
# Settings object
# ---------------------------------------------------------------------------

class _Settings:
    """Typed access to all ``DELPHI_*`` environment settings.

    Values are resolved once at import time.  Re-instantiate or call
    :func:`reload` after programmatically changing environment variables
    if needed.

    Attributes
    ----------
    tau:
        Exponential decay time constant for poke-rate estimation (seconds).
        ``DELPHI_TAU`` — default ``600.0``.
    dt:
        Window length for poke-rate estimation (seconds).
        ``DELPHI_DT`` — default ``60.0``.
    overlap:
        Fractional window overlap for poke-rate estimation ``[0, 1)``.
        ``DELPHI_OVERLAP`` — default ``0.5``.
    pre_days:
        Baseline days before each odor change for windowed analyses.
        ``DELPHI_PRE_DAYS`` — default ``3``.
    post_days:
        Post-change days included in windowed analyses.
        ``DELPHI_POST_DAYS`` — default ``1``.
    n_pokes_duration:
        Pokes extracted per day for the duration-comparison figure.
        ``DELPHI_N_POKES_DURATION`` — default ``25``.
    camera_fps:
        Camera frame-rate override for QC plots (Hz).  ``None`` means
        auto-detect from hardware register → rig config → HardwareSettings →
        default.  ``DELPHI_CAMERA_FPS`` — default ``None``.
    default_camera_fps:
        Last-resort camera frame rate when all auto-detect sources fail (Hz).
        ``DELPHI_DEFAULT_CAMERA_FPS`` — default ``60.0``.
    firmware:
        Default firmware version string (e.g. ``"0.1.0"``).  ``None`` means
        auto-detect from ``device.yml``.  ``DELPHI_FIRMWARE`` — default ``None``.
    workers:
        Parallel clip-export threads for the ``create-clips`` command.
        ``DELPHI_WORKERS`` — default ``4``.
    no_delete:
        Skip deletion of PortCamera source files after clip export.
        ``DELPHI_NO_DELETE`` — default ``False``.
    no_consolidate:
        Disable automatic run-directory consolidation in ``build-dataset``.
        ``DELPHI_NO_CONSOLIDATE`` — default ``False``.
    skip_build:
        Skip the build-dataset step in the full pipeline.
        ``DELPHI_SKIP_BUILD`` — default ``False``.
    skip_clips:
        Skip the create-clips step in the full pipeline.
        ``DELPHI_SKIP_CLIPS`` — default ``False``.
    skip_snapshot:
        Skip the snapshot step in the full pipeline.
        ``DELPHI_SKIP_SNAPSHOT`` — default ``False``.
    dataset_append:
        When ``True`` and ``delphi_dataset.csv`` already exists, ingest the
        full dataset and append new rows, deduplicating on
        ``beam_break_onset``.  When ``False`` (default), the build step is
        skipped if the CSV exists.  ``DELPHI_DATASET_APPEND`` — default
        ``False``.
    experiment:
        Default experiment type for the ``snapshot`` command and the full
        processing pipeline (e.g. ``"bonhoeffer"``).
        ``DELPHI_EXPERIMENT`` — default ``None``.
    data_root:
        Default run-level session directory path.  Used as the default for
        ``--data-root`` across all commands (``snapshot``, ``build-dataset``,
        ``consolidate``).  ``DELPHI_DATA_ROOT`` — default ``None``.
    """

    # Snapshot settings
    tau:                float          = _float("DELPHI_TAU", 600.0)
    dt:                 float          = _float("DELPHI_DT", 60.0)
    overlap:            float          = _float("DELPHI_OVERLAP", 0.5)
    pre_days:           int            = _int("DELPHI_PRE_DAYS", 3)
    post_days:          int            = _int("DELPHI_POST_DAYS", 1)
    n_pokes_duration:   int            = _int("DELPHI_N_POKES_DURATION", 25)
    camera_fps:         Optional[float] = _optional_float("DELPHI_CAMERA_FPS")

    # QC settings
    default_camera_fps: float          = _float("DELPHI_DEFAULT_CAMERA_FPS", 60.0)

    # Build / ingest settings
    firmware:           Optional[str]  = _optional_str("DELPHI_FIRMWARE")
    no_consolidate:     bool           = _bool("DELPHI_NO_CONSOLIDATE", False)
    dataset_append:     bool           = _bool("DELPHI_DATASET_APPEND", False)

    # Pipeline step toggles
    skip_build:         bool           = _bool("DELPHI_SKIP_BUILD", False)
    skip_clips:         bool           = _bool("DELPHI_SKIP_CLIPS", False)
    skip_snapshot:      bool           = _bool("DELPHI_SKIP_SNAPSHOT", False)

    # Video clip settings
    workers:            int            = _bool("DELPHI_WORKERS", 4)  # type: ignore[assignment]
    no_delete:          bool           = _bool("DELPHI_NO_DELETE", False)

    # Experiment type (snapshot / pipeline)
    experiment:         Optional[str]  = _optional_str("DELPHI_EXPERIMENT")

    # Path shortcuts — use _optional_path to normalise backslashes on Windows
    data_root:          Optional[str]  = _optional_path("DELPHI_DATA_ROOT")

    def __repr__(self) -> str:  # noqa: D105
        fields = [
            f"tau={self.tau}",
            f"dt={self.dt}",
            f"overlap={self.overlap}",
            f"pre_days={self.pre_days}",
            f"post_days={self.post_days}",
            f"n_pokes_duration={self.n_pokes_duration}",
            f"camera_fps={self.camera_fps}",
            f"default_camera_fps={self.default_camera_fps}",
            f"firmware={self.firmware!r}",
            f"workers={self.workers}",
            f"no_delete={self.no_delete}",
            f"no_consolidate={self.no_consolidate}",
            f"experiment={self.experiment!r}",
            f"skip_build={self.skip_build}",
            f"skip_clips={self.skip_clips}",
            f"skip_snapshot={self.skip_snapshot}",
            f"dataset_append={self.dataset_append}",
            f"data_root={self.data_root!r}",
        ]
        return f"Settings({', '.join(fields)})"


# Fix workers — _bool was used accidentally above; read as int
_Settings.workers = _int("DELPHI_WORKERS", 4)  # type: ignore[assignment]

#: Singleton settings object.  Import and use this directly::
#:
#:     from delphi_data.settings import settings
#:     print(settings.tau)
settings: _Settings = _Settings()


def reload() -> _Settings:
    """Re-read all ``DELPHI_*`` environment variables and return a fresh :class:`_Settings`.

    Useful in tests or interactive sessions where environment variables are
    changed programmatically after import.

    Returns
    -------
    _Settings
        New settings instance with current environment values.  Also updates
        the module-level :data:`settings` singleton.
    """
    global settings
    load_dotenv(override=False)
    settings = _Settings()
    settings.workers = _int("DELPHI_WORKERS", 4)
    return settings
