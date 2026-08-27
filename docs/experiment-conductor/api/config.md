# config

Configuration dataclass and loader.

Merges values from a `.env` file (loaded via `python-dotenv`) with optional
CLI overrides.  CLI flags always win.  See `.env.example` in the package
source for the full variable reference.

::: experiment_conductor.config
