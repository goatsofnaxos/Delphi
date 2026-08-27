# session_manager

Multi-session orchestrator and main polling loop.

`SessionManager` is the central controller.  It discovers new sessions via the
watcher, schedules cadence cycles, runs each session through its five-step
pipeline (consolidate → metadata → build → noise floor → upload), and persists
state to disk so work survives restarts.

::: experiment_conductor.session_manager
