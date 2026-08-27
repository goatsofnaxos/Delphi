# session

Per-session state for the experiment conductor.

`SessionPhase` enumerates the lifecycle phases a session passes through.
`SessionState` holds all mutable state for one acquisition session; it is
thread-safe via an embedded `threading.Lock` and can be serialised to/from
a plain dict for persistence.

::: experiment_conductor.session
