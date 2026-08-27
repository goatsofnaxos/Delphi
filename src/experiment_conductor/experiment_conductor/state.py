"""Deprecated — retained for backwards compatibility only.

Session state is now managed by
:class:`~experiment_conductor.session.SessionState` and
:class:`~experiment_conductor.session.SessionPhase`.

This module re-exports those names for any existing imports.
"""
import warnings

warnings.warn(
    "experiment_conductor.state is deprecated. "
    "Use experiment_conductor.session.SessionState / SessionPhase instead.",
    DeprecationWarning,
    stacklevel=2,
)

from experiment_conductor.session import SessionPhase as Phase  # noqa: F401, E402
from experiment_conductor.session import SessionState as ConductorState  # noqa: F401, E402
