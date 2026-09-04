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
import re
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


_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")


def _find_sidecar_path(directory: Path) -> Optional[Path]:
    """Return the path to ``.upload_history.json``, searching subdirs if needed.

    Checks *directory* directly first, then looks for the sidecar inside the
    earliest timestamp-named sub-directory (the consolidated run dir).  This
    handles the case where ``run_dir`` in the state file is ``None`` and the
    session root is used as a fallback.
    """
    # 1. Direct hit (run_dir is the actual run sub-directory)
    candidate = directory / SIDECAR_FILENAME
    if candidate.exists():
        return candidate

    # 2. Look inside timestamp-named children (session root was passed)
    try:
        ts_subdirs = sorted(
            d for d in directory.iterdir()
            if d.is_dir() and _TS_RE.fullmatch(d.name)
        )
    except OSError:
        return None

    for sub in ts_subdirs:
        candidate = sub / SIDECAR_FILENAME
        if candidate.exists():
            return candidate

    return None


def _load_sidecar(run_dir: Path) -> Optional[dict]:
    """Load the upload sidecar for *run_dir* (or its earliest run sub-dir)."""
    p = _find_sidecar_path(run_dir)
    if p is None:
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


def _fetch_transfer_jobs(subject_id: str, session_ts: str, run_dir: str) -> list[dict]:
    """Query the transfer service for *session*'s jobs; returns [] on any error."""
    from datetime import datetime, timezone

    try:
        from .uploader_bridge import compute_s3_prefix, query_chronic_ephys_job_statuses

        s3_bucket = os.getenv("CONDUCTOR_S3_BUCKET", "aind-open-data")
        transfer_endpoint = os.getenv(
            "TRANSFER_SERVICE_ENDPOINT",
            "http://aind-data-transfer-service/api/v2/submit_jobs",
        )
        acq_dt = datetime.strptime(session_ts, "%Y-%m-%dT%H-%M-%S").replace(
            tzinfo=timezone.utc
        )
        s3_prefix = compute_s3_prefix(
            source_directory=run_dir,
            subject_id=subject_id,
            acq_datetime=acq_dt,
            s3_bucket=s3_bucket,
        )
        if s3_prefix is None:
            return []
        return query_chronic_ephys_job_statuses(s3_prefix, transfer_endpoint)
    except Exception:
        return []


def _compact_transfer_summary(jobs: list[dict]) -> str:
    """Return a compact one-line transfer-service status for the dataset list."""
    if not jobs:
        return _dim("transfer: no recent jobs")

    _ts_color: dict = {
        "success": _green,
        "failed":  _red,
        "running": _yellow,
        "queued":  _yellow,
    }

    start_jobs = [j for j in jobs if j.get("job_type") == "chronic_ephys_start"]
    chunk_jobs = [j for j in jobs if j.get("job_type") == "chronic_ephys_chunk"]

    parts: list[str] = []

    if start_jobs:
        state = start_jobs[0].get("job_state", "?")
        cf = _ts_color.get(state, lambda x: x)
        parts.append(f"start: {cf(state)}")

    if chunk_jobs:
        by_state: dict[str, int] = {}
        for j in chunk_jobs:
            s = j.get("job_state", "?")
            by_state[s] = by_state.get(s, 0) + 1
        chunk_parts: list[str] = []
        for state in ("running", "queued", "success", "failed"):
            if state in by_state:
                cf = _ts_color.get(state, lambda x: x)
                chunk_parts.append(f"{by_state[state]} {cf(state)}")
        parts.append(f"chunks: {', '.join(chunk_parts)}")

    return "transfer: " + "  ·  ".join(parts) if parts else _dim("transfer: no jobs")


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

