"""Experiment conductor — main orchestration logic.

Lifecycle
---------
1. LAUNCHING  — spawn the launcher subprocess; wait for Bonsai to appear.
2. RUNNING    — periodic cadence cycles (pipeline -> metadata -> upload).
3. ENDING     — user signals end; update acquisition end time; final cycle.
4. DONE       — cleanup.

Each cadence cycle:
  a. Run delphi-data pipeline (consolidate + build-dataset + snapshot).
  b. After first consolidation: move Delphi metadata files.
  c. After metadata moved (once): generate AIND metadata.
  d. If AIND metadata exists + >=3 local chunks: submit upload job.
"""
from __future__ import annotations

import logging
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
from experiment_conductor.pipeline_bridge import move_delphi_metadata, run_pipeline
from experiment_conductor.state import ConductorState, Phase
from experiment_conductor.uploader_bridge import (
    UPLOAD_PAUSE_EVENT,
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
    import re

    chunk_re = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}")
    target = data_root / folder
    if not target.exists():
        return 0
    return sum(1 for d in target.iterdir() if d.is_dir() and chunk_re.fullmatch(d.name))


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

    with state.lock:
        state.phase = Phase.RUNNING
        state.start_time = datetime.now(timezone.utc)
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

        # ── a. delphi-data pipeline ──────────────────────────────────────────
        success = run_pipeline(
            data_root=cfg.data_root,
            experiment=cfg.delphi_experiment,
            firmware=cfg.delphi_firmware,
            subject_id=cfg.subject_id,
            skip_clips=True,
        )
        if success:
            with state.lock:
                state.first_consolidation_done = True
                state.last_pipeline_run = now

        # ── b. Move Delphi metadata ──────────────────────────────────────────
        if state.first_consolidation_done and not state.delphi_metadata_moved:
            move_delphi_metadata(cfg.data_root)
            with state.lock:
                state.delphi_metadata_moved = True

        # ── c. Generate AIND metadata (once) ────────────────────────────────
        if state.delphi_metadata_moved and not state.metadata_generated:
            ok = generate_metadata(
                experiment_type=cfg.experiment_type,
                subject_id=cfg.subject_id,
                protocol_id=cfg.protocol_id,
                instrument_id=cfg.instrument_id,
                experiment_room=cfg.experiment_room,
                acquisition_type=cfg.acquisition_type,
                delphi_computer_id=cfg.delphi_computer_id,
                surgeons=cfg.surgeons,
                experimenters=cfg.experimenters,
                data_root=cfg.data_root,
                surgery_notes_base=cfg.surgery_notes_base,
                metadata_output_path=cfg.data_root / "metadata",
            )
            if ok:
                with state.lock:
                    state.metadata_generated = True

        # ── d. Upload ────────────────────────────────────────────────────────
        if _metadata_ready(cfg.data_root):
            n_chunks = _count_local_chunks(cfg.data_root)
            is_start = not state.upload_started
            if is_start and n_chunks < 3:
                log.info(
                    "Waiting for >=3 chunks before start upload (%d so far).", n_chunks
                )
            else:
                acq_start = state.start_time or datetime.now(timezone.utc)
                ok = run_upload_cycle(
                    source_directory=str(cfg.data_root),
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

    # Prompt for actual end time
    raw = input(
        "\nExperiment ended. Enter end time as HH:MM (UTC, 24h) or press Enter for now: "
    ).strip()
    if raw:
        try:
            now = datetime.now(timezone.utc)
            h, m = (int(x) for x in raw.split(":"))
            end_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
        except Exception:
            log.warning("Could not parse time '%s'; using current UTC time.", raw)
            end_time = datetime.now(timezone.utc)
    else:
        end_time = datetime.now(timezone.utc)

    with state.lock:
        state.experiment_end_time = end_time

    log.info("Experiment end time: %s", end_time.isoformat())

    # Update acquisition.json
    if state.metadata_generated:
        update_acquisition_end_time(cfg.data_root / "metadata", end_time)

    # Pirouette: verify probe.json
    if "pirouette" in cfg.experiment_type.lower():
        if not verify_probe_json(cfg.data_root):
            print(
                "\nWARNING: probe.json missing from ecephys/. "
                "Place it there before the upload is submitted."
            )
            input("Press Enter to continue once probe.json is in place ...")

    # Final pipeline + upload cycle
    log.info("Running final processing and upload cycle ...")
    run_cadence_cycle(cfg, state)

    # Stop upload cleanly
    stop_upload()

    # Optional local deletion — only after confirming chunks are on S3
    if cfg.delete_after_upload and state.upload_started:
        log.info("Deleting large local files (delete_after_upload=true) ...")
        delete_local_files_after_upload(
            data_root=cfg.data_root,
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

    log.info("Experiment Conductor starting.")
    log.info("  Experiment type : %s", cfg.experiment_type)
    log.info("  Data root       : %s", cfg.data_root)
    log.info("  Subject ID      : %s", cfg.subject_id)
    if cfg.schedule_minute_of_hour is not None:
        log.info("  Schedule        : :%02d every hour", cfg.schedule_minute_of_hour)
    else:
        log.info("  Cadence         : %d min", cfg.pipeline_cadence_minutes)
    log.info("  S3 bucket       : %s", cfg.s3_bucket)
    log.info("  Dry run         : %s", cfg.dry_run)

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

    hotkeys = HotkeyListener(
        hotkey_pipeline=cfg.hotkey_pipeline,
        hotkey_upload_pause=cfg.hotkey_upload_pause,
        hotkey_end_experiment=cfg.hotkey_end_experiment,
        on_pipeline=lambda: run_cadence_cycle(cfg, state),
        on_upload_pause=toggle_upload_pause,
        on_end_experiment=_on_end,
    )
    hotkeys.start()

    print(
        f"\nExperiment running. Hotkeys:\n"
        f"  {cfg.hotkey_pipeline}       -> trigger pipeline now\n"
        f"  {cfg.hotkey_upload_pause}   -> pause/resume upload\n"
        f"  {cfg.hotkey_end_experiment} -> end experiment\n"
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
