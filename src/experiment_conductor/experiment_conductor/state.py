"""Thread-safe shared state for the experiment conductor."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Phase(Enum):
    """Lifecycle phase of the experiment conductor."""

    LAUNCHING = "launching"
    RUNNING = "running"
    ENDING = "ending"
    DONE = "done"


@dataclass
class ConductorState:
    """Mutable shared state updated by the conductor and its background threads.

    All writes should use the ``lock`` context manager to remain thread-safe.

    Parameters
    ----------
    phase : Phase
        Current lifecycle phase.
    first_consolidation_done : bool
        True once the first successful delphi-data consolidation has run.
    delphi_metadata_moved : bool
        True once HardwareSettings/RuleSettings have been moved to behavior/.
    metadata_generated : bool
        True once AIND metadata has been generated to data_root/metadata/.
    upload_started : bool
        True once the ``chronic_ephys_start`` job has been submitted.
    upload_paused : bool
        True while upload batches are paused by the user.
    pipeline_enabled : bool
        Whether the delphi-data pipeline step runs each cycle.  Initialised
        from ``ENABLE_PIPELINE`` in the config; toggled at runtime by hotkey.
    metadata_enabled : bool
        Whether AIND metadata generation runs each cycle.  Initialised from
        ``ENABLE_METADATA`` in the config; toggled at runtime by hotkey.
    upload_enabled : bool
        Whether the upload step runs each cycle.  Initialised from
        ``ENABLE_UPLOAD`` in the config; toggled at runtime by hotkey.
    experiment_end_time : Optional[datetime]
        UTC time when the user signalled experiment end.
    last_pipeline_run : Optional[datetime]
        UTC time of the most recent successful pipeline cycle.
    last_upload_run : Optional[datetime]
        UTC time of the most recent upload submission cycle.
    end_experiment_event : threading.Event
        Set by the hotkey listener when the user signals experiment end.
    """

    phase: Phase = Phase.LAUNCHING
    first_consolidation_done: bool = False
    delphi_metadata_moved: bool = False
    metadata_generated: bool = False
    upload_started: bool = False
    upload_paused: bool = False
    pipeline_enabled: bool = True
    metadata_enabled: bool = True
    upload_enabled: bool = True
    experiment_end_time: Optional[datetime] = None
    last_pipeline_run: Optional[datetime] = None
    last_upload_run: Optional[datetime] = None
    end_experiment_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
