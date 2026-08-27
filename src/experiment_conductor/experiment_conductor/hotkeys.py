"""Deprecated — hotkey support has been removed.

The refactored conductor runs headlessly on a shared network drive and no
longer listens for global keyboard shortcuts.  This module is retained only
to avoid import errors in existing scripts; it does nothing.
"""
import warnings

warnings.warn(
    "experiment_conductor.hotkeys is deprecated and will be removed in a "
    "future release.  The refactored conductor has no hotkey support.",
    DeprecationWarning,
    stacklevel=2,
)


class HotkeyListener:
    """No-op stub — hotkeys are no longer supported."""

    def __init__(self, **kwargs):  # noqa: ANN003
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass
