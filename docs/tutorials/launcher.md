# Tutorial: launcher

The launcher is an interactive terminal tool that starts Bonsai workflows for one or more experiments, manages pirouette recovery files, and records a session config that can be replayed later.

---

## Prerequisites

- Bonsai installed and the relevant workflows present under the paths defined in `launcher_config.json`
- `uv` available in your shell, or the launcher dependencies installed in your Python environment

```bash
cd src/launcher
uv sync          # or: pip install -e .
```

---

## Step 1 — Run the launcher interactively

```bash
cd src/launcher
uv run launcher.py
```

The launcher presents a menu of the experiments defined in `launcher_config.json`.  Select one with the number key.

```
Available experiments
  1. delphi
  2. pirouette
  3. delphi_pirouette
Enter experiment number (or 'all'):
```

---

## Step 2 — Enter subject and experimenter

After selecting an experiment, the launcher prompts for:

- **Subject ID** — type a new ID or press Enter to select from recently used subjects (stored in `known_subjects.json`).
- **Experimenter** — your name or initials.

```
Subject ID [last: 842456]: 842456
Experimenter: B. Pratt
```

---

## Step 3 — Confirm the parameter summary

Before launching Bonsai, the launcher displays every property that will be passed to the workflow.  Review the list, then press Enter to proceed.

```
─────────── Session summary ───────────
  SubjectID          842456
  Experimenter       B. Pratt
  RigName            delphi-rig-0
  ...
Press Enter to launch (or Ctrl-C to abort):
```

---

## Step 4 — Bonsai launches

The launcher opens each workflow in a separate Bonsai process.  For `delphi_pirouette` sessions, both the Delphi and Pirouette workflows start in order.  The launcher blocks until all processes exit.

If pirouette recovery files are configured, they are created (if missing) and zeroed before each launch and again on exit.

---

## Step 5 — Replay a session with a profile

Interactive sessions are auto-saved to `experiment_configs/` as JSON profiles.  Subsequent runs can skip most prompts:

```bash
uv run launcher.py --experiment delphi_pirouette_experiment
```

The launcher still asks for Subject ID and Experimenter, but all other settings come from the saved profile.

You can also point to a file directly:

```bash
uv run launcher.py --experiment /path/to/my_profile.json
```

---

## Tips

- **Relative paths in profiles** are resolved relative to the profile file, not the working directory.
- To run **all experiments in sequence** (one session per experiment), type `all` at the experiment menu.
- The Lifealert integration (start/stop recording) is available from a sub-menu that appears after Bonsai launches.
