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
import re
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ConductorConfig
from .metadata_bridge import check_metadata_present, generate_metadata, update_acquisition_end_time
from .noise_floor import estimate_noise_floor
from .pipeline_bridge import (
    has_delphi_controller_file,
    move_delphi_metadata,
    resolve_run_dir,
    run_consolidation,
    run_pipeline,
)
from .session import SessionPhase, SessionState
from .uploader_bridge import (
    UPLOAD_STOP_EVENT,
    delete_local_files_after_upload,
    run_upload_cycle,
    stop_upload,
)
from .watcher import discover_sessions

log = logging.getLogger(__name__)

_CHUNK_COUNT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")


def _count_local_chunks(run_dir: Path, folder: str) -> int:
    """Count timestamp-named directories in the camera-video folder."""
    target = run_dir / folder
    if not target.exists():
        return 0
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
            "Registered session: subject=%s  ts=%s", subject_id, data_root.name
        )
        return True

    @property
    def sessions(self) -> list[SessionState]:
        """Snapshot of the current session registry."""
        with self._registry_lock:
            return list(self._sessions.values())

    def run(self) -> None:
        """Main polling loop — runs until SIGINT/SIGTERM or :meth:`stop`.

        Installs signal handlers for graceful shutdown.
        """
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        log.info(
            "Session manager started. "
            "Watching %d path(s). Poll interval: %.0f s. Cadence: %d min.",
            len(self.cfg.watch_paths),
            self.cfg.poll_interval_s,
            self.cfg.pipeline_cadence_minutes,
        )
        for p in self.cfg.watch_paths:
            log.info("  Watch path: %s", p)

        while not self._stop_event.is_set():
            try:
                self._scan_and_register()
                self._process_due_sessions()
                self._save_state()
            except Exception:
                log.exception("Unexpected error in main loop.")
            self._stop_event.wait(self.cfg.poll_interval_s)

        log.info("Session manager stopped.")

    def stop(self) -> None:
        """Signal the main loop to exit after the current iteration."""
        self._stop_event.set()
        stop_upload()

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _scan_and_register(self) -> None:
        found = discover_sessions(self.cfg.watch_paths)
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
        log.info("Processing %d due session(s).", len(due))
        # At most 4 sessions in parallel; each session's steps are sequential
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(self._process_one, s): s for s in due}
            for future in as_completed(futures):
                state = futures[future]
                try:
                    future.result()
                except Exception:
                    log.exception(
                        "Unexpected error processing session %s.", state.data_root
                    )

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
                else:
                    log.warning(
                        "Session %s error %d/%d: %s",
                        state.data_root.name,
                        state.consecutive_errors,
                        self.cfg.max_consecutive_errors,
                        exc,
                    )
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

        # Always consolidate — new Bonsai restarts may have created extra run dirs
        log.info("[%s] Consolidating run directories.", state.subject_id)
        ok = run_consolidation(state.data_root)

        # Resolve the canonical run dir after consolidation
        run_dir = resolve_run_dir(state.data_root)

        if ok and not already_done:
            move_delphi_metadata(run_dir)
        elif ok:
            # Re-check in case new metadata files appeared
            move_delphi_metadata(run_dir)

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

        present = check_metadata_present(run_dir)
        with state.lock:
            state.metadata_present = present

        if present:
            log.debug("[%s] Metadata already present.", state.subject_id)
            return

        if not self.cfg.enable_metadata:
            log.info("[%s] Metadata missing but generation disabled.", state.subject_id)
            return

        with state.lock:
            state.phase = SessionPhase.METADATA_GENERATING
        log.info("[%s] Generating AIND metadata.", state.subject_id)
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
        with state.lock:
            state.metadata_generated = ok
            if ok:
                state.metadata_present = True

    def _step_pipeline(self, state: SessionState) -> None:
        """Build/append the delphi-data behaviour dataset if applicable."""
        if not self.cfg.enable_pipeline:
            return

        with state.lock:
            run_dir = state.run_dir or resolve_run_dir(state.data_root)
            already_built = state.dataset_built

        # Only run if there is actual Delphi controller data
        if not has_delphi_controller_file(run_dir):
            log.debug(
                "[%s] No DelphiController file found; skipping pipeline.",
                state.subject_id,
            )
            return

        with state.lock:
            state.phase = SessionPhase.BUILDING
        log.info("[%s] Running delphi-data pipeline.", state.subject_id)

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
        with state.lock:
            if ok:
                state.dataset_built = True

    def _step_noise_floor(self, state: SessionState) -> None:
        """Estimate the ephys noise floor (once per session)."""
        if not self.cfg.enable_noise_floor:
            return

        with state.lock:
            if state.noise_floor_estimated:
                return
            run_dir = state.run_dir or resolve_run_dir(state.data_root)
            state.phase = SessionPhase.NOISE_FLOOR

        log.info("[%s] Estimating noise floor.", state.subject_id)
        result = estimate_noise_floor(
            run_dir,
            n_seconds=self.cfg.noise_floor_n_seconds,
            max_channels=self.cfg.noise_floor_max_channels,
        )
        with state.lock:
            if result is not None:
                state.noise_floor_estimated = True

    def _step_upload(self, state: SessionState) -> None:
        """Submit new chunk upload jobs to the AIND transfer service."""
        if not self.cfg.enable_upload:
            return

        with state.lock:
            metadata_ready = state.metadata_present or state.metadata_generated
            run_dir = state.run_dir or resolve_run_dir(state.data_root)
            upload_started = state.upload_started

        if not metadata_ready:
            log.info("[%s] Skipping upload: metadata not yet ready.", state.subject_id)
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

        log.info("[%s] Running upload cycle (is_start=%s).", state.subject_id, not upload_started)
        acq_dt = _parse_session_datetime(state.session_datetime)

        ok = run_upload_cycle(
            source_directory=str(run_dir),
            subject_id=state.subject_id,
            acq_datetime=acq_dt,
            project_name=self.cfg.project_name,
            contact_email=self.cfg.contact_email,
            s3_bucket=self.cfg.s3_bucket,
            batch_size=self.cfg.upload_batch_size,
            dry_run=self.cfg.dry_run,
            num_of_last_chunks_to_ignore=self.cfg.num_last_chunks_to_ignore,
            is_start_job=not upload_started,
        )
        with state.lock:
            if ok:
                state.upload_started = True
                state.last_upload_run = datetime.now()

        # Optionally delete large local files after confirmed S3 upload
        if ok and self.cfg.delete_after_upload and upload_started:
            delete_local_files_after_upload(
                data_root=run_dir,
                keep_patterns=self.cfg.keep_local_patterns,
                s3_bucket=self.cfg.s3_bucket,
                subject_id=state.subject_id,
                acq_datetime=acq_dt,
            )

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
