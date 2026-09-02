"""Pause-file controller for experiment conductor job submissions.

The pause mechanism uses a sentinel lock file on disk.  Any process that can
reach the same filesystem path — e.g. the ``conductor-status`` viewer running
on the same machine — can create or remove the file to pause or resume the
conductor's upload step without restarting it.

The conductor checks for the file at the start of every upload cycle, so the
change takes effect within one poll interval.  The file contains a
human-readable ISO-8601 timestamp recording when the pause was activated.

Usage
-----
From another process (or the ``conductor-status`` viewer):

.. code-block:: python

    from experiment_conductor.pause_control import pause_submissions, resume_submissions
    from pathlib import Path

    pause_file = Path("conductor_pause.lock")
    pause_submissions(pause_file)   # conductor stops submitting on next cycle
    resume_submissions(pause_file)  # conductor resumes on next cycle

Environment variable
--------------------
``CONDUCTOR_PAUSE_FILE`` — path to the sentinel file.
Default: ``conductor_pause.lock`` (relative to cwd, or next to the state file
when configured alongside ``CONDUCTOR_STATE_FILE``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

PAUSE_FILENAME = "conductor_pause.lock"


def is_paused(pause_file: Path) -> bool:
    """Return *True* when the pause sentinel file exists on disk."""
    return pause_file.exists()


def pause_submissions(pause_file: Path) -> None:
    """Create the pause sentinel file, blocking future upload submissions.

    Writes the activation timestamp into the file body so it is human-readable
    when inspected with a text editor.  Safe to call when already paused.
    """
    pause_file.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pause_file.write_text(f"paused_at={ts}\n", encoding="utf-8")


def resume_submissions(pause_file: Path) -> None:
    """Remove the pause sentinel file, allowing upload submissions to resume.

    Safe to call when not currently paused (no-op).
    """
    pause_file.unlink(missing_ok=True)


def paused_since(pause_file: Path) -> str | None:
    """Return the pause timestamp string from inside the sentinel file, or *None*.

    Returns the raw ``paused_at=…`` value if parseable, otherwise the file's
    modification time as a fallback, or *None* if the file does not exist.
    """
    if not pause_file.exists():
        return None
    try:
        text = pause_file.read_text(encoding="utf-8").strip()
        for line in text.splitlines():
            if line.startswith("paused_at="):
                return line[len("paused_at="):]
    except OSError:
        pass
    # Fallback: use file mtime
    try:
        mtime = datetime.fromtimestamp(pause_file.stat().st_mtime, tz=timezone.utc)
        return mtime.isoformat(timespec="seconds")
    except OSError:
        return None