def _reset_upload_state(
    run_dir_str: str,
    subject_id: str,
    session_ts: str,
    state_file_path: Path,
    pid_file: Path,
) -> None:
    """Delete the sidecar and (when the conductor is stopped) patch state JSON.

    Prints a status message and returns.  Does NOT exit — the caller loops back
    to the dataset menu so the user can see the result.
    """
    run_dir = Path(run_dir_str)
    conductor_running = pid_file.exists()

    # ── 1. Locate and delete the sidecar ─────────────────────────────────────
    # The sidecar lives in the earliest run sub-directory, which may differ
    # from run_dir when the state file stores the session root as a fallback.
    sidecar_path = _find_sidecar_path(run_dir)
    if sidecar_path is not None:
        try:
            sidecar_path.unlink()
            print(f"\n  {_green('✔  Sidecar deleted:')} {_dim(str(sidecar_path))}")
        except OSError as exc:
            print(f"\n  {_red(f'✖  Could not delete sidecar: {exc}')}\n")
            return
    else:
        print(f"\n  {_yellow('Sidecar not found')} (already clean) under: {_dim(run_dir_str)}")

    # ── 2. Patch the state file (only safe when conductor is stopped) ─────────
    session_key = run_dir_str  # key used in conductor_state.json
    if conductor_running:
        print(
            f"\n  {_yellow('⚠  Conductor is running.')}  The sidecar has been cleared.\n"
            "  The conductor will detect that the start job is not in DocDB and\n"
            "  automatically re-submit the start job on the next 1–2 cadence cycles.\n"
            "  No further action needed.\n"
        )
    else:
        # Conductor is stopped — safe to patch the state file directly.
        try:
            state_data = json.loads(state_file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"\n  {_yellow(f'Could not read state file ({exc}) — skipping state patch.')}")
            print("  Restart the conductor; upload will reset on its first cycle.\n")
            return

        # Try exact key first; fall back to a partial-path match in case the
        # drive letter or slash style differs, OR because the state file is
        # keyed by data_root while session_key may be the run sub-directory
        # (a path that starts with data_root + a timestamp suffix).
        target_key = None
        if session_key in state_data:
            target_key = session_key
        else:
            for k in state_data:
                k_norm = k.replace("\\", "/").rstrip("/").lower()
                s_norm = session_key.replace("\\", "/").rstrip("/").lower()
                if (
                    k_norm == s_norm
                    or s_norm.startswith(k_norm + "/")   # run_dir starts with data_root
                    or k_norm.startswith(s_norm + "/")   # data_root starts with run_dir (unusual)
                    or k_norm.endswith(s_norm)
                    or s_norm.endswith(k_norm)
                ):
                    target_key = k
                    break

        if target_key is None:
            print(
                f"\n  {_yellow('Session key not found in state file.')}  "
                "Restart the conductor; upload will reset on its first cycle.\n"
            )
            return

        state_data[target_key]["upload_started"] = False
        state_data[target_key]["last_upload_run"] = None
        # Clear the in-memory phase back to idle so the conductor picks it up
        if state_data[target_key].get("phase") == "error":
            state_data[target_key]["phase"] = "idle"
            state_data[target_key]["consecutive_errors"] = 0
            state_data[target_key]["error_message"] = None

        try:
            state_file_path.write_text(
                json.dumps(state_data, indent=2), encoding="utf-8"
            )
            print(
                f"\n  {_green('✔  State file patched:')} upload_started → false\n"
                "  Restart the conductor; it will re-submit the start job on its\n"
                "  first cycle.\n"
            )
        except OSError as exc:
            print(f"\n  {_red(f'✖  Could not write state file: {exc}')}\n")


