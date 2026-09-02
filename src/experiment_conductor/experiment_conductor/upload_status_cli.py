"""Interactive upload-status viewer for the experiment conductor.

Run as a standalone command::

    conductor-status [--env-file .env] [--state-file conductor_state.json]

Reads the conductor state file to enumerate active (and completed) sessions,
then reads each session's ``.upload_history.json`` sidecar.  Presents a
numbered menu for selecting a dataset, then a sub-menu for viewing:

1. Full chunk history
2. In-progress chunks (submitted / pending)
3. Failed / skipped chunks

The viewer is read-only: it does not interact with the running conductor.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

from .pause_control import (
    PAUSE_FILENAME,
    is_paused,
    pause_submissions,
    paused_since,
    resume_submissions,
)
from .upload_sidecar import SIDECAR_FILENAME

# ---------------------------------------------------------------------------
# ANSI colour helpers (gracefully disabled when not a tty)
# ---------------------------------------------------------------------------

_TTY: bool = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def _green(t: str) -> str: return _c("32", t)
def _yellow(t: str) -> str: return _c("33", t)
def _red(t: str) -> str: return _c("31", t)
def _bold(t: str) -> str: return _c("1", t)
def _dim(t: str) -> str: return _c("2", t)
def _cyan(t: str) -> str: return _c("36", t)


def _state_color(state: str) -> str:
    return {
        "success": _green(state),
        "submitted": _yellow(state),
        "pending": _yellow(state),
        "failed": _red(state),
        "skipped": _red(state),
    }.get(state, state)


def _delete_color(ds: str) -> str:
    return {
        "success": _green(ds),
        "disabled": _dim(ds),
        "pending": _yellow(ds),
        "failed": _red(ds),
    }.get(ds, ds)


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def _load_state_file(path: Path) -> dict:
    """Load and return the conductor state JSON, or {} on missing/error."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  Warning: could not read state file {path}: {exc}")
        return {}


def _load_sidecar(run_dir: Path) -> Optional[dict]:
    """Load the upload sidecar for *run_dir*, or None if not found."""
    p = run_dir / SIDECAR_FILENAME
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _summarize_sidecar(sidecar: Optional[dict]) -> str:
    if sidecar is None:
        return _dim("no upload history")
    chunks = sidecar.get("chunks", {})
    n = len(chunks)
    if n == 0:
        return _dim("0 chunks")
    by_state: dict[str, int] = {}
    for rec in chunks.values():
        s = rec.get("state", "?")
        by_state[s] = by_state.get(s, 0) + 1
    order = ["success", "submitted", "pending", "skipped", "failed"]
    parts = [
        f"{by_state[s]} {_state_color(s)}"
        for s in order if s in by_state
    ]
    return f"{n} chunk(s): " + ", ".join(parts)


# ---------------------------------------------------------------------------
# Chunk table renderer
# ---------------------------------------------------------------------------

_COL_TS = 26
_COL_STATE = 10
_COL_RETRY = 8
_COL_CONFIRMED = 22
_COL_DELETE = 10


def _print_chunk_table(chunks: dict) -> None:
    if not chunks:
        print(f"\n  {_dim('(none)')}\n")
        return

    header = (
        f"  {'Chunk timestamp':<{_COL_TS}}"
        f"  {'State':<{_COL_STATE}}"
        f"  {'Tries':<{_COL_RETRY}}"
        f"  {'Confirmed at':<{_COL_CONFIRMED}}"
        f"  {'Delete':<{_COL_DELETE}}"
    )
    sep = "  " + "-" * (
        _COL_TS + _COL_STATE + _COL_RETRY + _COL_CONFIRMED + _COL_DELETE + 10
    )

    print()
    print(_bold(header))
    print(sep)

    for ts in sorted(chunks):
        rec = chunks[ts]
        state = _state_color(rec.get("state", "?"))
        retries = str(rec.get("retries", 0))
        confirmed = rec.get("confirmed_at") or _dim("—")
        delete = _delete_color(rec.get("delete_state", "?"))
        # Pad with spaces compensating for invisible ANSI codes
        print(
            f"  {ts:<{_COL_TS}}"
            f"  {state:<{_COL_STATE}}"
            f"  {retries:<{_COL_RETRY}}"
            f"  {confirmed:<{_COL_CONFIRMED}}"
            f"  {delete:<{_COL_DELETE}}"
        )
        for err in rec.get("errors", []):
            print(f"    {_red('↳ ' + err)}")

    print()


