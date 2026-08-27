# state

!!! warning "Deprecated"
    `state.py` has been replaced by [`session`](session.md).
    This page is retained for reference only.

`ConductorState` and the `Phase` enum have been replaced by
[`SessionState`](session.md#experiment_conductor.session.SessionState) and
[`SessionPhase`](session.md#experiment_conductor.session.SessionPhase), which
track the lifecycle of individual sessions rather than a single global state.