def _show_transfer_job_status(
    subject_id: str, session_ts: str, run_dir: str
) -> None:
    """Query the transfer service and print recent job statuses for this dataset."""
    from datetime import datetime, timezone

    print(f"\n  {_bold('Transfer service — recent jobs for this dataset:')}")
    try:
        from .uploader_bridge import compute_s3_prefix, query_chronic_ephys_job_statuses

        s3_bucket = os.getenv("CONDUCTOR_S3_BUCKET", "aind-open-data")
        transfer_endpoint = os.getenv(
            "TRANSFER_SERVICE_ENDPOINT",
            "http://aind-data-transfer-service/api/v2/submit_jobs",
        )
        acq_dt = datetime.strptime(session_ts, "%Y-%m-%dT%H-%M-%S").replace(
            tzinfo=timezone.utc
        )
        s3_prefix = compute_s3_prefix(
            source_directory=str(run_dir),
            subject_id=subject_id,
            acq_datetime=acq_dt,
            s3_bucket=s3_bucket,
        )
        if s3_prefix is None:
            print(
                f"  {_yellow('Could not compute S3 prefix.')}  "
                "Check CONDUCTOR_S3_BUCKET in your .env file.\n"
            )
            return

        print(f"  S3 prefix : {_dim(s3_prefix)}")
        print(f"  Endpoint  : {_dim(transfer_endpoint)}\n")

        jobs = query_chronic_ephys_job_statuses(s3_prefix, transfer_endpoint)
        if not jobs:
            print(
                f"  {_yellow('No jobs found in transfer service history.')}\n"
                "  Jobs older than 2 weeks are not returned by the service.\n"
            )
            return

        # ── Table ────────────────────────────────────────────────────────────
        _CW_TYPE  = 24
        _CW_STATE = 10
        _CW_SUB   = 22
        _CW_END   = 22
        header = (
            f"  {'Job type':<{_CW_TYPE}}"
            f"  {'State':<{_CW_STATE}}"
            f"  {'Submitted':<{_CW_SUB}}"
            f"  {'Ended':<{_CW_END}}"
        )
        sep = "  " + "-" * (_CW_TYPE + _CW_STATE + _CW_SUB + _CW_END + 8)
        print(_bold(header))
        print(sep)

        _state_color_ts = {
            "success": _green,
            "failed":  _red,
            "running": _yellow,
            "queued":  _yellow,
        }

        for j in jobs:
            jtype  = (j.get("job_type") or "unknown")[:_CW_TYPE]
            jstate = j.get("job_state") or "unknown"
            color  = _state_color_ts.get(jstate, lambda x: x)
            jsub   = (j.get("submit_time") or "—")[:_CW_SUB]
            jend   = (j.get("end_time") or "—")[:_CW_END]
            print(
                f"  {jtype:<{_CW_TYPE}}"
                f"  {color(jstate):<{_CW_STATE}}"
                f"  {jsub:<{_CW_SUB}}"
                f"  {jend:<{_CW_END}}"
            )
        print()

        # ── Highlight failed start jobs ───────────────────────────────────────
        failed_starts = [
            j for j in jobs
            if j.get("job_type") == "chronic_ephys_start"
            and j.get("job_state") == "failed"
        ]
        if failed_starts:
            print(
                f"  {_red(_bold('⚠  Start job failed.'))}  "
                "Use option 4 to reset upload state and retry.\n"
            )
    except Exception as exc:
        print(f"  {_red(f'Error querying transfer service: {exc}')}\n")


def _dataset_menu(
    subject_id: str,
    session_ts: str,
    run_dir: str,
    sidecar: Optional[dict],
    phase: str = "unknown",
    error_message: Optional[str] = None,
    state_file_path: Optional[Path] = None,
    pid_file: Optional[Path] = None,
) -> None:
    chunks: dict = sidecar.get("chunks", {}) if sidecar else {}
    delete_enabled: bool = sidecar.get("delete_enabled", False) if sidecar else False
    label = _bold(f"{subject_id}  /  {session_ts}")

    while True:
        print()
        print(f"  {'─'*60}")
        print(f"  Dataset : {label}")
        print(f"  Run dir : {_dim(run_dir)}")
        print(f"  Phase   : {_red('ERROR') if phase == 'error' else phase}")
        if error_message:
            print(f"  Error   : {_red(error_message)}")
        print(f"  Delete  : {'enabled' if delete_enabled else 'disabled'}")
        print(f"  {'─'*60}")
        _show_transfer_job_status(subject_id, session_ts, run_dir)
        print(f"  {_cyan('1')}. Full chunk history")
        print(f"  {_cyan('2')}. In-progress chunks  (submitted / pending)")
        print(f"  {_cyan('3')}. Failed / skipped chunks")
        print(f"  {_cyan('4')}. Reset upload state  {_red('(clears sidecar / restarts from start job)')}")
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
        elif choice == "4":
            print()
            print(f"  {_red(_bold('Reset upload state'))}")
            print(f"  This will delete the sidecar (.upload_history.json) for:")
            print(f"    {_dim(run_dir)}")
            print(f"  The conductor will re-submit the start job from scratch.")
            print()
            confirm = input(
                f"  {_yellow('Are you sure?')}  [y/N] "
            ).strip().lower()
            if confirm == "y":
                _reset_upload_state(
                    run_dir_str=run_dir,
                    subject_id=subject_id,
                    session_ts=session_ts,
                    state_file_path=state_file_path or Path("conductor_state.json"),
                    pid_file=pid_file or Path("conductor.pid"),
                )
                # Re-load sidecar so the view reflects the cleared state
                chunks = {}
                sidecar = None
            else:
                print("  Cancelled.\n")
        else:
            print(f"  {_red('Invalid.')}  Enter 1, 2, 3, 4, b, or q.")


