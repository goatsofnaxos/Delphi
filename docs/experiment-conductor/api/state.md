# state

Thread-safe experiment state.  The `Phase` enum and `ConductorState` dataclass
are the single source of truth for lifecycle transitions across threads.

::: experiment_conductor.state
