"""Deprecated — retained for backwards compatibility only.

The single-session conductor has been replaced by
:mod:`experiment_conductor.session_manager` and
:mod:`experiment_conductor.cli`, which support multiple sessions running
concurrently on a shared network drive.

The ``main`` entry point still works; it now delegates to :func:`.cli.main`.
"""
import warnings

warnings.warn(
    "experiment_conductor.conductor is deprecated. "
    "Use 'conductor' CLI or experiment_conductor.cli.main instead.",
    DeprecationWarning,
    stacklevel=2,
)

from experiment_conductor.cli import main  # noqa: F401, E402
