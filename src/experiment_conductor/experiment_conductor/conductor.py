"""Experiment conductor — main orchestration logic.

Lifecycle
---------
1. LAUNCHING  — spawn the launcher subprocess; wait for Bonsai to appear.
2. RUNNING    — periodic cadence cycles (pipeline -> metadata -> upload).
3. ENDING     — user signals end; update acquisition end time; final cycle.
4. DONE       — cleanup.

Each cadence cycle:
  a. Run delphi-data pipeline (consolidate + build-dataset + snapshot).
     For pirouette-only experiments: runs consolidation only (no build/snapshot).
  b. After first consolidation: move Delphi metadata files.
     Skipped for pirouette-only experiments (no Delphi controller data).
  c. After metadata moved (once): generate AIND metadata.
  d. If AIND metadata exists + >=3 local chunks: submit upload job.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psutil

from experiment_conductor.config import ConductorConfig, build_config
from experiment_conductor.hotkeys import HotkeyListener
from experiment_conductor.metadata_bridge import (
    generate_metadata,
    update_acquisition_end_time,
    verify_probe_json,
)
from experiment_conductor.pipeline_bridge import move_delphi_metadata, run_consolidation, run_pipeline
from experiment_conductor.state import ConductorState, Phase
from experiment_conductor.uploader_bridge import (
    delete_local_files_after_upload,
    run_upload_cycle,
    stop_upload,
    toggle_upload_pause,
)

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

_CYCLE_LOCK = threading.Lock()  # prevents overlapping cadence cycles

_SESSION_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")


def _normalize_session_datetime(raw: str) -> str:
    """Convert a session datetime string to the ``YYYY-MM-DDTHH-MM-SS`` directory format.

    Accepts both the AIND/launcher ISO format (``2024-06-01T12:00:00Z`` or
    ``2024-06-01T12:00:00``) and the already-normalised directory format
    (``2024-06-01T12-00-00``).  The time portion's colons are replaced with
    hyphens and any trailing timezone suffix (``Z`` or ``+HH:MM``) is stripped.

    Parameters
    ----------
    raw : str
        Raw session datetime string.

    Returns
    -------
    str
        Normalised string matching ``YYYY-MM-DDTHH-MM-SS``.

    Examples
    --------
    >>> _normalize_session_datetime("2024-06-01T12:00:00Z")
    '2024-06-01T12-00-00'
    >>> _normalize_session_datetime("2026-03-20T20-23-05")
    '2026-03-20T20-23-05'
    """
    # Strip trailing Z or +offset
    raw = re.sub(r"(Z|[+-]\d{2}:\d{2})$", "", raw.strip())
    # Split on T, normalise time colons → hyphens
    if "T" in raw:
        date_part, time_part = raw.split("T", 1)
        time_part = time_part.replace(":", "-")
        return f"{date_part}T{time_part}"
    return raw


def _resolve_data_root(
    cfg: ConductorConfig,
    poll_interval_s: float = 30.0,
    timeout_s: float = 3600.0,
) -> Path:
    """Compute and return the session directory on the local server.

    When ``cfg.session_datetime`` is provided, normalises the string to the
    ``YYYY-MM-DDTHH-MM-SS`` directory format (converting from AIND ISO format
    if necessary) and returns ``server_root / subject_id / session_datetime``
    immediately — the directory must already exist or be created by robocopy.

    When ``session_datetime`` is ``None``, polls
    ``server_root / subject_id /`` every ``poll_interval_s`` seconds until at
    least one ``YYYY-MM-DDTHH-MM-SS`` directory appears, then returns the
    newest one.  Robocopy may take up to an hour to first mirror data from the
    acquisition computer; the default timeout is 3600 s (1 hour).

    Parameters
    ----------
    cfg : ConductorConfig
        Conductor configuration with ``server_root`` and ``subject_id`` set.
    poll_interval_s : float
        Seconds between directory-scan retries (default 30).
    timeout_s : float
        Maximum seconds to wait for the session directory to appear (default 3600).

    Returns
    -------
    Path
        Resolved session directory path on the server.

    Raises
    ------
    TimeoutError
        If no session directory appears within ``timeout_s`` seconds.
    """
    subject_dir = cfg.server_root / cfg.subject_id

    if cfg.session_datetime:
        normalised = _normalize_session_datetime(cfg.session_datetime)
        resolved = subject_dir / normalised
        log.info("data_root (explicit session_datetime '%s'): %s", normalised, resolved)
        return resolved

    log.info(
        "Waiting for session directory under %s "
        "(polling every %.0fs, timeout %.0fs / ~%.0f min) ...",
        subject_dir, poll_interval_s, timeout_s, timeout_s / 60,
    )
    deadline = time.monotonic() + timeout_s
    while True:
        if subject_dir.is_dir():
            candidates = sorted(
                d for d in subject_dir.iterdir()
                if d.is_dir() and _SESSION_DIR_RE.match(d.name)
            )
            if candidates:
                resolved = candidates[-1]  # newest lexicographically == chronologically
                log.info("data_root (auto-detected): %s", resolved)
                return resolved

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"No session directory found under {subject_dir} after {timeout_s:.0f}s. "
                "Check that robocopy is running and SERVER_ROOT / SUBJECT_ID are correct."
            )
        log.info(
            "No session directory yet — retrying in %.0fs (%.0f min remaining) ...",
            poll_interval_s, remaining / 60,
        )
        time.sleep(poll_interval_s)


def _bonsai_running() -> bool:
    """Return True if any Bonsai.exe process is alive."""
    for proc in psutil.process_iter(["name"]):
        try:
            if "bonsai" in (proc.info["name"] or "").lower():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def _count_local_chunks(data_root: Path, folder: str = "behavior-videos/TopCamera") -> int:
    """Count timestamp-named directories (chunks) in the camera folder."""
    chunk_re = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}")
    target = data_root / folder
    if not target.exists():
        return 0
    return sum(1 for d in target.iterdir() if d.is_dir() and chunk_re.fullmatch(d.name))


def _get_session_start_time(data_root: Path) -> datetime:
    """Return the UTC creation time of the earliest file found in *data_root*.

    On Windows ``st_ctime`` is the file creation time.  Falls back to
    ``datetime.now(UTC)`` if the directory is empty or unreadable.
    """
    earliest: Optional[float] = None
    try:
        for f in data_root.rglob("*"):
            if f.is_file():
                t = f.stat().st_ctime
                if earliest is None or t < earliest:
                    earliest = t
    except Exception as exc:
        log.warning("Could not scan data_root for start time: %s", exc)
    if earliest is not None:
        return datetime.fromtimestamp(earliest, tz=timezone.utc)
    log.warning("No files found in data_root; using current time as start_time.")
    return datetime.now(timezone.utc)


def _resolve_run_dir(session_root: Path) -> Path:
    """Return the earliest timestamp-named run sub-directory inside *session_root*.

    If *session_root* contains no timestamp-named sub-directories it is returned
    unchanged — it is already a run directory.  This is called after every
    pipeline/consolidation step so that metadata, upload, and chunk-counting
    always target the canonical run directory rather than the session root.

    Parameters
    ----------
    session_root : Path
        Session root (or an already-resolved run directory).

    Returns
    -------
    Path
        Earliest run sub-directory, or *session_root* if none exist.
    """
    from delphi_data.curation import collect_run_dirs, find_earliest_run
    run_dirs = collect_run_dirs(str(session_root))
    if run_dirs:
        return Path(find_earliest_run(run_dirs))
    return session_root


def _metadata_ready(data_root: Path) -> bool:
    """Return True if all four AIND metadata files exist."""
    meta_dir = data_root / "metadata"
    required = ["subject.json", "acquisition.json", "procedures.json", "instrument.json"]
    return all((meta_dir / f).exists() for f in required)


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1: Launch
# ──────────────────────────────────────────────────────────────────────────────

def launch_experiments(cfg: ConductorConfig, state: ConductorState) -> None:
    """Spawn the launcher subprocess and wait for Bonsai to appear.

    Parameters
    ----------
    cfg : ConductorConfig
        Conductor configuration.
    state : ConductorState
        Shared conductor state (updated to RUNNING on success).
    """
    log.info("=== PHASE: LAUNCHING ===")
    log.info(
        "Starting launcher with profile '%s' ...", cfg.experiment_config
    )
    cmd = [
        sys.executable, "-m", "launcher.launcher",
        "--experiment", cfg.experiment_config,
    ]
    # Run launcher interactively (inherits stdin/stdout/stderr)
    proc = subprocess.Popen(cmd, cwd=str(cfg.launcher_dir))
    proc.wait()  # block until the user exits the launcher control menu

    log.info("Launcher exited. Checking for running Bonsai processes ...")
    if not _bonsai_running():
        log.warning("No Bonsai process detected after launcher exit. Proceeding anyway.")

    # Resolve data_root from the server path when using server-relative mode
    if cfg.data_root is None:
        if cfg.server_root is None:
            raise RuntimeError("Neither data_root nor server_root is set — cannot proceed.")
        cfg.data_root = _resolve_data_root(cfg)

    log.info("Session data root: %s", cfg.data_root)

    start_time = _get_session_start_time(cfg.data_root)
    with state.lock:
        state.phase = Phase.RUNNING
        state.start_time = start_time
    log.info("Session start time (earliest file): %s", start_time.isoformat())
    log.info("=== PHASE: RUNNING ===")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2: Cadence cycle
# ──────────────────────────────────────────────────────────────────────────────

def run_cadence_cycle(cfg: ConductorConfig, state: ConductorState) -> None:
    """Execute one full pipeline + metadata + upload cycle.

    Thread-safe via ``_CYCLE_LOCK`` — concurrent invocations are dropped
    (not queued) so hotkey mashing does not pile up work.

    Parameters
    ----------
    cfg : ConductorConfig
        Conductor configuration.
    state : ConductorState
        Shared conductor state.
    """
    if not _CYCLE_LOCK.acquire(blocking=False):
        log.info("Cycle already running — skipping this trigger.")
        return

    try:
        log.info("--- Cadence cycle started ---")
        now = datetime.now(timezone.utc)

        # ── a. delphi-data pipeline / consolidation ──────────────────────────
        is_delphi = "delphi" in cfg.experiment_type
        if not state.pipeline_enabled:
            log.info("Pipeline disabled — skipping.")
        elif not is_delphi:
            # Pirouette-only: no Delphi controller data, but still consolidate
            # run sub-directories so ecephys data is in canonical layout.
            if not state.first_consolidation_done:
                success = run_consolidation(data_root=cfg.data_root)
                if success:
                    with state.lock:
                        state.first_consolidation_done = True
                        state.last_pipeline_run = now
        else:
            success = run_pipeline(
                data_root=cfg.data_root,
                experiment=cfg.delphi_experiment,
                firmware=cfg.delphi_firmware,
                subject_id=cfg.subject_id,
                skip_build=cfg.pipeline_skip_build,
                skip_clips=cfg.pipeline_skip_clips,
                skip_snapshot=cfg.pipeline_skip_snapshot,
            )
            if success:
                with state.lock:
                    state.first_consolidation_done = True
                    state.last_pipeline_run = now

        # Resolve the earliest run directory after consolidation.
        # cfg.data_root is the session root; all steps below must operate on
        # the run dir (behavior/, ecephys/, metadata/ all live inside it).
        run_dir = _resolve_run_dir(cfg.data_root)
        log.debug("Effective run directory: %s", run_dir)

        # ── b. Move Delphi metadata ──────────────────────────────────────────
        if not is_delphi:
            pass  # no Delphi metadata files to move
        elif state.first_consolidation_done and not state.delphi_metadata_moved:
            move_delphi_metadata(run_dir)
            with state.lock:
                state.delphi_metadata_moved = True

        # ── c. Generate AIND metadata (once) ────────────────────────────────
        if not state.metadata_enabled:
            log.info("Metadata generation disabled — skipping.")
        elif state.delphi_metadata_moved and not state.metadata_generated:
            ok = generate_metadata(
                experiment_type=cfg.experiment_type,
                subject_id=cfg.subject_id,
                protocol_id=cfg.protocol_id,
                instrument_id=cfg.instrument_id,
                experiment_room=cfg.experiment_room,
                acquisition_type=cfg.experiment_type,
                delphi_computer_id=cfg.delphi_computer_id,
                surgeons=cfg.surgeons,
                experimenters=cfg.experimenters,
                data_root=run_dir,
                surgery_notes_base=cfg.surgery_notes_base,
                metadata_output_path=run_dir / "metadata",
            )
            if ok:
                initial_end_time = datetime.now(timezone.utc)
                with state.lock:
                    state.metadata_generated = True
                    if state.experiment_end_time is None:
                        state.experiment_end_time = initial_end_time
                    end_time_snapshot = state.experiment_end_time
                update_acquisition_end_time(
                    run_dir / "metadata", end_time_snapshot
                )

        # ── d. Upload ────────────────────────────────────────────────────────
        if not state.upload_enabled:
            log.info("Upload disabled — skipping.")
        elif _metadata_ready(run_dir):
            n_chunks = _count_local_chunks(run_dir, folder=cfg.chunk_camera_folder)
            is_start = not state.upload_started
            if is_start and n_chunks < 3:
                log.info(
                    "Waiting for >=3 chunks before start upload (%d so far).", n_chunks
                )
            else:
                acq_start = state.start_time or datetime.now(timezone.utc)
                ok = run_upload_cycle(
                    source_directory=str(run_dir),
                    subject_id=cfg.subject_id,
                    acq_datetime=acq_start,
                    project_name=cfg.project_name,
                    contact_email=cfg.contact_email,
                    s3_bucket=cfg.s3_bucket,
                    batch_size=cfg.upload_batch_size,
                    dry_run=cfg.dry_run,
                    is_start_job=is_start,
                )
                if ok and is_start:
                    with state.lock:
                        state.upload_started = True
                if ok:
                    with state.lock:
                        state.last_upload_run = now

        log.info("--- Cadence cycle complete ---")

    finally:
        _CYCLE_LOCK.release()


# ──────────────────────────────────────────────────────────────────────────────
# Cadence scheduler
# ──────────────────────────────────────────────────────────────────────────────

class _CadenceScheduler:
    """Self-rescheduling timer with two modes.

    **Interval mode** (default): fires every ``interval_seconds`` seconds.

    **On-the-hour mode**: fires at a fixed minute past every hour
    (e.g. minute=45 → 10:45, 11:45, …).  The first tick is scheduled for the
    next occurrence of that minute; subsequent ticks fire every 3600 s so they
    stay locked to the same wall-clock minute.
    """

    def __init__(
        self,
        callback,
        interval_seconds: Optional[float] = None,
        on_minute_of_hour: Optional[int] = None,
    ):
        if on_minute_of_hour is not None:
            if not (0 <= on_minute_of_hour <= 59):
                raise ValueError("on_minute_of_hour must be 0–59")
            self._mode = "hourly"
            self._target_minute = on_minute_of_hour
        else:
            self._mode = "interval"
            self._interval = interval_seconds
        self._callback = callback
        self._timer: Optional[threading.Timer] = None
        self._stopped = False

    def start(self) -> None:
        """Schedule the first tick."""
        self._schedule_next()

    def _seconds_until_next_minute(self) -> float:
        """Seconds from now until the next occurrence of ``_target_minute``."""
        now = datetime.now()
        current_total_minutes = now.hour * 60 + now.minute
        target_total_minutes = (now.hour if now.minute < self._target_minute else now.hour + 1) * 60 + self._target_minute
        # Handle hour wrap (e.g. target=5, now=23:50 → next day 00:05)
        delta_minutes = target_total_minutes - current_total_minutes
        if delta_minutes <= 0:
            delta_minutes += 60
        return delta_minutes * 60 - now.second - now.microsecond / 1e6

    def _schedule_next(self) -> None:
        if self._stopped:
            return
        if self._mode == "hourly":
            delay = self._seconds_until_next_minute()
        else:
            delay = self._interval
        self._timer = threading.Timer(delay, self._tick)
        self._timer.daemon = True
        self._timer.start()
        log.debug("Next cadence cycle in %.0f s (%.1f min)", delay, delay / 60)

    def _tick(self) -> None:
        if not self._stopped:
            try:
                self._callback()
            except Exception as exc:
                log.error("Cadence callback error: %s", exc, exc_info=True)
            # In hourly mode reschedule for 3600 s so the next tick stays on
            # the same minute; _seconds_until_next_minute would add only ~0 s.
            if self._mode == "hourly" and not self._stopped:
                self._timer = threading.Timer(3600, self._tick)
                self._timer.daemon = True
                self._timer.start()
            else:
                self._schedule_next()

    def stop(self) -> None:
        """Cancel the pending timer."""
        self._stopped = True
        if self._timer:
            self._timer.cancel()


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3: Experiment end
# ──────────────────────────────────────────────────────────────────────────────

def end_experiment(cfg: ConductorConfig, state: ConductorState) -> None:
    """Handle the experiment-end sequence.

    Parameters
    ----------
    cfg : ConductorConfig
        Conductor configuration.
    state : ConductorState
        Shared conductor state.
    """
    log.info("=== PHASE: ENDING ===")
    with state.lock:
        state.phase = Phase.ENDING

    # Prompt for end time — show the current value if already set via hotkey
    with state.lock:
        current_end = state.experiment_end_time
    if current_end:
        prompt = (
            f"\nExperiment ended. Current end time: {current_end.strftime('%H:%M')} UTC. "
            "Enter new HH:MM to override, or press Enter to keep it: "
        )
    else:
        prompt = "\nExperiment ended. Enter end time as HH:MM (UTC, 24h) or press Enter for now: "

    raw = input(prompt).strip()
    if raw:
        try:
            now = datetime.now(timezone.utc)
            h, m = (int(x) for x in raw.split(":"))
            end_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
        except Exception:
            log.warning("Could not parse time '%s'; keeping existing end time.", raw)
            end_time = current_end or datetime.now(timezone.utc)
    else:
        end_time = current_end or datetime.now(timezone.utc)

    with state.lock:
        state.experiment_end_time = end_time

    log.info("Experiment end time: %s", end_time.isoformat())

    # Update acquisition.json — resolve run dir to find metadata/
    run_dir = _resolve_run_dir(cfg.data_root)
    if state.metadata_generated:
        update_acquisition_end_time(run_dir / "metadata", end_time)

    # Pirouette: verify probe.json
    if "pirouette" in cfg.experiment_type.lower():
        if not verify_probe_json(run_dir):
            print(
                "\nWARNING: probe.json missing from ecephys/. "
                "Place it there before the upload is submitted."
            )
            input("Press Enter to continue once probe.json is in place ...")

    # Final pipeline + upload cycle — wait for any in-flight cycle to finish
    # first so the lock is free and the final cycle is guaranteed to run.
    log.info("Running final processing and upload cycle ...")
    if not _CYCLE_LOCK.acquire(timeout=300):
        log.warning("Timed out waiting for in-flight cycle; final cycle may be incomplete.")
    else:
        _CYCLE_LOCK.release()
    run_cadence_cycle(cfg, state)

    # Stop upload cleanly
    stop_upload()

    # Optional local deletion — only after confirming chunks are on S3
    if cfg.delete_after_upload and state.upload_started:
        log.info("Deleting large local files (delete_after_upload=true) ...")
        delete_local_files_after_upload(
            data_root=run_dir,
            keep_patterns=cfg.keep_local_patterns,
            s3_bucket=cfg.s3_bucket,
            subject_id=cfg.subject_id,
            acq_datetime=state.start_time or datetime.now(timezone.utc),
        )

    with state.lock:
        state.phase = Phase.DONE
    log.info("=== PHASE: DONE ===")


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """Experiment conductor main entry point.

    Loads configuration, runs the full experiment lifecycle:
    launch -> periodic cycles -> end -> cleanup.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    cfg = build_config()
    state = ConductorState()

    # Initialise runtime-mutable stage flags from config
    state.pipeline_enabled = cfg.enable_pipeline
    state.metadata_enabled = cfg.enable_metadata
    state.upload_enabled = cfg.enable_upload

    # For pirouette-only experiments there is no Delphi controller data, so the
    # pipeline and metadata-move steps are skipped.  Pre-set the gate flag so
    # AIND metadata generation isn't blocked waiting for a pipeline that will
    # never run.
    if "delphi" not in cfg.experiment_type:
        state.delphi_metadata_moved = True

    log.info("Experiment Conductor starting.")
    log.info("  Experiment type : %s", cfg.experiment_type)
    log.info("  Subject ID      : %s", cfg.subject_id)
    if cfg.server_root:
        log.info("  Server root     : %s", cfg.server_root)
        log.info("  Session DT      : %s", cfg.session_datetime or "(auto-detect after launch)")
        log.info("  data_root       : (resolved after launcher exits)")
    else:
        log.info("  Data root       : %s", cfg.data_root)
    if cfg.schedule_minute_of_hour is not None:
        log.info("  Schedule        : :%02d every hour", cfg.schedule_minute_of_hour)
    else:
        log.info("  Cadence         : %d min", cfg.pipeline_cadence_minutes)
    log.info("  S3 bucket       : %s", cfg.s3_bucket)
    log.info("  Dry run         : %s", cfg.dry_run)
    log.info("  Pipeline        : %s (skip_build=%s, skip_clips=%s, skip_snapshot=%s)",
             "ON" if cfg.enable_pipeline else "OFF",
             cfg.pipeline_skip_build, cfg.pipeline_skip_clips, cfg.pipeline_skip_snapshot)
    log.info("  Metadata        : %s", "ON" if cfg.enable_metadata else "OFF")
    log.info("  Upload          : %s", "ON" if cfg.enable_upload else "OFF")

    # ── Phase 1: Launch ──────────────────────────────────────────────────────
    launch_experiments(cfg, state)

    # ── Phase 2: Periodic cycles ─────────────────────────────────────────────
    if cfg.schedule_minute_of_hour is not None:
        scheduler = _CadenceScheduler(
            callback=lambda: run_cadence_cycle(cfg, state),
            on_minute_of_hour=cfg.schedule_minute_of_hour,
        )
        log.info("Cadence scheduler started (every hour at :%02d).", cfg.schedule_minute_of_hour)
    else:
        scheduler = _CadenceScheduler(
            callback=lambda: run_cadence_cycle(cfg, state),
            interval_seconds=cfg.pipeline_cadence_minutes * 60,
        )
        log.info("Cadence scheduler started (%d min interval).", cfg.pipeline_cadence_minutes)
    scheduler.start()

    # Run first cycle immediately after launch
    threading.Thread(
        target=lambda: run_cadence_cycle(cfg, state), daemon=True
    ).start()

    # Hotkeys
    def _on_end():
        state.end_experiment_event.set()

    def _toggle_pipeline():
        with state.lock:
            state.pipeline_enabled = not state.pipeline_enabled
        status = "ENABLED" if state.pipeline_enabled else "DISABLED"
        log.info("Pipeline toggled: %s", status)
        print(f"\n[hotkey] Pipeline {status}")

    def _toggle_metadata():
        with state.lock:
            state.metadata_enabled = not state.metadata_enabled
        status = "ENABLED" if state.metadata_enabled else "DISABLED"
        log.info("Metadata generation toggled: %s", status)
        print(f"\n[hotkey] Metadata generation {status}")

    def _toggle_upload():
        with state.lock:
            state.upload_enabled = not state.upload_enabled
        status = "ENABLED" if state.upload_enabled else "DISABLED"
        log.info("Upload toggled: %s", status)
        print(f"\n[hotkey] Upload {status}")

    def _update_end_time():
        """Prompt for a new end time and update acquisition.json."""
        raw = input(
            "\n[hotkey] Enter new end time as HH:MM (UTC, 24h) or press Enter for now: "
        ).strip()
        if raw:
            try:
                now = datetime.now(timezone.utc)
                h, m = (int(x) for x in raw.split(":"))
                new_end = now.replace(hour=h, minute=m, second=0, microsecond=0)
            except Exception:
                log.warning("Could not parse time '%s'; using current UTC time.", raw)
                new_end = datetime.now(timezone.utc)
        else:
            new_end = datetime.now(timezone.utc)
        with state.lock:
            state.experiment_end_time = new_end
        log.info("End time updated to %s", new_end.isoformat())
        print(f"[hotkey] End time updated to {new_end.isoformat()}")
        if state.metadata_generated:
            update_acquisition_end_time(cfg.data_root / "metadata", new_end)

    def _retry_metadata():
        """Reset the metadata-generated flag so the next cycle regenerates it."""
        with state.lock:
            state.metadata_generated = False
        log.info("Metadata retry requested — will regenerate on next cycle.")
        print("\n[hotkey] Metadata will regenerate on the next cycle.")
        threading.Thread(target=lambda: run_cadence_cycle(cfg, state), daemon=True).start()

    hotkeys = HotkeyListener(
        hotkey_pipeline=cfg.hotkey_pipeline,
        hotkey_upload_pause=cfg.hotkey_upload_pause,
        hotkey_end_experiment=cfg.hotkey_end_experiment,
        on_pipeline=lambda: run_cadence_cycle(cfg, state),
        on_upload_pause=toggle_upload_pause,
        on_end_experiment=_on_end,
        hotkey_toggle_pipeline=cfg.hotkey_toggle_pipeline,
        hotkey_toggle_metadata=cfg.hotkey_toggle_metadata,
        hotkey_toggle_upload=cfg.hotkey_toggle_upload,
        on_toggle_pipeline=_toggle_pipeline,
        on_toggle_metadata=_toggle_metadata,
        on_toggle_upload=_toggle_upload,
        hotkey_update_end_time=cfg.hotkey_update_end_time,
        hotkey_retry_metadata=cfg.hotkey_retry_metadata,
        on_update_end_time=_update_end_time,
        on_retry_metadata=_retry_metadata,
    )
    hotkeys.start()

    toggle_lines = ""
    if cfg.hotkey_toggle_pipeline:
        toggle_lines += f"  {cfg.hotkey_toggle_pipeline} -> toggle pipeline on/off\n"
    if cfg.hotkey_toggle_metadata:
        toggle_lines += f"  {cfg.hotkey_toggle_metadata} -> toggle metadata on/off\n"
    if cfg.hotkey_toggle_upload:
        toggle_lines += f"  {cfg.hotkey_toggle_upload} -> toggle upload on/off\n"
    if cfg.hotkey_update_end_time:
        toggle_lines += f"  {cfg.hotkey_update_end_time} -> update acquisition end time\n"
    if cfg.hotkey_retry_metadata:
        toggle_lines += f"  {cfg.hotkey_retry_metadata} -> retry metadata generation\n"

    print(
        f"\nExperiment running."
        f"\n  Pipeline : {'ON' if state.pipeline_enabled else 'OFF'}"
        f"  (build={'skip' if cfg.pipeline_skip_build else 'on'}"
        f", clips={'skip' if cfg.pipeline_skip_clips else 'on'}"
        f", snapshot={'skip' if cfg.pipeline_skip_snapshot else 'on'})"
        f"\n  Metadata : {'ON' if state.metadata_enabled else 'OFF'}"
        f"\n  Upload   : {'ON' if state.upload_enabled else 'OFF'}"
        f"\nHotkeys:\n"
        f"  {cfg.hotkey_pipeline}       -> trigger pipeline now\n"
        f"  {cfg.hotkey_upload_pause}   -> pause/resume upload\n"
        f"  {cfg.hotkey_end_experiment} -> end experiment\n"
        f"{toggle_lines}"
        f"  Ctrl+C             -> emergency exit\n"
    )

    # Wait for end signal or Ctrl+C
    try:
        while not state.end_experiment_event.is_set():
            # Also exit loop if Bonsai is no longer running
            if state.phase == Phase.RUNNING and not _bonsai_running():
                log.info("Bonsai no longer detected — prompting for experiment end.")
                break
            time.sleep(5)
    except KeyboardInterrupt:
        log.info("Ctrl+C received.")

    scheduler.stop()
    hotkeys.stop()

    # ── Phase 3: End ─────────────────────────────────────────────────────────
    end_experiment(cfg, state)
    log.info("Experiment conductor finished.")


if __name__ == "__main__":
    main()
