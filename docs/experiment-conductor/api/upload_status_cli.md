# upload_status_cli

Interactive upload-status viewer — the `conductor-status` command.

```bash
conductor-status [--env-file .env] [--state-file PATH] [--pause-file PATH]
```

Reads the conductor state file to enumerate active datasets, then reads each
session's `.upload_history.json` sidecar.  Presents a numbered menu for
selecting a dataset and a sub-menu for viewing chunk history.

## Main menu

The main menu always shows the current submission state at the top:

```
╔══════════════════════════════════════════════════╗
║   Experiment Conductor — Upload Status Viewer    ║
╚══════════════════════════════════════════════════╝

  ▶  Submissions ACTIVE

  1. 842456  2026-09-01T10-00-00
     \\server\share\842456\2026-09-01T10-00-00
     3 chunk(s): 2 success, 1 submitted

  p. Pause upload submissions
  q. Quit
```

| Key | Action |
|-----|--------|
| `1`–`N` | Open the dataset detail sub-menu |
| `p` | Pause upload submissions (creates the pause sentinel file) |
| `r` | Resume upload submissions (removes the pause sentinel file) |
| `q` | Quit |

## Dataset sub-menu

| Key | View |
|-----|------|
| `1` | Full chunk history (all states) |
| `2` | In-progress chunks (`submitted` / `pending`) |
| `3` | Failed / skipped chunks |
| `b` | Back to dataset list |
| `q` | Quit |

## Pause / resume

`conductor-status` is the primary interface for pausing and resuming upload
submissions.  Pressing **`p`** creates a sentinel lock file; pressing **`r`**
removes it.  The running conductor checks for the file at the start of every
upload cycle and skips submissions while it exists.  See
[pause_control](pause_control.md) for the underlying mechanism.

::: experiment_conductor.upload_status_cli
