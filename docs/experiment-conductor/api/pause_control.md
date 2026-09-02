# pause_control

Pause-file controller for upload job submissions.

The pause mechanism uses a sentinel lock file on disk.  Any process that can
reach the same filesystem path — typically `conductor-status` running on the
same machine — can create or remove the file to pause or resume the
conductor's upload step without restarting it.

The conductor checks for the file at the start of every upload cycle, so the
change takes effect within one poll interval (`CONDUCTOR_POLL_INTERVAL_S`,
default 60 s).

## How it works

```
conductor-status (viewer)           conductor (main process)
─────────────────────────           ─────────────────────────
Press p                             
  → pause_submissions()             
      → creates conductor_pause.lock

                                    next upload cycle:
                                      is_paused() → True
                                      → log warning, skip cycle

Press r
  → resume_submissions()
      → deletes conductor_pause.lock

                                    next upload cycle:
                                      is_paused() → False
                                      → submit chunks normally
```

## Default location

The pause file defaults to `conductor_pause.lock` in the same directory as
`CONDUCTOR_STATE_FILE`.  Both `conductor` and `conductor-status` derive the
same default path so they automatically agree without explicit configuration.

Override with `CONDUCTOR_PAUSE_FILE` or `--pause-file`.

## Manual control

The pause file can also be managed directly without `conductor-status`:

```bash
# Pause
touch conductor_pause.lock

# Resume
del conductor_pause.lock   # Windows
rm conductor_pause.lock    # Unix
```

::: experiment_conductor.pause_control