# ---------------------------------------------------------------------------
# Top-level dataset selector
# ---------------------------------------------------------------------------

def _force_quit_conductor(pid_file: Path) -> None:
    """Read *pid_file* and send a forceful kill signal to the conductor process.

    On Windows this calls ``taskkill /F /PID <pid>``.
    On other platforms it sends ``SIGKILL`` via :func:`os.kill`.

    Prints a status message and returns — callers should loop back to the menu.
    """
    import signal
    import subprocess

    if not pid_file.exists():
        print(
            f"\n  {_yellow('PID file not found:')} {pid_file}\n"
            "  The conductor may not be running, or it was started without a PID file.\n"
        )
        return

    try:
        pid_text = pid_file.read_text(encoding="utf-8").strip()
        pid = int(pid_text)
    except (OSError, ValueError) as exc:
        print(f"\n  {_red(f'Could not read PID file ({pid_file}): {exc}')}\n")
        return

    print(f"\n  Force-quitting conductor (PID {pid}) …")
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print(f"  {_green(f'✔  Process {pid} terminated.')}\n")
            else:
                # taskkill prints its own error to stdout
                msg = (result.stdout or result.stderr or "unknown error").strip()
                print(f"  {_red(f'taskkill failed: {msg}')}\n")
        else:
            os.kill(pid, signal.SIGKILL)
            print(f"  {_green(f'✔  SIGKILL sent to process {pid}.')}\n")
    except (ProcessLookupError, PermissionError) as exc:
        print(f"  {_red(f'Could not kill process {pid}: {exc}')}\n")


def _pause_status_line(pause_file) -> str:
    """Return a coloured one-line status string for the pause sentinel."""
    if is_paused(pause_file):
        since = paused_since(pause_file)
        label = f"PAUSED{f'  (since {since})' if since else ''}"
        return _red(f"⏸  Submissions {label}")
    return _green("▶  Submissions ACTIVE")


# ---------------------------------------------------------------------------
# Auto-refresh helpers
# ---------------------------------------------------------------------------

_DEFAULT_REFRESH_S: float = 30.0


def _clear_screen() -> None:
    """Clear the terminal screen when stdout is a TTY."""
    if not _TTY:
        return
    if sys.platform == "win32":
        os.system("cls")
    else:
        os.system("clear")


_PROMPT_BASE = "  Select"


def _wait_for_input(timeout_s: float) -> str | None:
    """Wait up to *timeout_s* seconds for a single-line selection, or return ``None``.

    Displays a live countdown that rewrites itself in place each second so the
    user can see exactly when the next auto-refresh will fire.  The format is::

        Select (refresh in 28s): <typed>

    Uses non-blocking I/O:

    * **Windows** — polls :mod:`msvcrt` every 50 ms and redraws the prompt line
      with ``\\r`` once per second.
    * **Unix** — loops :func:`select.select` with a 1-second ceiling so the
      countdown updates at the same cadence.

    Pressing **Ctrl+C** raises :exc:`KeyboardInterrupt` on both platforms.
    """
    import time

    def _prompt(secs_left: int) -> str:
        return f"\r{_PROMPT_BASE} (refresh in {secs_left:2d}s): "

    if sys.platform == "win32":
        import msvcrt

        buf = ""
        deadline = time.monotonic() + timeout_s
        last_secs = -1

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Erase the prompt line and signal timeout
                print(f"\r{' ' * 60}\r", end="", flush=True)
                return None

            secs_left = max(1, int(remaining) + 1)
            if secs_left != last_secs:
                last_secs = secs_left
                # Overwrite the line: prompt + current buffer
                line = _prompt(secs_left) + buf
                print(f"{line}{' ' * 4}", end="", flush=True)  # trailing spaces erase old chars
                # Reposition cursor at end of buffer (after the trailing spaces)
                print(f"\r{line}", end="", flush=True)

            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    print()
                    return buf.strip().lower()
                elif ch == "\x08":  # backspace
                    if buf:
                        buf = buf[:-1]
                        last_secs = -1  # force prompt redraw
                elif ch == "\x03":  # Ctrl+C
                    print()
                    raise KeyboardInterrupt
                elif ch in ("\x00", "\xe0"):  # special-key prefix — discard next byte
                    msvcrt.getwch()
                else:
                    buf += ch
                    last_secs = -1  # force prompt redraw

            time.sleep(0.05)

    else:
        import select as _select

        buf = ""
        deadline = time.monotonic() + timeout_s
        last_secs = -1

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(f"\r{' ' * 60}\r", end="", flush=True)
                return None

            secs_left = max(1, int(remaining) + 1)
            if secs_left != last_secs:
                last_secs = secs_left
                print(_prompt(secs_left), end="", flush=True)

            # Wait up to 1 s so we can update the countdown each second
            ready, _, _ = _select.select([sys.stdin], [], [], min(1.0, remaining))
            if ready:
                line = sys.stdin.readline()
                print()
                return line.strip().lower()

        return None  # unreachable but satisfies type checkers