# ---------------------------------------------------------------------------
# Dataset sub-menu
# ---------------------------------------------------------------------------

def _dataset_menu(
    subject_id: str,
    session_ts: str,
    run_dir: str,
    sidecar: Optional[dict],
) -> None:
    chunks: dict = sidecar.get("chunks", {}) if sidecar else {}
    delete_enabled: bool = sidecar.get("delete_enabled", False) if sidecar else False
    label = _bold(f"{subject_id}  /  {session_ts}")

    while True:
        print()
        print(f"  {'─'*60}")
        print(f"  Dataset : {label}")
        print(f"  Run dir : {_dim(run_dir)}")
        print(f"  Delete  : {'enabled' if delete_enabled else 'disabled'}")
        print(f"  {'─'*60}")
        print()
        print(f"  {_cyan('1')}. Full chunk history")
        print(f"  {_cyan('2')}. In-progress chunks  (submitted / pending)")
        print(f"  {_cyan('3')}. Failed / skipped chunks")
        print(f"  {_cyan('b')}. Back to dataset list")
        print(f"  {_cyan('q')}. Quit")
        print()

        choice = input("  Select view: ").strip().lower()

        if choice == "q":
            sys.exit(0)
        elif choice == "b":
            return
        elif choice == "1":
            print(f"\n  {_bold('Full history — all chunks:')}")
            _print_chunk_table(chunks)
        elif choice == "2":
            in_prog = {
                k: v for k, v in chunks.items()
                if v.get("state") in ("submitted", "pending")
            }
            print(f"\n  {_bold('In-progress chunks:')}")
            _print_chunk_table(in_prog)
        elif choice == "3":
            failed = {
                k: v for k, v in chunks.items()
                if v.get("state") in ("failed", "skipped")
            }
            print(f"\n  {_bold('Failed / skipped chunks:')}")
            _print_chunk_table(failed)
        else:
            print(f"  {_red('Invalid.')}  Enter 1, 2, 3, b, or q.")


# ---------------------------------------------------------------------------
# Top-level dataset selector
# ---------------------------------------------------------------------------

def _pause_status_line(pause_file) -> str:
    """Return a coloured one-line status string for the pause sentinel."""
    if is_paused(pause_file):
        since = paused_since(pause_file)
        label = f"PAUSED{f'  (since {since})' if since else ''}"
        return _red(f"⏸  Submissions {label}")
    return _green("▶  Submissions ACTIVE")


