"""Network-drive watcher: discovers new acquisition session directories.

The watcher performs a synchronous scan of one or more *watch paths*, returning
every session directory found.  The :class:`SessionManager` calls
:func:`discover_sessions` on each polling interval.

Expected directory layout::

    <watch_path>/
    └── <subject_id>/          (numeric, e.g. "842456")
        └── <session_ts>/      (YYYY-MM-DDTHH-MM-SS, e.g. "2026-03-20T20-23-05")
            ├── <run_ts>/      (YYYY-MM-DDTHH-MM-SS, e.g. "2026-03-20T20-23-37")
            │   ├── behavior/
            │   └── behavior-videos/
            └── <run_ts>/      (second run if Bonsai was restarted)

After consolidation there will be exactly one run sub-directory under the
session directory.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# Matches YYYY-MM-DDTHH-MM-SS (Bonsai session/run timestamp format)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")


def is_run_dir(path: Path) -> bool:
    """Return *True* if *path* looks like a Bonsai run directory.

    A run directory is a directory whose name matches ``YYYY-MM-DDTHH-MM-SS``
    and which contains a ``behavior/`` or ``behavior-videos/`` subdirectory,
    indicating that Bonsai has actually written data there.

    Parameters
    ----------
    path : Path
        Candidate path.
    """
    if not path.is_dir() or not _TIMESTAMP_RE.match(path.name):
        return False
    return (path / "behavior").is_dir() or (path / "behavior-videos").is_dir()


def is_session_dir(path: Path) -> bool:
    """Return *True* if *path* is a top-level session directory.

    A session directory is a directory whose name matches
    ``YYYY-MM-DDTHH-MM-SS`` and which contains at least one run
    sub-directory (a child whose name also matches the timestamp pattern and
    which has ``behavior/`` data).

    Parameters
    ----------
    path : Path
        Candidate path.
    """
    if not path.is_dir() or not _TIMESTAMP_RE.match(path.name):
        return False
    try:
        return any(is_run_dir(child) for child in path.iterdir())
    except PermissionError:
        return False


def discover_sessions(
    watch_paths: list[Path],
    allowed_subjects: set[str] | None = None,
) -> list[tuple[str, Path]]:
    """Scan *watch_paths* and return all discovered session directories.

    Each element of *watch_paths* is scanned one level deep for
    subject-ID subdirectories, then one more level for session-timestamp
    subdirectories::

        watch_path / <subject_id> / <YYYY-MM-DDTHH-MM-SS>  ← returned

    Parameters
    ----------
    watch_paths : list of Path
        Root directories to scan.
    allowed_subjects : set of str, optional
        When non-empty, only subject-ID directories whose name appears in
        this set are descended into.  An empty set or ``None`` allows every
        subject directory (no filter applied).

    Returns
    -------
    list of (subject_id, session_root) tuples
        ``subject_id`` is the name of the parent directory
        (e.g. ``"842456"``).  ``session_root`` is the full path to the
        session directory.
    """
    results: list[tuple[str, Path]] = []
    _filter = allowed_subjects or set()

    for watch_path in watch_paths:
        if not watch_path.exists():
            log.warning("Watch path does not exist (skipping): %s", watch_path)
            continue
        if not watch_path.is_dir():
            log.warning("Watch path is not a directory (skipping): %s", watch_path)
            continue

        try:
            subject_dirs = sorted(watch_path.iterdir())
        except PermissionError as exc:
            log.warning("Cannot read watch path %s: %s", watch_path, exc)
            continue

        for subject_dir in subject_dirs:
            if not subject_dir.is_dir():
                continue
            subject_id = subject_dir.name

            if _filter and subject_id not in _filter:
                log.debug("Subject %s not in allowlist — skipping.", subject_id)
                continue

            try:
                candidates = sorted(subject_dir.iterdir())
            except PermissionError as exc:
                log.warning("Cannot read subject dir %s: %s", subject_dir, exc)
                continue

            for candidate in candidates:
                if is_session_dir(candidate):
                    results.append((subject_id, candidate))
                    log.debug(
                        "Discovered session: subject=%s  ts=%s",
                        subject_id,
                        candidate.name,
                    )

    return results
