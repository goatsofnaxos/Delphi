"""Global hotkey listener for the experiment conductor.

Uses pynput to listen for configurable key combinations in a background thread.
Three actions are supported:

- Trigger pipeline cycle immediately
- Toggle upload pause/resume
- Signal experiment end
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)


def _parse_hotkey_string(hotkey_str: str):
    """Convert a pynput-style hotkey string like ``<ctrl>+<shift>+p`` to a set of keys."""
    from pynput import keyboard

    parts = [p.strip() for p in hotkey_str.split("+")]
    keys = set()
    for part in parts:
        if part.startswith("<") and part.endswith(">"):
            key_name = part[1:-1]
            try:
                keys.add(getattr(keyboard.Key, key_name))
            except AttributeError:
                log.warning("Unknown key: %s", part)
        else:
            keys.add(keyboard.KeyCode.from_char(part))
    return frozenset(keys)


class HotkeyListener:
    """Background thread that listens for global hotkeys.

    Parameters
    ----------
    hotkey_pipeline : str
        pynput hotkey string to trigger an immediate pipeline cycle.
    hotkey_upload_pause : str
        pynput hotkey string to toggle upload pause/resume.
    hotkey_end_experiment : str
        pynput hotkey string to signal experiment end.
    on_pipeline : Callable
        Called (no args) when the pipeline hotkey fires.
    on_upload_pause : Callable
        Called (no args) when the upload-pause hotkey fires.
    on_end_experiment : Callable
        Called (no args) when the end-experiment hotkey fires.
    """

    def __init__(
        self,
        hotkey_pipeline: str,
        hotkey_upload_pause: str,
        hotkey_end_experiment: str,
        on_pipeline: Callable,
        on_upload_pause: Callable,
        on_end_experiment: Callable,
    ):
        self._hk_pipeline = _parse_hotkey_string(hotkey_pipeline)
        self._hk_pause = _parse_hotkey_string(hotkey_upload_pause)
        self._hk_end = _parse_hotkey_string(hotkey_end_experiment)
        self._on_pipeline = on_pipeline
        self._on_pause = on_upload_pause
        self._on_end = on_end_experiment
        self._pressed: set = set()
        self._listener: Optional[object] = None

    def _on_press(self, key) -> None:
        self._pressed.add(key)
        current = frozenset(self._pressed)
        if current == self._hk_pipeline:
            log.info("Hotkey: trigger pipeline cycle")
            threading.Thread(target=self._on_pipeline, daemon=True).start()
        elif current == self._hk_pause:
            log.info("Hotkey: toggle upload pause")
            threading.Thread(target=self._on_pause, daemon=True).start()
        elif current == self._hk_end:
            log.info("Hotkey: end experiment")
            threading.Thread(target=self._on_end, daemon=True).start()

    def _on_release(self, key) -> None:
        self._pressed.discard(key)

    def start(self) -> None:
        """Start the listener in a background daemon thread."""
        from pynput import keyboard

        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()
        log.info(
            "Hotkey listener started. Pipeline=%s | Pause=%s | End=%s",
            self._hk_pipeline,
            self._hk_pause,
            self._hk_end,
        )

    def stop(self) -> None:
        """Stop the listener."""
        if self._listener:
            self._listener.stop()
