"""Session manager: orchestrates multiple acquisition sessions.

The :class:`SessionManager` is the heart of the refactored conductor.  It:

1. Polls configured *watch paths* for new session directories via
   :func:`~experiment_conductor.watcher.discover_sessions`.
2. Maintains a registry of :class:`~experiment_conductor.session.SessionState`
   objects, one per session.
3. Runs each due session through a fixed sequence of steps on a cadence.
4. Persists state to a JSON file so work survives conductor restarts.

Per-session cadence cycle
--------------------------
For each session whose last-processed timestamp is older than
``pipeline_cadence_minutes``:

a. **Consolidate** — merge run sub-directories into the earliest run dir and
   relocate Delphi metadata JSONL files to ``behavior/metadata/``.
b. **Check metadata** — verify that all four AIND JSON files are present.
   If not, run :func:`~experiment_conductor.metadata_bridge.generate_metadata`.
c. **Build dataset** — if a ``DelphiController*.jsonl`` file exists, invoke
   ``delphi-data pipeline`` to build/append the behaviour dataset.
d. **Noise floor** — if ``enable_noise_floor`` is set, estimate the ephys
   RMS noise floor from raw binary data (once per session).
e. **Upload** — submit new chunk jobs to the AIND data-transfer service.

Sessions with unrecoverable errors are marked :attr:`SessionPhase.ERROR`
after ``max_consecutive_errors`` consecutive failures and skipped until
manually remedied.
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ConductorConfig
from .pause_control import is_paused, paused_since
from .logging_config import VERBOSE
from .metadata_bridge import check_metadata_present, generate_metadata, update_acquisition_end_time
from .noise_floor import estimate_noise_floor
from .pipeline_bridge import (
    has_delphi_controller_file,
    move_delphi_metadata,
    resolve_run_dir,
    run_consolidation,
    run_pipeline,
)
from delphi_data.curation import normalize_onix_sample_metadata
from .session import SessionPhase, SessionState
from .upload_sidecar import UploadSidecar
from .uploader_bridge import (
    UPLOAD_STOP_EVENT,
    delete_local_files_after_upload,
    list_confirmed_s3_chunks,
    run_upload_cycle,
    stop_upload,
    compute_s3_prefix,
)
from .watcher import discover_sessions

log = logging.getLogger(__name__)

_CHUNK_COUNT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")


class FatalSessionError(Exception):
    """An unrecoverable error that immediately marks the session ERROR.

    Raise instead of a plain exception when the condition requires manual
    intervention and retrying would be pointless (e.g. a required file is
    permanently absent).
    """


def _count_local_chunks(run_dir: Path, folder: str) -> int:
    """Count hourly video chunks in the camera folder.

    Supports two layouts:

    * **Flat files** (current): ``<Camera>_YYYY-MM-DDTHH-MM-SS.mp4`` files
      directly inside ``folder``.  Each distinct timestamp suffix = one chunk.
    * **Subdirectory** (legacy): timestamp-named directories inside ``folder``.
    """
    target = run_dir / folder
    if not target.exists():
        return 0

    # Flat-file layout: count distinct timestamps from *.mp4 files.
    # Pattern: anything ending in _YYYY-MM-DDTHH-MM-SS.mp4
    _SUFFIX_RE = re.compile(r"_(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})\.mp4$")
    timestamps = {
        m.group(1)
        for f in target.iterdir()
        if f.is_file() and (m := _SUFFIX_RE.search(f.name))
    }
    if timestamps:
        return len(timestamps)

    # Subdirectory layout (fallback).
    return sum(
        1 for d in target.iterdir()
        if d.is_dir() and _CHUNK_COUNT_RE.fullmatch(d.name)
    )


def _parse_session_datetime(name: str) -> datetime:
    """Parse a ``YYYY-MM-DDTHH-MM-SS`` directory name to a UTC datetime."""
    return datetime.strptime(name, "%Y-%m-%dT%H-%M-%S").replace(tzinfo=timezone.utc)


class SessionManager:
    """Manages the lifecycle of multiple acquisition sessions.

    Discovers new sessions on one or more shared network drive paths, processes
    each session on a configurable cadence, and persists state between restarts.

    Parameters
    ----------
    cfg : ConductorConfig
        Global conductor configuration.
    """

    def __init__(self, cfg: ConductorConfig) -> None:
        self.cfg = cfg
        self._sessions: dict[str, SessionState] = {}   # keyed by str(data_root)
        self._registry_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._load_state()

    # ── Public API ────────────────────────────────────────────────────────────

    def add_session(self, subject_id: str, data_root: Path) -> bool:
        """Register a session for processing.

        Returns *True* if this is a newly registered session.

        Parameters
        ----------
        subject_id : str
            Numeric AIND subject identifier.
        data_root : Path
            Session root directory (contains run-timestamp sub-directories).

        Returns
        -------
        bool
            *True* if the session was newly added; *False* if it was already
            known.
        """
        key = str(data_root)
        with self._registry_lock:
            if key in self._sessions:
                return False
            state = SessionState(
                data_root=data_root,
                subject_id=subject_id,
                session_datetime=data_root.name,
                discovered_at=datetime.now(),
            )
            self._sessions[key] = state
        log.info(
            "NEW SESSION — subject=%s  ts=%s  path=%s",
            subject_id,
            data_root.name,
            data_root,
        )
        log.log(
            VERBOSE,
            "Session registered at %s  (min age %.1f min before first processing)",
            datetime.now().strftime("%H:%M:%S"),
            self.cfg.min_session_age_minutes,
        )
        return True

    @property
    def sessions(self) -> list[SessionState]:
        """Snapshot of the current session registry."""
        with self._registry_lock:
            return list(self._sessions.values())

    def run(self) -> None:
        """Main polling loop — runs until Ctrl-C, SIGTERM, or :meth:`stop`.

        SIGINT (Ctrl-C) is intentionally **not** overridden so that Python
        raises ``KeyboardInterrupt`` naturally — this guarantees the process
        exits immediately on Windows and Unix alike.  SIGTERM is handled
        gracefully on platforms that support it (not Windows).
        """
        # SIGTERM: graceful shutdown on Unix.  Windows doesn't support it so
        # we skip registration there to avoid a silent no-op or OSError.
        if sys.platform != "win32":
            try:
                signal.signal(signal.SIGTERM, self._handle_signal)
            except (OSError, ValueError):
                pass

        log.info(
            "Session manager started. "
            "Watching %d path(s). Poll interval: %.0f s. Cadence: %d min.",
            len(self.cfg.watch_paths),
            self.cfg.poll_interval_s,
            self.cfg.pipeline_cadence_minutes,
        )
        if self.cfg.subject_ids:
            log.info(
                "  Workers: %d (one per restricted subject ID; "
                "updates automatically when CONDUCTOR_SUBJECT_IDS changes).",
                len(self.cfg.subject_ids),
            )
        else:
            log.info(
                "  Workers: %d (unrestricted fallback — set CONDUCTOR_SUBJECT_IDS "
                "to pin one worker per subject).",
                self.cfg.max_workers,
            )
        for p in self.cfg.watch_paths:
            log.info("  Watch path: %s", p)

        if is_paused(self.cfg.pause_file):
            since = paused_since(self.cfg.pause_file)
            log.warning(
                "⚠️  Upload submissions are currently PAUSED%s.  "
                "Run `conductor-status` and choose Resume, or delete: %s",
                f" (since {since})" if since else "",
                self.cfg.pause_file,
            )

        # Write PID file so conductor-status can force-kill this process.
        pid_file = self.cfg.pause_file.parent / "conductor.pid"
        try:
            pid_file.write_text(str(os.getpid()), encoding="utf-8")
            log.log(VERBOSE, "PID %d written to %s", os.getpid(), pid_file)
        except OSError as exc:
            log.warning("Could not write PID file %s: %s", pid_file, exc)
            pid_file = None  # type: ignore[assignment]

        try:
            while not self._stop_event.is_set():
                log.debug("Poll cycle starting.")
                try:
                    self._scan_and_register()
                    self._process_due_sessions()
                    self._save_state()
                except Exception:
                    log.exception("Unexpected error in main loop.")
                log.debug(
                    "Poll cycle complete.  Next poll in %.0f s.",
                    self.cfg.poll_interval_s,
                )
                self._stop_event.wait(self.cfg.poll_interval_s)
        except KeyboardInterrupt:
            log.info(
                "Keyboard interrupt — stopping gracefully "
                "(press Ctrl+C again to force quit) ..."
            )
            self.stop()
            try:
                self._stop_event.wait(5.0)
            except KeyboardInterrupt:
                log.warning("Force quit.")
                if pid_file and pid_file.exists():
                    pid_file.unlink(missing_ok=True)
                os._exit(1)
        finally:
            if pid_file and pid_file.exists():
                try:
                    pid_file.unlink(missing_ok=True)
                except OSError:
                    pass

        log.info("Session manager stopped.")

    def stop(self) -> None:
        """Signal the main loop to exit after the current iteration."""
        self._stop_event.set()
        stop_upload()

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _reload_subject_ids(self) -> None:
        """Re-read ``CONDUCTOR_SUBJECT_IDS`` from the ``.env`` file.

        Called at the start of every scan so that new subject IDs can be added
        (or removed) by editing the ``.env`` file while the conductor is
        running, without requiring a restart.  Changes are logged at INFO level.
        Falls back to the in-memory value silently if the file cannot be read.
        """
        try:
            from dotenv import dotenv_values
            vals = dotenv_values(self.cfg.env_file_path)
            raw = vals.get("CONDUCTOR_SUBJECT_IDS", "")
            new_ids = [s.strip() for s in raw.split(",") if s.strip()]
        except Exception:
            return  # keep existing list on any read error

        current = set(self.cfg.subject_ids)
        incoming = set(new_ids)

        added = incoming - current
        removed = current - incoming

        if added:
            log.info(
                "Subject ID allowlist updated — added: %s",
                ", ".join(sorted(added)),
            )
        if removed:
            log.info(
                "Subject ID allowlist updated — removed: %s",
                ", ".join(sorted(removed)),
            )

        if added or removed:
            self.cfg.subject_ids = new_ids

    def _scan_and_register(self) -> None:
        self._reload_subject_ids()
        allowed = set(self.cfg.subject_ids) if self.cfg.subject_ids else None
        found = discover_sessions(self.cfg.watch_paths, allowed_subjects=allowed)
        log.log(VERBOSE, "Scan complete — %d session(s) visible on watch paths.", len(found))
        for subject_id, session_root in found:
            self.add_session(subject_id, session_root)

    # ── Scheduling ────────────────────────────────────────────────────────────

    def _due_sessions(self) -> list[SessionState]:
        """Return sessions that are ready for a processing cycle right now."""
        now = datetime.now()
        min_age = timedelta(minutes=self.cfg.min_session_age_minutes)
        cadence = timedelta(minutes=self.cfg.pipeline_cadence_minutes)
        backoff = timedelta(minutes=self.cfg.error_backoff_minutes)
        due: list[SessionState] = []

        with self._registry_lock:
            for state in self._sessions.values():
                if state.phase in (SessionPhase.COMPLETE, SessionPhase.ERROR):
                    continue
                # Respect error backoff
                if state.consecutive_errors >= self.cfg.max_consecutive_errors:
                    if state.last_processed and (now - state.last_processed) < backoff:
                        continue
                # Don't process until minimum age
                if state.discovered_at and (now - state.discovered_at) < min_age:
                    continue
                # Respect cadence
                if state.last_processed and (now - state.last_processed) < cadence:
                    continue
                due.append(state)

        return due

    def _process_due_sessions(self) -> None:
        due = self._due_sessions()
        if not due:
            log.debug("No sessions due for processing.")
            return

        # Derive worker count from the subject-ID allowlist so each allowed
        # animal gets its own thread.  Falls back to cfg.max_workers when no
        # allowlist is configured (unrestricted mode).
        n_subjects = len(self.cfg.subject_ids)
        workers = n_subjects if n_subjects > 0 else self.cfg.max_workers
        log.info(
            "Processing %d due session(s) with %d worker(s)%s.",
            len(due),
            workers,
            f" ({n_subjects} restricted subject(s))" if n_subjects > 0 else " (unrestricted)",
        )
        # Use explicit shutdown(wait=False) so that a KeyboardInterrupt or
        # force-kill does not block waiting for network-I/O-bound workers.
        # Workers that are already running will finish in the background;
        # any pending (not-yet-started) futures are cancelled immediately.
        pool = ThreadPoolExecutor(max_workers=workers)
        futures = {pool.submit(self._process_one, s): s for s in due}
        try:
            for future in as_completed(futures):
                state = futures[future]
                try:
                    future.result()
                except Exception:
                    log.exception(
                        "Unexpected error processing session %s.", state.data_root
                    )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    # ── Per-session processing ────────────────────────────────────────────────

    def _process_one(self, state: SessionState) -> None:
        """Run one full cadence cycle for *state*."""
        with state.lock:
            state.last_processed = datetime.now()

        try:
            self._step_consolidate(state)
            self._step_metadata(state)
            self._step_pipeline(state)
            self._step_noise_floor(state)
            self._step_upload(state)
        except FatalSessionError as exc:
            # Unrecoverable — skip the retry ladder and mark ERROR immediately.
            with state.lock:
                state.error_message = str(exc)
                state.consecutive_errors = self.cfg.max_consecutive_errors
                state.phase = SessionPhase.ERROR
            log.error(
                "Session %s — fatal error, marking ERROR immediately. "
                "Manual intervention required: %s",
                state.data_root,
                exc,
            )
        except Exception as exc:
            with state.lock:
                state.error_message = str(exc)
                state.consecutive_errors += 1
                if state.consecutive_errors >= self.cfg.max_consecutive_errors:
                    state.phase = SessionPhase.ERROR
                    log.error(
                        "Session %s reached max errors (%d). Marking ERROR. "
                        "Manual intervention required.",
                        state.data_root,
                        self.cfg.max_consecutive_errors,
                    )
                    log.exception("Final error traceback:")
                else:
                    log.warning(
                        "Session %s error %d/%d: %s",
                        state.data_root.name,
                        state.consecutive_errors,
                        self.cfg.max_consecutive_errors,
                        exc,
                    )
                    log.debug("Error traceback:", exc_info=True)
        else:
            with state.lock:
                state.consecutive_errors = 0
                state.error_message = None

    def _step_consolidate(self, state: SessionState) -> None:
        """Merge run sub-directories and move Delphi JSONL metadata files."""
        with state.lock:
            already_done = state.consolidation_done

        with state.lock:
            state.phase = SessionPhase.CONSOLIDATING

        log.log(
            VERBOSE,
            "[%s] CONSOLIDATING — session root: %s  (previously consolidated: %s)",
            state.subject_id,
            state.data_root,
            already_done,
        )

        # Always consolidate — new Bonsai restarts may have created extra run dirs
        log.info("[%s] Consolidating run directories …", state.subject_id)
        ok = run_consolidation(state.data_root)

        # Resolve the canonical run dir after consolidation
        run_dir = resolve_run_dir(state.data_root)

        log.log(
            VERBOSE,
            "[%s] Run dir resolved to: %s  (consolidation ok=%s)",
            state.subject_id,
            run_dir,
            ok,
        )

        if ok and not already_done:
            move_delphi_metadata(run_dir)
        elif ok:
            # Re-check in case new metadata files appeared
            move_delphi_metadata(run_dir)

        # Normalise ONIX SampleMetadata for all session types (including
        # pirouette-only, where the pipeline step is skipped).  Safe no-op
        # when already normalised or when the OnixEphys/ directory is absent.
        if ok and run_dir is not None:
            log.log(
                VERBOSE,
                "[%s] Normalising ONIX SampleMetadata (ecephys/OnixEphys/) …",
                state.subject_id,
            )
            try:
                changed = normalize_onix_sample_metadata(run_dir)
                log.log(
                    VERBOSE,
                    "[%s] ONIX SampleMetadata: %s.",
                    state.subject_id,
                    "offset applied" if changed else "already at 0 / directory absent",
                )
            except Exception:
                log.warning(
                    "[%s] normalize_onix_sample_metadata raised an error (continuing).",
                    state.subject_id,
                    exc_info=True,
                )

        with state.lock:
            state.consolidation_done = ok
            if ok:
                state.run_dir = run_dir

    def _step_metadata(self, state: SessionState) -> None:
        """Check for, and if missing generate, AIND metadata JSON files."""
        with state.lock:
            run_dir = state.run_dir or resolve_run_dir(state.data_root)

        with state.lock:
            state.phase = SessionPhase.METADATA_CHECK

        log.log(
            VERBOSE,
            "[%s] METADATA_CHECK — checking %s/metadata/ for required JSON files.",
            state.subject_id,
            run_dir,
        )

        present = check_metadata_present(run_dir)
        with state.lock:
            state.metadata_present = present

        if present:
            log.log(VERBOSE, "[%s] All required metadata JSON files present — skipping generation.", state.subject_id)
            return

        if not self.cfg.enable_metadata:
            log.info("[%s] Metadata missing but generation disabled.", state.subject_id)
            return

        # ── Preflight: probe.json required for any experiment type with ecephys ─
        _ECEPHYS_TYPES = {"pirouette", "delphi_pirouette"}
        if self.cfg.experiment_type in _ECEPHYS_TYPES:
            probe_json = run_dir / "ecephys" / "probe.json"
            if not probe_json.exists():
                raise FatalSessionError(
                    f"probe.json not found at {probe_json}. "
                    "Copy the probe configuration JSON into ecephys/ and reset the session."
                )
            log.log(VERBOSE, "[%s] probe.json present: %s", state.subject_id, probe_json)

        with state.lock:
            state.phase = SessionPhase.METADATA_GENERATING
        log.info("[%s] Generating AIND metadata …", state.subject_id)
        log.log(
            VERBOSE,
            "[%s] METADATA_GENERATING — type=%s  instrument=%s  room=%s  experimenters=%s",
            state.subject_id,
            self.cfg.experiment_type,
            self.cfg.instrument_id,
            self.cfg.experiment_room,
            self.cfg.experimenters,
        )
        ok = generate_metadata(
            experiment_type=self.cfg.experiment_type,
            subject_id=state.subject_id,
            protocol_id=self.cfg.protocol_id,
            instrument_id=self.cfg.instrument_id,
            experiment_room=self.cfg.experiment_room,
            acquisition_type=self.cfg.acquisition_type,
            delphi_computer_id=self.cfg.delphi_computer_id,
            surgeons=self.cfg.surgeons,
            experimenters=self.cfg.experimenters,
            data_root=run_dir,
            surgery_notes_base=self.cfg.surgery_notes_base,
            metadata_output_path=run_dir / "metadata",
        )
        if ok:
            # Stamp acquisition end time with "now" as a placeholder
            update_acquisition_end_time(run_dir / "metadata", datetime.now(timezone.utc))
            log.info("[%s] AIND metadata generated successfully.", state.subject_id)
        else:
            log.warning("[%s] Metadata generation completed with errors.", state.subject_id)
        with state.lock:
            state.metadata_generated = ok
            if ok:
                state.metadata_present = True

    def _step_pipeline(self, state: SessionState) -> None:
        """Build/append the delphi-data behaviour dataset if applicable."""
        if not self.cfg.enable_pipeline:
            log.log(VERBOSE, "[%s] Pipeline disabled — skipping BUILDING step.", state.subject_id)
            return

        with state.lock:
            run_dir = state.run_dir or resolve_run_dir(state.data_root)
            already_built = state.dataset_built

        # Only run if there is actual Delphi controller data
        if not has_delphi_controller_file(run_dir):
            log.log(
                VERBOSE,
                "[%s] No behavior/DelphiController/ directory found — "
                "skipping pipeline (pirouette-only session).",
                state.subject_id,
            )
            return

        with state.lock:
            state.phase = SessionPhase.BUILDING
        log.info(
            "[%s] BUILDING delphi-data dataset%s …",
            state.subject_id,
            " (append mode)" if already_built else " (first run)",
        )
        log.log(
            VERBOSE,
            "[%s] Pipeline config — experiment=%s  firmware=%s  "
            "skip_build=%s  skip_clips=%s  skip_snapshot=%s  append=%s",
            state.subject_id,
            self.cfg.delphi_experiment,
            self.cfg.delphi_firmware,
            self.cfg.pipeline_skip_build,
            self.cfg.pipeline_skip_clips,
            self.cfg.pipeline_skip_snapshot,
            already_built,
        )

        ok = run_pipeline(
            data_root=run_dir,
            experiment=self.cfg.delphi_experiment,
            firmware=self.cfg.delphi_firmware,
            subject_id=state.subject_id,
            skip_build=self.cfg.pipeline_skip_build,
            skip_clips=self.cfg.pipeline_skip_clips,
            skip_snapshot=self.cfg.pipeline_skip_snapshot,
            append=already_built,
        )
        if ok:
            log.info("[%s] delphi-data pipeline completed successfully.", state.subject_id)
        else:
            log.warning("[%s] delphi-data pipeline reported errors.", state.subject_id)
        with state.lock:
            if ok:
                state.dataset_built = True

    def _step_noise_floor(self, state: SessionState) -> None:
        """Estimate the ephys noise floor (once per session)."""
        if not self.cfg.enable_noise_floor:
            log.log(VERBOSE, "[%s] Noise-floor estimation disabled — skipping.", state.subject_id)
            return

        with state.lock:
            if state.noise_floor_estimated:
                log.log(VERBOSE, "[%s] Noise floor already estimated — skipping.", state.subject_id)
                return
            run_dir = state.run_dir or resolve_run_dir(state.data_root)
            state.phase = SessionPhase.NOISE_FLOOR

        log.info("[%s] Estimating ephys noise floor …", state.subject_id)
        log.log(
            VERBOSE,
            "[%s] Noise floor config — n_seconds=%.1f  max_channels=%s",
            state.subject_id,
            self.cfg.noise_floor_n_seconds,
            self.cfg.noise_floor_max_channels or "all",
        )
        result = estimate_noise_floor(
            run_dir,
            n_seconds=self.cfg.noise_floor_n_seconds,
            max_channels=self.cfg.noise_floor_max_channels,
        )
        with state.lock:
            if result is not None:
                state.noise_floor_estimated = True
                log.info("[%s] Noise floor estimation complete.", state.subject_id)
            else:
                log.warning("[%s] Noise floor estimation returned no result.", state.subject_id)

    def _step_upload(self, state: SessionState) -> None:
        """Submit new chunk upload jobs to the AIND transfer service."""
        if not self.cfg.enable_upload:
            log.log(VERBOSE, "[%s] Upload disabled — skipping UPLOADING step.", state.subject_id)
            return

        if is_paused(self.cfg.pause_file):
            since = paused_since(self.cfg.pause_file)
            log.info(
                "[%s] Upload submissions PAUSED%s — skipping cycle.  "
                "Run `conductor-status` and choose Resume to re-enable.",
                state.subject_id,
                f" since {since}" if since else "",
            )
            return

        with state.lock:
            metadata_ready = state.metadata_present or state.metadata_generated
            run_dir = state.run_dir or resolve_run_dir(state.data_root)
            upload_started = state.upload_started

        if not metadata_ready:
            log.info("[%s] Skipping upload — metadata not yet ready.", state.subject_id)
            return

        # Gate the start job on >=3 available chunks
        if not upload_started:
            n_chunks = _count_local_chunks(run_dir, self.cfg.chunk_camera_folder)
            if n_chunks < 3:
                log.info(
                    "[%s] Waiting for ≥3 chunks before start upload (%d so far).",
                    state.subject_id,
                    n_chunks,
                )
                return

        with state.lock:
            state.phase = SessionPhase.UPLOADING

        is_start = not upload_started
        acq_dt = _parse_session_datetime(state.session_datetime)

        # ── Load / create the per-session upload sidecar ──────────────────────
        sidecar = UploadSidecar(
            run_dir=run_dir,
            subject_id=state.subject_id,
            session_ts=state.session_datetime,
            delete_enabled=self.cfg.delete_after_upload,
        )

        # ── State recovery from sidecar ───────────────────────────────────────
        # The sidecar is authoritative for upload progress.  If conductor_state.json
        # is absent or stale (e.g. after a crash or machine move), the sidecar
        # tells us whether the start job was already submitted so we don't repeat it.
        recovered = sidecar.recover_upload_state()
        if recovered["upload_started"] and not upload_started:
            log.info(
                "[%s] Recovering upload_started=True from sidecar "
                "(state file missing or stale).",
                state.subject_id,
            )
            with state.lock:
                state.upload_started = True
            upload_started = True
            is_start = False  # recalculate — start job was already done
        if recovered["last_upload_run"] is not None:
            with state.lock:
                if state.last_upload_run is None:
                    state.last_upload_run = recovered["last_upload_run"]

        skip_chunks = sidecar.chunks_to_skip(self.cfg.upload_max_retries)

        log.info(
            "[%s] UPLOADING — submitting %s job …",
            state.subject_id,
            "chronic_ephys_start" if is_start else "chronic_ephys_chunk",
        )
        log.log(
            VERBOSE,
            "[%s] Upload config — bucket=%s  acq_dt=%s  batch_size=%d  "
            "ignore_last=%d  dry_run=%s  max_retries=%d  sidecar_skip=%d  source=%s",
            state.subject_id,
            self.cfg.s3_bucket,
            acq_dt.isoformat(),
            self.cfg.upload_batch_size,
            self.cfg.num_last_chunks_to_ignore,
            self.cfg.dry_run,
            self.cfg.upload_max_retries,
            len(skip_chunks),
            run_dir,
        )

        result = run_upload_cycle(
            source_directory=str(run_dir),
            subject_id=state.subject_id,
            acq_datetime=acq_dt,
            project_name=self.cfg.project_name,
            contact_email=self.cfg.contact_email,
            s3_bucket=self.cfg.s3_bucket,
            batch_size=self.cfg.upload_batch_size,
            dry_run=self.cfg.dry_run,
            num_of_last_chunks_to_ignore=self.cfg.num_last_chunks_to_ignore,
            is_start_job=is_start,
            skip_chunks=skip_chunks,
        )

        # Record submission in the sidecar
        for chunk_ts in result.submitted_chunks:
            sidecar.mark_submitted(chunk_ts, self.cfg.upload_max_retries)

        # Compute the S3 prefix once — used for both confirmation and deletion
        s3_prefix: str | None = compute_s3_prefix(
            source_directory=str(run_dir),
            subject_id=state.subject_id,
            acq_datetime=acq_dt,
            s3_bucket=self.cfg.s3_bucket,
        )

        # Confirm any previously-submitted chunks that are now visible in S3
        pending_in_sidecar = sidecar.submitted_chunk_timestamps()
        if pending_in_sidecar and s3_prefix:
            confirmed_in_s3 = list_confirmed_s3_chunks(self.cfg.s3_bucket, s3_prefix)
            for chunk_ts in pending_in_sidecar & confirmed_in_s3:
                sidecar.mark_confirmed(chunk_ts)
                log.log(
                    VERBOSE,
                    "[%s] Chunk %s confirmed in S3.",
                    state.subject_id,
                    chunk_ts,
                )

        with state.lock:
            if result.success:
                state.upload_started = True
                state.last_upload_run = datetime.now()

        if result.success:
            log.info(
                "[%s] Upload cycle completed successfully (%s).  "
                "Submitted %d chunk(s) this cycle.",
                state.subject_id,
                "start job" if is_start else "chunk job",
                len(result.submitted_chunks),
            )
        elif result.start_job_not_in_docdb:
            # The chunk job was blocked because the start-job DocDB record
            # doesn't exist.  This means a previously-submitted start job never
            # completed (e.g. it failed on the transfer service side).  Reset
            # upload_started and clear any "submitted" sidecar entries so the
            # next cycle re-submits the start job with a clean slate.
            with state.lock:
                state.upload_started = False
            reset_count = sidecar.reset_submitted_chunks()
            log.warning(
                "[%s] Chunk job blocked — start job not yet in DocDB.  "
                "Reset upload_started and cleared %d submitted sidecar record(s); "
                "next cycle will re-submit the start job.",
                state.subject_id,
                reset_count,
            )
        else:
            log.warning("[%s] Upload cycle skipped or failed.", state.subject_id)

        # Optionally delete large local files after confirmed S3 upload
        if result.success and self.cfg.delete_after_upload and upload_started:
            log.log(
                VERBOSE,
                "[%s] DELETE_AFTER_UPLOAD — querying S3 then removing confirmed local files …",
                state.subject_id,
            )
            delete_local_files_after_upload(
                data_root=run_dir,
                keep_patterns=self.cfg.keep_local_patterns,
                s3_bucket=self.cfg.s3_bucket,
                subject_id=state.subject_id,
                acq_datetime=acq_dt,
            )
            # Mark deleted chunks in the sidecar (all confirmed-in-S3 chunks
            # that have delete_state pending are candidates; deletion is
            # handled by delete_local_files_after_upload which operates at
            # the file level — we mark the sidecar at chunk granularity here)
            if s3_prefix is not None:
                confirmed_for_deletion = list_confirmed_s3_chunks(
                    self.cfg.s3_bucket, s3_prefix
                )
                for chunk_ts in confirmed_for_deletion:
                    # Only transition pending→success; already-deleted chunks
                    # have delete_state "success" and mark_deleted is a no-op.
                    sidecar.mark_deleted(chunk_ts)

    # ── State persistence ─────────────────────────────────────────────────────

    def _save_state(self) -> None:
        """Write all session states to :attr:`cfg.state_file` as JSON."""
        if self.cfg.state_file is None:
            return
        with self._registry_lock:
            data = {k: v.to_dict() for k, v in self._sessions.items()}
        try:
            self.cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.cfg.state_file.write_text(
                json.dumps(data, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.warning("Could not save state to %s: %s", self.cfg.state_file, exc)

    def _load_state(self) -> None:
        """Restore session states from :attr:`cfg.state_file` if it exists."""
        if self.cfg.state_file is None or not self.cfg.state_file.exists():
            return
        try:
            data = json.loads(
                self.cfg.state_file.read_text(encoding="utf-8")
            )
            with self._registry_lock:
                for k, v in data.items():
                    self._sessions[k] = SessionState.from_dict(v)
            log.info(
                "Restored %d session(s) from %s.",
                len(self._sessions),
                self.cfg.state_file,
            )
        except Exception as exc:
            log.warning(
                "Could not load state from %s: %s", self.cfg.state_file, exc
            )

    # ── Signal handling ───────────────────────────────────────────────────────

    def _handle_signal(self, signum: int, frame) -> None:  # noqa: ANN001
        log.info("Received signal %d — shutting down gracefully ...", signum)
        self.stop()
