# launcher

Interactive multi-experiment Bonsai workflow launcher for Delphi and Pirouette sessions.

## Usage

```bash
# Interactive
uv run launcher.py

# From a saved profile
uv run launcher.py --experiment delphi_experiment
uv run launcher.py --experiment pirouette_experiment
uv run launcher.py --experiment delphi_pirouette_experiment
```

## Features

- Experiment profiles (JSON/YAML) with relative-path support
- Subject ID recall across runs (`known_subjects.json`)
- Auto-saves each interactive session as a reusable profile (`generated_configs/`)
- Pirouette recovery-file reset on every launch
- Lifealert start/stop control menu
- Sequential or parallel multi-session launches

## Installation

```bash
uv venv
uv pip install -e .
```
