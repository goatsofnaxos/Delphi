# conductor

!!! warning "Deprecated"
    `conductor.py` has been replaced by [`session_manager`](session_manager.md)
    and [`cli`](cli.md).  This page is retained for reference only.

The old single-session conductor tied the launcher, delphi-data pipeline,
metadata generator, and S3 uploader into a linear lifecycle
(`LAUNCHING → RUNNING → ENDING → DONE`).

The refactored conductor uses a **multi-session manager** that watches a
network drive for new sessions and processes them concurrently on a
configurable cadence.  See [session_manager](session_manager.md) and the
[Overview](../index.md) for the new architecture.