def _main_menu(sessions: list[tuple], pause_file) -> None:
    """Render the dataset list and handle selection.

    *sessions* is a list of ``(subject_id, session_ts, run_dir, sidecar)``
    tuples sorted by subject_id then session_ts.
    *pause_file* is a :class:`~pathlib.Path` to the conductor pause sentinel.
    """
    while True:
        print()
        print(_bold("╔══════════════════════════════════════════════════╗"))
        print(_bold("║   Experiment Conductor — Upload Status Viewer    ║"))
        print(_bold("╚══════════════════════════════════════════════════╝"))
        print()
        print(f"  {_pause_status_line(pause_file)}")
        print()

        if not sessions:
            print(f"  {_yellow('No sessions found in state file.')}")
            print("  Has the conductor run with CONDUCTOR_STATE_FILE set?")
            print()

        else:
            for i, (subject_id, session_ts, run_dir, sidecar) in enumerate(sessions, 1):
                summary = _summarize_sidecar(sidecar)
                print(f"  {_bold(str(i))}. {_bold(subject_id)}  {session_ts}")
                print(f"     {_dim(str(run_dir))}")
                print(f"     {summary}")
                print()

        # ── Actions ──────────────────────────────────────────────────────────
        currently_paused = is_paused(pause_file)
        if currently_paused:
            print(f"  {_cyan('r')}. Resume upload submissions  (removes pause)")
        else:
            print(f"  {_cyan('p')}. Pause upload submissions")

        if sessions:
            print(f"  {_cyan('1')}–{_cyan(str(len(sessions)))}. View dataset details")
        print(f"  {_cyan('q')}. Quit")
        print()

        choice = input("  Select: ").strip().lower()

        if choice == "q":
            return

        # ── Pause / resume ────────────────────────────────────────────────────
        if choice == "p":
            if currently_paused:
                print(f"  {_yellow('Already paused.')}  Use r to resume.")
            else:
                try:
                    pause_submissions(pause_file)
                    print(
                        f"\n  {_red('⏸  Upload submissions PAUSED.')}  "
                        f"Pause file: {_dim(str(pause_file))}\n"
                        "  The conductor will skip upload cycles until you resume.\n"
                    )
                except OSError as exc:
                    print(f"  {_red(f'Could not create pause file: {exc}')}")
            continue

        if choice == "r":
            if not currently_paused:
                print(f"  {_yellow('Not currently paused.')}  Submissions are already active.")
            else:
                try:
                    resume_submissions(pause_file)
                    print(
                        f"\n  {_green('▶  Upload submissions RESUMED.')}  "
                        "The conductor will submit chunks on its next cycle.\n"
                    )
                except OSError as exc:
                    print(f"  {_red(f'Could not remove pause file: {exc}')}")
            continue

        # ── Dataset selection ─────────────────────────────────────────────────
        if not sessions:
            print(f"  {_red('No sessions to select.')}")
            continue

        try:
            idx = int(choice) - 1
        except ValueError:
            opts = f"1–{len(sessions)}, p, r, q" if not currently_paused else f"1–{len(sessions)}, r, q"
            print(f"  {_red('Invalid.')}  Enter {opts}.")
            continue

        if not (0 <= idx < len(sessions)):
            print(f"  {_red('Out of range.')}  Enter 1–{len(sessions)}.")
            continue

        subject_id, session_ts, run_dir, sidecar = sessions[idx]
        _dataset_menu(subject_id, session_ts, run_dir, sidecar)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the ``conductor-status`` command."""
    import argparse

    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(
        prog="conductor-status",
        description=(
            "Interactive upload-status viewer for the experiment conductor.  "
            "Reads the conductor state file to enumerate sessions and displays "
            "per-chunk upload history from each session's .upload_history.json sidecar."
        ),
    )
    parser.add_argument(
        "--env-file", default=".env", metavar="PATH",
        help="Path to the .env file (default: .env in cwd).",
    )
    parser.add_argument(
        "--state-file", default=None, metavar="PATH",
        help=(
            "Path to the conductor state JSON file.  "
            "Overrides CONDUCTOR_STATE_FILE from the env file."
        ),
    )
    parser.add_argument(
        "--pause-file", default=None, metavar="PATH",
        help=(
            "Path to the upload-pause sentinel file.  "
            "Overrides CONDUCTOR_PAUSE_FILE from the env file.  "
            f"Default: {PAUSE_FILENAME} (next to the state file when possible)."
        ),
    )
    args = parser.parse_args()

    load_dotenv(args.env_file, override=False)

    state_file_path = Path(
        args.state_file
        or os.getenv("CONDUCTOR_STATE_FILE")
        or "conductor_state.json"
    )

    # Resolve pause file: CLI > env > next to state file > cwd
    pause_file_path = Path(
        args.pause_file
        or os.getenv("CONDUCTOR_PAUSE_FILE")
        or str(state_file_path.parent / PAUSE_FILENAME)
    )

    print(f"\n  Reading state from : {state_file_path}")
    print(f"  Pause file         : {pause_file_path}")
    state_data = _load_state_file(state_file_path)

    sessions: list[tuple] = []
    if state_data:
        for _key, session_dict in state_data.items():
            subject_id = session_dict.get("subject_id", "unknown")
            session_ts = session_dict.get("session_datetime", "unknown")
            run_dir_raw = session_dict.get("run_dir") or _key
            run_dir = Path(run_dir_raw) if run_dir_raw else None
            sidecar = _load_sidecar(run_dir) if run_dir else None
            sessions.append((subject_id, session_ts, str(run_dir_raw), sidecar))
        sessions.sort(key=lambda t: (t[0], t[1]))
    else:
        print(
            f"\n  {_yellow('No sessions found in state file:')} {state_file_path}\n"
            "  Pause / resume controls are still available below.\n"
            "  Start the conductor with CONDUCTOR_STATE_FILE configured to see sessions."
        )

    _main_menu(sessions, pause_file_path)
