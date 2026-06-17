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
    hotkey_toggle_pipeline : str, optional
        pynput hotkey string to toggle pipeline enable/disable.
    hotkey_toggle_metadata : str, optional
        pynput hotkey string to toggle metadata enable/disable.
    hotkey_toggle_upload : str, optional
        pynput hotkey string to toggle upload enable/disable.
    on_toggle_pipeline : Callable, optional
        Called (no args) when the pipeline-toggle hotkey fires.
    on_toggle_metadata : Callable, optional
        Called (no args) when the metadata-toggle hotkey fires.
    on_toggle_upload : Callable, optional
        Called (no args) when the upload-toggle hotkey fires.
    hotkey_update_end_time : str, optional
        pynput hotkey string to update the acquisition end time.
    hotkey_retry_metadata : str, optional
        pynput hotkey string to reset and retry metadata generation.
    on_update_end_time : Callable, optional
        Called (no args) when the update-end-time hotkey fires.
    on_retry_metadata : Callable, optional
        Called (no args) when the retry-metadata hotkey fires.
    """

    def __init__(
        self,
        hotkey_pipeline: str,
        hotkey_upload_pause: str,
        hotkey_end_experiment: str,
        on_pipeline: Callable,
        on_upload_pause: Callable,
        on_end_experiment: Callable,
        hotkey_toggle_pipeline: Optional[str] = None,
        hotkey_toggle_metadata: Optional[str] = None,
        hotkey_toggle_upload: Optional[str] = None,
        on_toggle_pipeline: Optional[Callable] = None,
        on_toggle_metadata: Optional[Callable] = None,
        on_toggle_upload: Optional[Callable] = None,
        hotkey_update_end_time: Optional[str] = None,
        hotkey_retry_metadata: Optional[str] = None,
        on_update_end_time: Optional[Callable] = None,
        on_retry_metadata: Optional[Callable] = None,
    ):
        self._hk_pipeline = _parse_hotkey_string(hotkey_pipeline)
        self._hk_pause = _parse_hotkey_string(hotkey_upload_pause)
        self._hk_end = _parse_hotkey_string(hotkey_end_experiment)
        self._on_pipeline = on_pipeline
        self._on_pause = on_upload_pause
        self._on_end = on_end_experiment

        # Optional toggle hotkeys
        self._hk_toggle_pipeline = _parse_hotkey_string(hotkey_toggle_pipeline) if hotkey_toggle_pipeline else None
        self._hk_toggle_metadata = _parse_hotkey_string(hotkey_toggle_metadata) if hotkey_toggle_metadata else None
        self._hk_toggle_upload = _parse_hotkey_string(hotkey_toggle_upload) if hotkey_toggle_upload else None
        self._on_toggle_pipeline = on_toggle_pipeline
        self._on_toggle_metadata = on_toggle_metadata
        self._on_toggle_upload = on_toggle_upload

        # Optional action hotkeys
        self._hk_update_end_time = _parse_hotkey_string(hotkey_update_end_time) if hotkey_update_end_time else None
        self._hk_retry_metadata = _parse_hotkey_string(hotkey_retry_metadata) if hotkey_retry_metadata else None
        self._on_update_end_time = on_update_end_time
        self._on_retry_metadata = on_retry_metadata

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
        elif self._hk_toggle_pipeline and current == self._hk_toggle_pipeline and self._on_toggle_pipeline:
            log.info("Hotkey: toggle pipeline enable")
            threading.Thread(target=self._on_toggle_pipeline, daemon=True).start()
        elif self._hk_toggle_metadata and current == self._hk_toggle_metadata and self._on_toggle_metadata:
            log.info("Hotkey: toggle metadata enable")
            threading.Thread(target=self._on_toggle_metadata, daemon=True).start()
        elif self._hk_toggle_upload and current == self._hk_toggle_upload and self._on_toggle_upload:
            log.info("Hotkey: toggle upload enable")
            threading.Thread(target=self._on_toggle_upload, daemon=True).start()
        elif self._hk_update_end_time and current == self._hk_update_end_time and self._on_update_end_time:
            log.info("Hotkey: update acquisition end time")
            threading.Thread(target=self._on_update_end_time, daemon=True).start()
        elif self._hk_retry_metadata and current == self._hk_retry_metadata and self._on_retry_metadata:
            log.info("Hotkey: retry metadata generation")
            threading.Thread(target=self._on_retry_metadata, daemon=True).start()

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
            self._hk_pipeline, self._hk_pause, self._hk_end,
        )
        if self._hk_toggle_pipeline:
            log.info("  Toggle pipeline=%s | Toggle metadata=%s | Toggle upload=%s",
                     self._hk_toggle_pipeline, self._hk_toggle_metadata, self._hk_toggle_upload)
        if self._hk_update_end_time:
            log.info("  Update end time=%s | Retry metadata=%s",
                     self._hk_update_end_time, self._hk_retry_metadata)

    def stop(self) -> None:
        """Stop the listener."""
        if self._listener:
            self._listener.stop()
