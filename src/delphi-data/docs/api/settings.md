# settings

Environment-based configuration for the ``delphi-data`` package.

Settings are loaded from a ``.env`` file (searched upward from the current
working directory) and from shell environment variables.  Command-line
arguments always take precedence.

## Priority order

```
CLI argument  >  shell env var  >  .env file  >  code default
```

## Quick start

Copy `.env.example` from the repository root to `.env` and uncomment the
variables you want to change:

```bash
cp .env.example .env
# edit .env
```

Then run any command normally — settings are picked up automatically:

```bash
delphi-data snapshot --experiment bonhoeffer --data-root /path/to/run
# uses DELPHI_TAU, DELPHI_CAMERA_FPS, etc. from .env
```

A CLI flag always wins:

```bash
delphi-data snapshot --experiment bonhoeffer --data-root /path/to/run --tau 300
# --tau 300 overrides DELPHI_TAU in .env
```

---

## Module reference

::: delphi_data.settings._Settings
    options:
      filters: []

::: delphi_data.settings.settings

::: delphi_data.settings.reload
