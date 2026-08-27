# cli

Command-line entry point for the experiment conductor.

Invoked as the `conductor` command (configured in `pyproject.toml`) or via
`python -m experiment_conductor`.

## Usage

```bash
conductor [--watch-paths PATH,...] [--add-session PATH] [--log-level LEVEL]
          [--dry-run] [--state-file PATH] [--env-file PATH]
```

::: experiment_conductor.cli