def _load_sessions(state_file_path: Path) -> list[tuple]:
    """Read *state_file_path* and return a sorted list of session tuples.

    Each tuple is
    ``(subject_id, session_ts, run_dir_str, sidecar_dict_or_None, phase_str, error_message_or_None)``.
    """
    state_data = _load_state_file(state_file_path)
    sessions: list[tuple] = []
    if state_data:
        for _key, session_dict in state_data.items():
            subject_id = session_dict.get("subject_id", "unknown")
            session_ts = session_dict.get("session_datetime", "unknown")
            run_dir_raw = session_dict.get("run_dir") or _key
            run_dir = Path(run_dir_raw) if run_dir_raw else None
            sidecar = _load_sidecar(run_dir) if run_dir else None
            phase = session_dict.get("phase", "unknown")
            error_message = session_dict.get("error_message")
            sessions.append((subject_id, session_ts, str(run_dir_raw), sidecar, phase, error_message))
        sessions.sort(key=lambda t: (t[0], t[1]))
    return sessions


# ---------------------------------------------------------------------------
# Top-level dataset selector
# ---------------------------------------------------------------------------

def _main_menu(
    state_file_path: Path,
    pause_file: Path,
    refresh_s: float = _DEFAULT_REFRESH_S,
) -> None:
    """Auto-refreshing dataset list and action menu.

    Reloads the conductor state file and all upload sidecars on every cycle
    (either after *refresh_s* seconds of inactivity, or immediately after the
    user completes an action).  The screen is cleared before each redraw when
    stdout is a TTY.

    Parameters
    ----------
    state_file_path:
        Path to ``conductor_state.json``.
    pause_file:
        Path to the upload-pause sentinel file.  The PID file is expected at
        ``pause_file.parent / "conductor.pid"``.
    refresh_s:
        Auto-refresh interval in seconds (default 30).
    """
    import datetime

    pid_file = pause_file.parent / "conductor.pid"

    while True:
        # ── Reload fresh data ─────────────────────────────────────────────────
        sessions = _load_sessions(state_file_path)
        # Best-effort transfer-service query for each session (errors → [])
        transfer_jobs: list[list[dict]] = [
            _fetch_transfer_jobs(sid, sts, rd)
            for sid, sts, rd, *_ in sessions
        ]
        currently_paused = is_paused(pause_file)
        conductor_running = pid_file.exists()
        updated_at = datetime.datetime.now().strftime("%H:%M:%S")

        # ── Render ───────────────────────────────────────────────────────────
        _clear_screen()
        print()
        print(_bold("╔══════════════════════════════════════════════════╗"))
        print(_bold("║   Experiment Conductor — Upload Status Viewer    ║"))
        print(_bold("╚══════════════════════════════════════════════════╝"))
        print(
            f"  {_dim(f'Last updated: {updated_at}  ·  '
                      f'Auto-refresh: {int(refresh_s)}s  ·  '
                      f'State: {state_file_path}')}"
        )
        print()
        print(f"  {_pause_status_line(pause_file)}")
        print()

        if not sessions:
            print(f"  {_yellow('No sessions found in state file.')}")
            print("  Has the conductor run with CONDUCTOR_STATE_FILE set?")
            print()
        else:
            for i, (subject_id, session_ts, run_dir, sidecar, phase, error_message) in enumerate(sessions, 1):
                # Phase badge
                if phase == "error":
                    phase_badge = _red("⚠ ERROR")
                elif phase in ("idle", "discovered"):
                    phase_badge = _dim(phase)
                else:
                    phase_badge = _yellow(phase)

                sidecar_summary = _summarize_sidecar(sidecar)
                ts_summary = _compact_transfer_summary(transfer_jobs[i - 1])
                print(f"  {_bold(str(i))}. {_bold(subject_id)}  {session_ts}  {phase_badge}")
                print(f"     {_dim(str(run_dir))}")
                print(f"     {sidecar_summary}")
                print(f"     {ts_summary}")
                if error_message:
                    # Truncate very long messages to keep the list readable
                    msg = error_message if len(error_message) <= 120 else error_message[:117] + "…"
                    print(f"     {_red('✖ ' + msg)}")
                print()

        # ── Actions ──────────────────────────────────────────────────────────
        if currently_paused:
            print(f"  {_cyan('r')}. Resume upload submissions  (removes pause)")
        else:
            print(f"  {_cyan('p')}. Pause upload submissions")

        if conductor_running:
            print(f"  {_cyan('k')}. Force quit conductor  {_dim(f'(PID file: {pid_file})')}")
        if sessions:
            print(f"  {_cyan('1')}–{_cyan(str(len(sessions)))}. View dataset details")
        print(f"  {_cyan('q')}. Quit")
        print()

        # ── Wait for input (with auto-refresh countdown) ─────────────────────
        choice = _wait_for_input(refresh_s)

        if choice is None:
            # Timeout — loop back to reload and redraw
            continue

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

        # ── Force quit ────────────────────────────────────────────────────────
        if choice == "k":
            if not conductor_running:
                print(
                    f"\n  {_yellow('No PID file found at')} {pid_file}\n"
                    "  The conductor does not appear to be running.\n"
                )
            else:
                confirm = input(
                    f"  {_red('Force-quit the conductor immediately?')}  "
                    "All in-flight uploads will be abandoned.  [y/N] "
                ).strip().lower()
                if confirm == "y":
                    _force_quit_conductor(pid_file)
                else:
                    print("  Cancelled.\n")
            continue

        # ── Dataset selection ─────────────────────────────────────────────────
        if not sessions:
            print(f"  {_red('No sessions to select.')}")
            continue

        try:
            idx = int(choice) - 1
        except ValueError:
            parts = []
            if sessions:
                parts.append(f"1–{len(sessions)}")
            parts.append("r" if currently_paused else "p")
            if conductor_running:
                parts.append("k")
            parts.append("q")
            print(f"  {_red('Invalid.')}  Enter {', '.join(parts)}.")
            continue

        if not (0 <= idx < len(sessions)):
            print(f"  {_red('Out of range.')}  Enter 1–{len(sessions)}.")
            continue

        subject_id, session_ts, run_dir, sidecar, phase, error_message = sessions[idx]
        _dataset_menu(
            subject_id, session_ts, run_dir, sidecar, phase, error_message,
            state_file_path=state_file_path,
            pid_file=pid_file,
        )
        # Immediately redraw after returning from the sub-menu


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
            "per-chunk upload history from each session's .upload_history.json sidecar.  "
            "The display auto-refreshes every REFRESH seconds."
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
    parser.add_argument(
        "--refresh", default=None, type=float, metavar="SECONDS",
        help=(
            f"Auto-refresh interval in seconds (default: {int(_DEFAULT_REFRESH_S)}).  "
            "The display reloads all state and sidecar files at this cadence.  "
            "Overrides CONDUCTOR_STATUS_REFRESH from the env file."
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

    # Resolve refresh interval: CLI > env > default
    _env_refresh = os.getenv("CONDUCTOR_STATUS_REFRESH")
    try:
        refresh_s = float(args.refresh if args.refresh is not None else (_env_refresh or _DEFAULT_REFRESH_S))
    except ValueError:
        print(f"  Warning: CONDUCTOR_STATUS_REFRESH='{_env_refresh}' is not a number — using default {int(_DEFAULT_REFRESH_S)}s.")
        refresh_s = _DEFAULT_REFRESH_S

    try:
        _main_menu(state_file_path, pause_file_path, refresh_s=refresh_s)
    except KeyboardInterrupt:
        print("\n  Exiting conductor-status.")
        sys.exit(0)



if __name__ == "__main__":
    main()
