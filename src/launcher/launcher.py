"""
Experiment Launcher for Information Foraging tasks

What's special in this build
---------------------------
• DO NOT pass 'LifealertRoot' to Bonsai: it is filtered out at launch time.
• Pre-launch SUMMARY excludes LifealertRoot (so it mirrors only what Bonsai receives).
• All other features retained:
  - Profiles (--experiment <NAME|FILE>) to skip prompts (except Experimenter & Subject).
  - Multi-experiment & multi-session.
  - "All experiments" mode (one session per experiment; ask Experimenter & Subject once).
  - Per-session parameter overrides (session-only), per-session file copies (Option B).
  - Pirouette recovery files under <root>/src are created if missing and zeroed on launch and on exit.
  - Lifealert menu after Bonsai launch: start/stop, choose directories from a persisted list.
  - Subject ID recall from known_subjects.json (remembered across runs).
  - Interactive sessions are auto-saved as reusable configs in generated_configs/.
  - Profile files may use relative paths (resolved relative to the profile's own directory).

Run examples
------------
Interactive:
    uv run launcher.py

Using a profile by NAME (resolves ./experiments/<NAME>.json|.yaml|.yml or next to launcher.py):
    uv run launcher.py --experiment delphi_experiment

Using a profile by FILE PATH:
    uv run launcher.py --experiment C:/path/to/my_profile.json
"""

import argparse
import json
import os
import re
import sys
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime, timezone

import psutil

# Optional YAML support (if PyYAML is available)
try:
    import yaml  # type: ignore
except Exception:
    yaml = None

CONFIG_PATH = Path(__file__).with_name("launcher_config.json")
EXPERIMENTS_DIR = Path(__file__).with_name("experiment_configs")
SESSIONS_DIR = Path(__file__).with_name("sessions")
GENERATED_CONFIGS_DIR = Path(__file__).with_name("experiment_configs")
SUBJECTS_CONFIG_PATH = Path(__file__).with_name("known_subjects.json")

# Track per-session files and temp files to clean up at the end
TEMP_SESSION_FILES: List[str] = []
# Pirouette recovery files — zeroed (not deleted) on exit
PIROUETTE_RECOVERY_FILES: List[str] = []
# Track lifealert background processes to terminate on exit
LIFEALERT_PROCS: List[subprocess.Popen] = []
# Track launched Bonsai processes
WORKFLOW_PROCS: List[subprocess.Popen] = []
# Original XML content for .bonsai files patched in-place, keyed by absolute path
BONSAI_ORIGINALS: Dict[str, str] = {}


# -------------------------------
# CLI
# -------------------------------
def parse_args():
    """Parse command-line arguments for the launcher.

    Returns
    -------
    argparse.Namespace
        Parsed argument namespace. Key attribute: ``experiment`` (str or None).
    """
    p = argparse.ArgumentParser(description="Bonsai multi-experiment launcher")
    p.add_argument(
        "--experiment",
        "-e",
        help="Profile by NAME (resolved in ./experiments or next to launcher.py) or a .json/.yaml/.yml FILE path. NONE by default.",
        default=None,
    )
    return p.parse_args()


# -------------------------------
# I/O & basic helpers
# -------------------------------
def load_config() -> Dict[str, Any]:
    """Load and validate the launcher configuration from disk.

    Returns
    -------
    dict
        Parsed configuration dictionary with at least an ``EXPERIMENTS`` key.

    Raises
    ------
    SystemExit
        If the config file is missing, not a JSON object, or has no valid
        ``EXPERIMENTS`` map.
    """
    if not CONFIG_PATH.exists():
        print(f"Configuration file not found: {CONFIG_PATH}")
        print("Please create 'launcher_config.json' next to this script.")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        print("Invalid configuration: expected a top-level JSON object.")
        sys.exit(1)
    if "EXPERIMENTS" not in data or not isinstance(data["EXPERIMENTS"], dict):
        # Backward compatibility: wrap flat maps
        if any(
            isinstance(v, dict) and "workflows" in v
            for v in data.values()
            if isinstance(v, dict)
        ):
            experiments = {k: v for k, v in data.items() if isinstance(v, dict)}
            data = {"EXPERIMENTS": experiments}
        else:
            print("Missing 'EXPERIMENTS' map in configuration.")
            sys.exit(1)
    return data


def save_config(cfg: Dict[str, Any]) -> None:
    """Persist the launcher configuration to disk.

    Parameters
    ----------
    cfg : dict
        Configuration dictionary to serialise as JSON.

    Returns
    -------
    None
    """
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def norm(p: str) -> str:
    """Normalise a filesystem path (expand ``~`` and collapse separators).

    Parameters
    ----------
    p : str
        Raw path string.

    Returns
    -------
    str
        Normalised absolute-style path string.
    """
    return os.path.normpath(os.path.expanduser(p))


def exists_path(p: str) -> bool:
    """Return True if *p* (after normalisation) points to an existing filesystem entry.

    Parameters
    ----------
    p : str
        Path string to test.

    Returns
    -------
    bool
        ``True`` if the path exists, ``False`` otherwise.
    """
    return os.path.exists(norm(p))


def prompt(prompt_text: str, default: str = "") -> str:
    """Prompt the user for a string value with an optional default.

    Parameters
    ----------
    prompt_text : str
        Text shown before the input cursor.
    default : str, optional
        Value returned when the user submits an empty line.

    Returns
    -------
    str
        The entered value, or *default* if the user pressed Enter.
    """
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt_text}{suffix}: ").strip()
    return val or default


def prompt_yes_no(prompt_text: str, default_yes: bool = True) -> bool:
    """Prompt the user for a yes/no confirmation.

    Parameters
    ----------
    prompt_text : str
        Question to display.
    default_yes : bool, optional
        If ``True`` (default), pressing Enter returns ``True``.

    Returns
    -------
    bool
        ``True`` for yes, ``False`` for no.
    """
    default = "Y/n" if default_yes else "y/N"
    while True:
        ans = input(f"{prompt_text} ({default}): ").strip().lower()
        if not ans:
            return default_yes
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("Please answer y or n.")


def is_probably_path(value: str) -> bool:
    """Heuristically decide whether *value* looks like a filesystem path.

    Parameters
    ----------
    value : str
        The string to test.

    Returns
    -------
    bool
        ``True`` if the value appears to be a path (contains slashes or has a
        recognised file extension), ``False`` otherwise.
    """
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v:
        return False
    if "://" in v or v.startswith("@"):
        return False
    looks_like_path = (
        "\\" in v
        or "/" in v
        or os.path.splitext(v)[1].lower()
        in {".json", ".yml", ".yaml", ".bonsai", ".csv", ".txt", ".exe"}
    )
    return looks_like_path


def make_relative_if_under_root(path_abs: str, root_abs: str) -> str:
    """Return a forward-slash relative path if *path_abs* lives under *root_abs*.

    Parameters
    ----------
    path_abs : str
        Absolute path to the target file or directory.
    root_abs : str
        Absolute path to the root directory.

    Returns
    -------
    str
        Relative path (forward slashes) when *path_abs* is under *root_abs*,
        otherwise the normalised absolute path.
    """
    path_abs = norm(path_abs)
    root_abs = norm(root_abs)
    try:
        rel = os.path.relpath(path_abs, root_abs)
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except Exception:
        pass
    return path_abs.replace("\\", "/")


# -------------------------------
# Session helpers
# -------------------------------
def utc_timestamp_for_session() -> str:
    """Generate a UTC timestamp string suitable for use in session IDs and filenames.

    Returns
    -------
    str
        Timestamp in the format ``YYYY-MM-DDTHH-MM-SS`` (e.g. ``2025-05-09T18-46-21``).
    """
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H-%M-%S"
    )  # e.g., 2025-05-09T18-46-21


def build_session(exp_key: str, experimenter_default: str = "") -> Dict[str, Any]:
    """Interactively build a session dict by prompting for experimenter and subject.

    Parameters
    ----------
    exp_key : str
        Experiment key used to name the session.
    experimenter_default : str, optional
        Pre-filled default for the experimenter prompt.

    Returns
    -------
    dict
        Session dictionary as returned by :func:`build_session_with_values`.
    """
    experimenter = prompt("Experimenter name", default=experimenter_default)
    subject_id = prompt_subject_id()
    return build_session_with_values(exp_key, experimenter, subject_id)


def build_session_with_values(
    exp_key: str, experimenter: str, subject_id: str, start_utc: str = ""
) -> Dict[str, Any]:
    """Build a session dict from explicit values (no user prompts).

    Parameters
    ----------
    exp_key : str
        Experiment key used to derive ``experiment_name``.
    experimenter : str
        Experimenter name.
    subject_id : str
        Subject identifier.
    start_utc : str, optional
        Pre-set UTC start timestamp. Generated automatically when empty.

    Returns
    -------
    dict
        Session dictionary with keys ``session_id``, ``experimenter``,
        ``subject_id``, ``experiment_name``, ``start_utc``, and
        ``experiment_key``.
    """
    experiment_name = exp_key.lower()
    if not start_utc:
        start_utc = utc_timestamp_for_session()
    session_id = f"{experiment_name}_{subject_id}_{start_utc}".replace(" ", "_")
    return {
        "session_id": session_id,
        "experimenter": experimenter,
        "subject_id": subject_id,
        "experiment_name": experiment_name,
        "start_utc": start_utc,
        "experiment_key": exp_key,
    }


def save_session_record(session: Dict[str, Any]) -> Path:
    """Write a session dictionary to a JSON file in the sessions directory.

    Parameters
    ----------
    session : dict
        Session dictionary as returned by :func:`build_session_with_values`.

    Returns
    -------
    Path
        Path to the written file, or an empty ``Path()`` on failure.
    """
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        safe_exp = session.get("experiment_name", "exp")
        safe_subj = (session.get("subject_id", "subj") or "").replace(" ", "_")
        fp = (
            SESSIONS_DIR / f"session_{session['start_utc']}_{safe_exp}_{safe_subj}.json"
        )
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2, ensure_ascii=False)
        TEMP_SESSION_FILES.append(str(fp))
        return fp
    except Exception as e:
        print(f"Warning: Could not write session log: {e}")
        return Path()


def load_known_subjects() -> List[str]:
    """Load the list of previously used subject IDs from disk.

    Returns
    -------
    list of str
        Subject IDs in the order they were first entered, or an empty
        list if the file does not exist or cannot be read.
    """
    if not SUBJECTS_CONFIG_PATH.exists():
        return []
    try:
        with open(SUBJECTS_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(s) for s in data if s]
    except Exception:
        pass
    return []


def save_known_subjects(subjects: List[str]) -> None:
    """Persist the list of known subject IDs to disk.

    Parameters
    ----------
    subjects : list of str
        Subject IDs to save.

    Returns
    -------
    None
    """
    try:
        with open(SUBJECTS_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(subjects, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: could not save known_subjects.json: {e}")


def prompt_subject_id(default: str = "") -> str:
    """Prompt the user to select or enter a subject ID, with recall from previous sessions.

    Displays a numbered list of previously used subject IDs loaded from
    ``known_subjects.json``. The user may select by number or type a new
    value. New values are appended to ``known_subjects.json`` automatically.

    Parameters
    ----------
    default : str, optional
        Pre-filled default value shown in brackets.

    Returns
    -------
    str
        The chosen or newly entered subject ID (never empty; re-prompts
        until a non-empty value is provided).
    """
    known = load_known_subjects()
    while True:
        print("\nSubject ID:")
        if known:
            for i, s in enumerate(known, start=1):
                marker = " [default]" if s == default else ""
                print(f"  {i}. {s}{marker}")
        suffix = f" [default: {default}]" if default else ""
        raw = input(f"Select by number or type new ID{suffix}: ").strip()

        if not raw and default:
            return default

        # Only treat as a menu index if it matches a listed option exactly
        if raw.isdigit() and 1 <= int(raw) <= len(known):
            return known[int(raw) - 1]

        # Anything else (including numeric subject IDs out of range) is a new ID
        sid = raw
        if not sid:
            print("Subject ID cannot be empty. Please try again.")
            continue

        if sid not in known:
            known.append(sid)
            save_known_subjects(known)
            print(f"  Saved '{sid}' to known subjects.")
        return sid


def save_generated_config(
    selected_keys: List[str],
    prepared_per_experiment: Dict[str, Dict[str, Any]],
    sessions_to_launch: List[Dict[str, Any]],
    auto_start: bool,
    parallel: bool,
) -> Path:
    """Save the current interactive session parameters as a reusable profile file.

    The generated config mirrors the structure of ``delphi_experiment.json``
    and can be passed directly to ``--experiment`` on the next run.

    Parameters
    ----------
    selected_keys : list of str
        Experiment keys that were configured for this run.
    prepared_per_experiment : dict
        Mapping of exp_key to a dict containing ``root``, ``workflow``,
        and ``base_params`` as set up during configuration.
    sessions_to_launch : list of dict
        All assembled session dicts for this run (used to derive
        ``session_count`` per experiment).
    auto_start : bool
        Whether Bonsai was set to auto-start.
    parallel : bool
        Whether sessions were set to run in parallel.

    Returns
    -------
    Path
        Path to the saved config file, or an empty ``Path()`` on failure.
    """
    try:
        GENERATED_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        dt_str = now.strftime("%Y-%m-%dT%H-%M-%S")
        exp_label = "_".join(k.lower() for k in selected_keys)
        fname = f"{exp_label}_{date_str}_{dt_str}.json"
        fpath = GENERATED_CONFIGS_DIR / fname

        experiments_cfg: Dict[str, Any] = {}
        for exp_key in selected_keys:
            info = prepared_per_experiment[exp_key]
            chosen_root = info["root"]
            wf_abs = info["workflow"]
            base_params = info["base_params"]

            # Store workflow relative to root when possible
            wf_stored = make_relative_if_under_root(wf_abs, chosen_root)

            # Count sessions for this experiment
            sc = sum(1 for s in sessions_to_launch if s["exp_key"] == exp_key)

            # Rebuild params relative to root when possible
            params_stored: Dict[str, str] = {}
            for k, v in base_params.items():
                if is_probably_path(v) and os.path.isabs(v):
                    params_stored[k] = make_relative_if_under_root(v, chosen_root)
                else:
                    params_stored[k] = v

            experiments_cfg[exp_key] = {
                "root": chosen_root,
                "workflow": wf_stored,
                "session_count": sc,
                "parameters": params_stored,
            }

        cfg_data = {
            "generated_at": dt_str,
            "experiments": selected_keys,
            "auto_start": auto_start,
            "parallel": parallel,
            "experiments_config": experiments_cfg,
        }

        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(cfg_data, f, indent=2, ensure_ascii=False)
        return fpath
    except Exception as e:
        print(f"Warning: could not save generated config: {e}")
        return Path()


# -------------------------------
# Experiment selection (interactive)
# -------------------------------
def select_experiments(cfg: Dict[str, Any]) -> List[str]:
    """Interactively prompt the user to select one or more experiments.

    Parameters
    ----------
    cfg : dict
        Launcher configuration dictionary containing an ``EXPERIMENTS`` map.

    Returns
    -------
    list of str
        Ordered, deduplicated list of selected experiment keys.
    """
    experiments = cfg["EXPERIMENTS"]
    keys = sorted(experiments.keys())
    print("\n=== Select Experiments ===")
    for i, k in enumerate(keys, start=1):
        print(f"  {i}. {k} — {experiments[k].get('name', '')}")
    print("  all. All experiments")

    while True:
        raw = input("Enter numbers/names (comma-separated), or 'all': ").strip()
        if not raw:
            print("Please enter a selection.")
            continue

        if raw.lower() == "all":
            return keys

        chosen: List[str] = []
        tokens = [t.strip() for t in raw.replace(";", ",").split(",") if t.strip()]
        ok = True
        for t in tokens:
            if t.isdigit():
                i = int(t)
                if 1 <= i <= len(keys):
                    chosen.append(keys[i - 1])
                else:
                    ok = False
                    break
            else:
                match = [k for k in keys if k.lower() == t.lower()]
                if match:
                    chosen.append(match[0])
                else:
                    ok = False
                    print(f"Unknown experiment: {t}")
                    break
        if ok and chosen:
            # preserve order, de-dupe
            result = []
            seen = set()
            for k in chosen:
                if k not in seen:
                    seen.add(k)
                    result.append(k)
            return result
        print("Invalid selection. Try again.")


# -------------------------------
# Workflow roots & workflows
# -------------------------------
def ensure_workflow_root(exp_key: str, exp: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """Interactively ensure a valid WorkflowRoot is selected for an experiment.

    Presents saved roots, lets the user pick one or add a new path. Persists
    any additions to the config file.

    Parameters
    ----------
    exp_key : str
        Experiment key in ``cfg["EXPERIMENTS"]``.
    exp : dict
        Experiment config dict (mutated in place when a new root is added).
    cfg : dict
        Full launcher config (saved to disk when updated).

    Returns
    -------
    str
        Normalised absolute path to the chosen WorkflowRoot.
    """
    experiments = cfg["EXPERIMENTS"]
    roots = exp.get("WorkflowRoot", [])
    if not isinstance(roots, list):
        roots = [roots]

    if not roots:
        print("No WorkflowRoot configured. Let's add one.")

    while True:
        options = (
            [f"{r} {'(found)' if exists_path(r) else '(missing)'}" for r in roots]
            if roots
            else []
        )
        options.append("Add a new WorkflowRoot...")
        print("\nSelect a WorkflowRoot")
        for i, opt in enumerate(options, start=1):
            print(f"  {i}. {opt}")
        raw = input("Enter number: ").strip()
        if not raw.isdigit():
            print("Invalid selection. Try again.")
            continue
        idx = int(raw) - 1

        if idx == len(options) - 1:
            new_root = prompt("Enter full path to WorkflowRoot (folder)")
            if not exists_path(new_root):
                print("That path does not exist. Please try again.")
                continue
            new_root = norm(new_root)
            if new_root not in roots:
                roots.append(new_root)
                exp["WorkflowRoot"] = [r.replace("\\", "/") for r in roots]
                experiments[exp_key] = exp
                cfg["EXPERIMENTS"] = experiments
                save_config(cfg)
            return new_root
        else:
            if 0 <= idx < len(roots):
                chosen = roots[idx]
                if exists_path(chosen):
                    return norm(chosen)
                else:
                    print(
                        "Selected root path does not exist. Choose another or add a new one."
                    )
            else:
                print("Invalid selection. Try again.")


def ensure_workflows(
    exp_key: str, exp: Dict[str, Any], chosen_root: str, cfg: Dict[str, Any]
) -> List[str]:
    """Show available workflows, let the user select one by number or enter a new path.

    Returns a one-element list with the absolute selected workflow path.

    Parameters
    ----------
    exp_key : str
        Experiment key in ``cfg["EXPERIMENTS"]``.
    exp : dict
        Experiment config dict (mutated when a new workflow is added).
    chosen_root : str
        Absolute path to the WorkflowRoot used to resolve relative entries.
    cfg : dict
        Full launcher config (saved to disk when updated).

    Returns
    -------
    list of str
        Single-element list containing the absolute path to the chosen workflow.
    """
    experiments = cfg["EXPERIMENTS"]
    wf_list = exp.get("workflows", [])
    if not isinstance(wf_list, list):
        wf_list = []
        exp["workflows"] = wf_list

    def to_abs(wf_entry: str) -> str:
        if not wf_entry:
            return ""
        return norm(
            wf_entry
            if os.path.isabs(wf_entry)
            else os.path.join(chosen_root, wf_entry.lstrip("\\/"))
        )

    while True:
        print("\nAvailable workflows for this experiment:")
        if wf_list:
            for idx, wf in enumerate(wf_list, start=1):
                abs_candidate = to_abs(wf)
                status = "[OK]" if exists_path(abs_candidate) else "[MISSING]"
                print(f"  {idx}. {wf}  {status}")
        else:
            print("  (none yet)")
        print(f"  {len(wf_list) + 1}. Enter a new workflow path...")

        sel_raw = input("Choose a workflow by number, or enter a new path: ").strip()

        if sel_raw.isdigit():
            sel = int(sel_raw)
            if 1 <= sel <= len(wf_list):
                chosen_entry = wf_list[sel - 1]
                chosen_abs = to_abs(chosen_entry)
                if not exists_path(chosen_abs):
                    print(f"\nSelected workflow does not exist:\n  {chosen_abs}")
                    print(
                        "Please fix it by entering a new path, or choose another entry."
                    )
                    continue
                return [chosen_abs]
            elif sel == len(wf_list) + 1:
                pass
            else:
                print("Invalid selection. Try again.")
                continue

        if not sel_raw:
            print("A workflow selection or path is required. Try again.")
            continue

        new_path = sel_raw
        if not os.path.isabs(new_path):
            new_path = os.path.join(chosen_root, new_path.lstrip("\\/"))
        new_path = norm(new_path)

        if not exists_path(new_path):
            print("That path does not exist. Try again.")
            continue

        stored = make_relative_if_under_root(new_path, chosen_root)
        if stored not in wf_list:
            wf_list.append(stored)
            exp["workflows"] = wf_list
            experiments[exp_key] = exp
            cfg["EXPERIMENTS"] = experiments
            save_config(cfg)
            print(f"Added workflow: {stored}")
        return [new_path]


# -------------------------------
# Parameter editor (persisted)
# -------------------------------
def prompt_parameters_menu(
    exp_key: str, exp: Dict[str, Any], chosen_root: str, cfg: Dict[str, Any]
) -> None:
    """Interactive menu to view and edit persisted experiment parameters.

    Allows the user to select, modify, add, and remove parameter values.
    Changes are persisted to ``launcher_config.json`` immediately.

    Parameters
    ----------
    exp_key : str
        Experiment key in ``cfg["EXPERIMENTS"]``.
    exp : dict
        Experiment config dict (mutated in place).
    chosen_root : str
        Absolute path to the WorkflowRoot for resolving relative paths.
    cfg : dict
        Full launcher config (saved to disk when updated).

    Returns
    -------
    None
    """
    experiments = cfg["EXPERIMENTS"]
    params = exp.get("parameters", {}) or {}
    if not isinstance(params, dict):
        exp["parameters"] = {}
        params = exp["parameters"]

    param_opts: Dict[str, List[str]] = exp.get("ParameterOptions", {}) or {}
    if not isinstance(param_opts, dict):
        exp["ParameterOptions"] = {}
        param_opts = exp["ParameterOptions"]

    def persist():
        experiments[exp_key] = exp
        cfg["EXPERIMENTS"] = experiments
        save_config(cfg)

    def to_abs(p: str) -> str:
        if not p:
            return ""
        return norm(
            p if os.path.isabs(p) else os.path.join(chosen_root, p.lstrip("\\/"))
        )

    def add_option(name: str, stored_value: str) -> None:
        if not stored_value:
            return
        lst = param_opts.get(name, [])
        if stored_value not in lst:
            lst.append(stored_value)
            param_opts[name] = lst
            exp["ParameterOptions"] = param_opts

    def store_pathlike(value_raw: str) -> str:
        v = value_raw.strip()
        if (not is_probably_path(v)) or "://" in v or v.startswith("@"):
            return v
        candidate_abs = to_abs(v)
        if exists_path(candidate_abs):
            return make_relative_if_under_root(candidate_abs, chosen_root)
        return v

    def choose_value_for_parameter(name: str) -> None:
        current_v = params.get(name, "")
        saved_list = list(param_opts.get(name, []) or [])
        if current_v and current_v not in saved_list:
            saved_list.insert(0, current_v)

        while True:
            print(f"\nParameter: {name}")
            if saved_list:
                for i, v in enumerate(saved_list, start=1):
                    is_pathlike = (
                        is_probably_path(v) and "://" not in v and not v.startswith("@")
                    )
                    preview = to_abs(v) if is_probably_path(v) else v
                    status = (
                        " [OK]"
                        if (is_probably_path(v) and exists_path(preview))
                        else (" [MISSING]" if is_probably_path(v) else "")
                    )
                    tag = " [current]" if v == current_v else ""
                    print(f"  {i}. {v}{tag}{status}")
            else:
                print("  (no saved values yet)")
            new_idx = len(saved_list) + 1
            rm_idx = new_idx + 1 if saved_list else new_idx
            back_idx = rm_idx + 1 if saved_list else new_idx

            print(f"  {new_idx}. Enter a new value...")
            if saved_list:
                print(f"  {rm_idx}. Remove a saved value...")
            print(f"  {back_idx}. Back")

            sel = input("Choose by number, or type a new value directly: ").strip()

            if sel and not sel.isdigit():
                new_raw = sel
                stored = store_pathlike(new_raw)
                params[name] = stored
                add_option(name, stored)
                persist()
                return

            if not sel.isdigit():
                print("Invalid selection. Try again.")
                continue

            sel_i = int(sel)

            if 1 <= sel_i <= len(saved_list):
                chosen = saved_list[sel_i - 1]
                stored = store_pathlike(chosen)
                params[name] = stored
                add_option(name, stored)
                persist()
                return

            if sel_i == new_idx:
                new_raw = prompt("New value", default="")
                if not new_raw:
                    continue
                stored = store_pathlike(new_raw)
                params[name] = stored
                add_option(name, stored)
                persist()
                return

            if saved_list and sel_i == rm_idx:
                print("\nSelect a value to remove from saved options:")
                for i, v in enumerate(saved_list, start=1):
                    print(f"  {i}. {v}")
                raw_rm = input("Enter number to remove (or blank to cancel): ").strip()
                if raw_rm.isdigit():
                    rm_i = int(raw_rm)
                    if 1 <= rm_i <= len(saved_list):
                        victim = saved_list[rm_i - 1]
                        if victim == current_v:
                            print(
                                "Cannot remove the current value. Switch current first."
                            )
                        else:
                            opts = param_opts.get(name, [])
                            if victim in opts:
                                opts.remove(victim)
                                param_opts[name] = opts
                                exp["ParameterOptions"] = param_opts
                                persist()
                                print("Removed.")
                continue

            if sel_i == back_idx:
                return

            print("Invalid selection. Try again.")

    while True:
        param_names = sorted(params.keys())
        print("\n=== Parameter Editor ===")
        if param_names:
            for i, name in enumerate(param_names, start=1):
                print(f"  {i}. {name}  [current={params.get(name, '')}]")
        else:
            print("  (no parameters yet)")

        add_idx = len(param_names) + 1
        done_idx = add_idx + 1
        print(f"  {add_idx}. Add a new parameter...")
        print(f"  {done_idx}. Done")

        sel = input(
            "Choose a parameter by number, or press Enter to add a new one: "
        ).strip()
        if not sel:
            sel = str(add_idx)

        if sel.isdigit():
            sel_i = int(sel)
            if 1 <= sel_i <= len(param_names):
                choose_value_for_parameter(param_names[sel_i - 1])
                continue
            if sel_i == add_idx:
                while True:
                    name = prompt(
                        "New parameter name (e.g., RuleSettings)", default=""
                    ).strip()
                    if not name:
                        print("A parameter name is required.")
                        continue
                    break
                initial = prompt(
                    "Initial value (absolute/relative path, URL, or connection string)",
                    default="",
                ).strip()
                if initial:
                    if is_probably_path(initial):
                        initial_abs = (
                            norm(os.path.join(chosen_root, initial.lstrip("\\/")))
                            if not os.path.isabs(initial)
                            else norm(initial)
                        )
                        stored = (
                            make_relative_if_under_root(initial_abs, chosen_root)
                            if exists_path(initial_abs)
                            else initial
                        )
                    else:
                        stored = initial
                    exp["parameters"][name] = stored
                    lst = param_opts.get(name, [])
                    if stored not in lst:
                        lst.append(stored)
                        param_opts[name] = lst
                    persist()
                else:
                    exp["parameters"][name] = ""
                    persist()
                continue
            if sel_i == done_idx:
                print("Parameter edits complete.")
                return

        print("Invalid selection. Try again.")


# -------------------------------
# Lifealert options menu (persisted)
# -------------------------------
def lifealert_options_menu(
    pir_exp: Dict[str, Any], pir_root: str, cfg: Dict[str, Any]
) -> str:
    """Interactive menu to select or add a Lifealert directory.

    Saves the chosen path to the Pirouette experiment's ``LifealertOptions``
    list and persists to ``launcher_config.json``.

    Parameters
    ----------
    pir_exp : dict
        Pirouette experiment config dict (mutated in place).
    pir_root : str
        Absolute path to the Pirouette WorkflowRoot for resolving relative paths.
    cfg : dict
        Full launcher config (saved to disk when updated).

    Returns
    -------
    str
        Absolute path to the chosen Lifealert directory, or ``""`` if the user
        chose Back.
    """
    experiments = cfg["EXPERIMENTS"]
    params = pir_exp.get("parameters", {}) or {}
    options = pir_exp.get("LifealertOptions", []) or []
    current = params.get("LifealertRoot", "")

    def persist():
        pir_exp["parameters"] = params
        pir_exp["LifealertOptions"] = options
        experiments["Pirouette"] = pir_exp
        cfg["EXPERIMENTS"] = experiments
        save_config(cfg)

    def to_abs(p: str) -> str:
        if not p:
            return ""
        return norm(p if os.path.isabs(p) else os.path.join(pir_root, p.lstrip("\\/")))

    while True:
        print("\n=== Lifealert Directory ===")
        if options:
            for i, v in enumerate(options, start=1):
                tag = " [current]" if v == current else ""
                print(f"  {i}. {v}{tag}")
        else:
            print("  (no saved Lifealert paths yet)")
        add_i = len(options) + 1
        back_i = add_i + 1
        print(f"  {add_i}. Enter a new path...")
        print(f"  {back_i}. Back")

        sel = input("Choose by number, or type a new path directly: ").strip()

        if sel and not sel.isdigit():
            newp = sel
            selected_abs = to_abs(newp)
            if exists_path(selected_abs):
                stored = make_relative_if_under_root(selected_abs, pir_root)
            else:
                stored = newp
            params["LifealertRoot"] = stored
            if stored not in options:
                options.append(stored)
            persist()
            return selected_abs

        if not sel.isdigit():
            print("Invalid selection. Try again.")
            continue

        si = int(sel)
        if 1 <= si <= len(options):
            stored = options[si - 1]
            selected_abs = to_abs(stored)
            params["LifealertRoot"] = stored
            persist()
            return selected_abs

        if si == add_i:
            newp = prompt("New Lifealert directory", default="").strip()
            if not newp:
                continue
            selected_abs = to_abs(newp)
            if exists_path(selected_abs):
                stored = make_relative_if_under_root(selected_abs, pir_root)
            else:
                stored = newp
            params["LifealertRoot"] = stored
            if stored not in options:
                options.append(stored)
            persist()
            return selected_abs

        if si == back_i:
            return ""

        print("Invalid selection. Try again.")


# -------------------------------
# Parameter expansion to absolute for launch
# -------------------------------
def expand_and_validate_parameters(
    exp_key: str, exp: Dict[str, Any], chosen_root: str, cfg: Dict[str, Any]
) -> Dict[str, str]:
    """Expand all relative path parameters to absolute paths for launch.

    Parameters
    ----------
    exp_key : str
        Experiment key (unused in current logic, reserved for future use).
    exp : dict
        Experiment config dict containing a ``parameters`` sub-dict.
    chosen_root : str
        Absolute path to the WorkflowRoot used to resolve relative values.
    cfg : dict
        Full launcher config (unused in current logic, reserved for future use).

    Returns
    -------
    dict
        Mapping of parameter name to absolute (or unchanged) value string.
    """
    params = exp.get("parameters", {}) or {}
    final_params: Dict[str, str] = {}

    for k, v in params.items():
        if isinstance(v, str) and is_probably_path(v):
            candidate = (
                v if os.path.isabs(v) else os.path.join(chosen_root, v.lstrip("\\/"))
            )
            candidate = norm(candidate)
            if "://" not in v and not v.startswith("@"):
                final_params[k] = candidate
            else:
                final_params[k] = v
        else:
            final_params[k] = v

    return final_params


# -------------------------------
# Resolve Bonsai exe path
# -------------------------------
def resolve_bonsai_cmd(
    exp_key: str, exp: Dict[str, Any], chosen_root: str, cfg: Dict[str, Any]
) -> str:
    """Resolve the absolute path to the Bonsai executable for an experiment.

    Reads ``BONSAI_CMD`` from the experiment config. Prompts the user if the
    value is missing or the path does not exist, and persists the choice.

    Parameters
    ----------
    exp_key : str
        Experiment key in ``cfg["EXPERIMENTS"]``.
    exp : dict
        Experiment config dict (mutated when ``BONSAI_CMD`` is updated).
    chosen_root : str
        Absolute path to the WorkflowRoot for resolving relative paths.
    cfg : dict
        Full launcher config (saved to disk when updated).

    Returns
    -------
    str
        Absolute path to the Bonsai executable.
    """
    rel_cmd = (exp.get("BONSAI_CMD") or "").strip()

    def _full(p: str) -> str:
        return norm(
            p if os.path.isabs(p) else os.path.join(chosen_root, p.lstrip("\\/"))
        )

    if rel_cmd:
        full = _full(rel_cmd)
        if exists_path(full):
            return full
        else:
            print(f"\nBonsai executable not found at: {full}")
    else:
        print("\nNo BONSAI_CMD set for this experiment yet.")

    while True:
        new_val = prompt(
            "Enter path to Bonsai executable (absolute or RELATIVE to the chosen WorkflowRoot)"
        )
        if not new_val:
            print("A path is required.")
            continue
        full = _full(new_val)
        if not exists_path(full):
            print("That path does not exist. Please try again.")
            continue

        stored = make_relative_if_under_root(full, chosen_root)
        exp["BONSAI_CMD"] = stored
        cfg["EXPERIMENTS"][exp_key] = exp
        save_config(cfg)
        return full


# -------------------------------
# Pirouette recovery temp files
# -------------------------------
def zero_pirouette_temp_files(chosen_root: str) -> None:
    """Reset Pirouette recovery temp files in ``<chosen_root>/src`` to ``"0"``.

    Creates each file if it does not yet exist.  All three files are registered
    in ``PIROUETTE_RECOVERY_FILES`` so that cleanup zeroes them again on exit
    rather than deleting them.

    Parameters
    ----------
    chosen_root : str
        Absolute path to the Pirouette WorkflowRoot.

    Returns
    -------
    None
    """
    src_dir = Path(chosen_root) / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    filenames = [
        "AccumulatedCommutatorTurnsRecovery.txt",
        "AccumulatedMagnetTurnsRecovery.txt",
        "SampleIndexRecovery.txt",
    ]
    for name in filenames:
        fpath = src_dir / name
        try:
            existed = fpath.exists()
            fpath.write_text("0", encoding="utf-8")
            if str(fpath) not in PIROUETTE_RECOVERY_FILES:
                PIROUETTE_RECOVERY_FILES.append(str(fpath))
            action = "Reset" if existed else "Created"
            print(f"{action} recovery file: {fpath}")
        except Exception as e:
            print(f"Warning: could not reset {fpath}: {e}")


# -------------------------------
# Per-session schema copies
# -------------------------------
def _remap_path_to_local_root(path_str: str, local_root: str) -> Optional[str]:
    """Attempt to remap a foreign absolute path to the local workflow root.

    Walks progressively shorter suffixes of *path_str* until a path that
    exists under *local_root* is found.

    Parameters
    ----------
    path_str : str
        An absolute path that may originate from a different machine.
    local_root : str
        The local workflow root directory to remap into.

    Returns
    -------
    str or None
        Remapped path string if a match exists under *local_root*, otherwise
        ``None``.
    """
    try:
        local_root_p = Path(local_root)
        foreign_p = Path(path_str.replace("/", "\\"))
        try:
            foreign_p.relative_to(local_root_p)
            return None  # already under local root — no change needed
        except ValueError:
            pass
        # Try progressively shorter suffixes (skip the drive letter part)
        parts = foreign_p.parts[1:]
        for start in range(len(parts)):
            candidate = local_root_p.joinpath(*parts[start:])
            if candidate.exists():
                return str(candidate)
    except Exception:
        pass
    return None


def patch_bonsai_xml(
    workflow_path: str,
    chosen_root: str,
) -> None:
    """Patch a ``.bonsai`` workflow file in-place to fix stale default paths.

    Bonsai deserialises the workflow XML *before* applying ``-p`` command-line
    overrides.  When default property values embedded in the XML reference paths
    that do not exist on the current machine (e.g. saved on a different
    workstation), Bonsai raises an XML error at load time.  This function edits
    the original ``.bonsai`` file directly so Bonsai always opens
    ``DelphiMain.bonsai`` — no copies are created.

    The original XML content is saved to ``BONSAI_ORIGINALS`` and is restored
    automatically by ``cleanup_temp_files_and_processes`` when the launcher
    exits, leaving the repository in its original state.

    Parameters
    ----------
    workflow_path : str
        Absolute path to the ``.bonsai`` workflow file.
    chosen_root : str
        Workflow root directory for the current machine.

    Returns
    -------
    None
    """
    src = Path(workflow_path)
    if not src.exists():
        return

    try:
        original = src.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Warning: could not read Bonsai XML for patching ({e}); skipping.")
        return

    # Match absolute Windows-style paths (backslash or forward-slash variants)
    path_re = re.compile(r'[A-Za-z]:[/\\][^<>&"\n\t]*')

    replacements: Dict[str, str] = {}
    for match in path_re.finditer(original):
        raw = match.group(0).rstrip("\\/")
        if raw in replacements:
            continue
        if Path(raw).exists():
            continue  # already valid on this machine
        remapped = _remap_path_to_local_root(raw, chosen_root)
        if remapped:
            replacements[raw] = remapped

    if not replacements:
        return  # nothing to patch

    patched = original
    for old, new in replacements.items():
        patched = patched.replace(old, new)

    try:
        src.write_text(patched, encoding="utf-8")
        # Save original only once per file (first patch wins)
        if str(src) not in BONSAI_ORIGINALS:
            BONSAI_ORIGINALS[str(src)] = original
        print(f"  Patched Bonsai XML in-place ({len(replacements)} path(s) remapped): {src.name}")
    except Exception as e:
        print(f"Warning: could not write patched Bonsai XML ({e}); skipping.")


def copy_and_patch_hardware_schema(
    src_abs: str, subject_id: str, start_utc_hyphen: str
) -> str:
    """Copy a hardware schema file and inject ``subject_id`` and ``session_time``.

    The patched copy is written next to the original with a name that includes
    the subject ID and timestamp, and is registered for deletion on exit.

    Parameters
    ----------
    src_abs : str
        Absolute path to the source hardware schema file (YAML).
    subject_id : str
        Subject identifier to inject.
    start_utc_hyphen : str
        Session start timestamp in ``YYYY-MM-DDTHH-MM-SS`` format.

    Returns
    -------
    str
        Absolute path to the patched copy.
    """
    src = Path(src_abs)
    parent = src.parent
    stem = src.stem
    ext = src.suffix or ".yml"
    dst = parent / f"{stem}_{subject_id}_{start_utc_hyphen}{ext}"

    try:
        text = src.read_text(encoding="utf-8") if src.exists() else ""
    except Exception:
        text = ""

    def upsert_line(content: str, key: str, value: str) -> str:
        pattern = re.compile(rf"^(?P<prefix>{re.escape(key)}\s*:\s*).*$", re.MULTILINE)
        if pattern.search(content):
            return pattern.sub(rf"\g<prefix>{value}", content)
        else:
            return f"{key}: {value}\n" + content

    text = upsert_line(text, "subject_id", f"'{subject_id}'")
    text = upsert_line(text, "session_time", f"'{start_utc_hyphen}'")

    try:
        dst.write_text(text, encoding="utf-8")
        TEMP_SESSION_FILES.append(str(dst))
    except Exception as e:
        print(f"Warning: could not write patched hardware schema: {e}")
    return str(dst)


def copy_and_patch_aind_session_json(
    src_abs: str,
    subject_id: str,
    start_utc_hyphen: str,
    experimenter: str,
    experiment_value: str,
) -> str:
    """Copy an AIND session JSON and inject session metadata fields.

    The patched copy is written next to the original and registered for
    deletion on exit.

    Parameters
    ----------
    src_abs : str
        Absolute path to the source AIND session JSON file.
    subject_id : str
        Subject identifier.
    start_utc_hyphen : str
        Session start timestamp in ``YYYY-MM-DDTHH-MM-SS`` format.
    experimenter : str
        Experimenter name (placed in a one-element list under ``experimenter``).
    experiment_value : str
        Experiment label stored under the ``experiment`` key.

    Returns
    -------
    str
        Absolute path to the patched copy.
    """
    src = Path(src_abs)
    parent = src.parent
    stem = src.stem
    ext = src.suffix or ".json"
    dst = parent / f"{stem}_{subject_id}_{start_utc_hyphen}{ext}"

    obj: Dict[str, Any] = {}
    if src.exists():
        try:
            obj = json.loads(src.read_text(encoding="utf-8"))
        except Exception:
            obj = {}

    obj["subject"] = subject_id

    # Convert session start (hyphen format) to true RFC3339/ISO-8601 timestamp
    # Example target: 2026-03-13T16:11:58.419072Z
    dt = datetime.strptime(start_utc_hyphen, "%Y-%m-%dT%H-%M-%S")
    dt = dt.replace(tzinfo=timezone.utc)
    obj["date"] = dt.isoformat(timespec="microseconds").replace("+00:00", "Z")
    obj["session_name"] = f"{subject_id}_{start_utc_hyphen}"
    obj["experimenter"] = [experimenter] if experimenter else []
    obj["experiment"] = experiment_value

    try:
        dst.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        # Do NOT register in TEMP_SESSION_FILES — the patched AindBehaviorSessionModel
        # file must be kept after the launcher exits so downstream tools can read it.
    except Exception as e:
        print(f"Warning: could not write patched AIND session JSON: {e}")
    return str(dst)


# -------------------------------
# Named per-session overrides helper (define BEFORE use)
# -------------------------------
def build_session_overrides_if_available(
    exp: Dict[str, Any], session: Dict[str, Any]
) -> Dict[str, str]:
    """If the experiment config includes parameter names like 'Experimenter', 'SubjectId',
    'SessionId', 'SessionStartUtc', 'ExperimentName', prepare per-session overrides.

    Parameters
    ----------
    exp : dict
        Experiment config dict (used to check which parameter names are declared).
    session : dict
        Session dictionary from :func:`build_session_with_values`.

    Returns
    -------
    dict
        Mapping of parameter name to session value for any matching keys.
    """
    param_names = set((exp.get("parameters") or {}).keys())
    overrides = {}
    mapping = [
        ("Experimenter", "experimenter"),
        ("SubjectId", "subject_id"),
        ("SessionId", "session_id"),
        ("SessionStartUtc", "start_utc"),
        ("ExperimentName", "experiment_name"),
    ]
    for pname, sname in mapping:
        if pname in param_names:
            overrides[pname] = str(session.get(sname, ""))
    return overrides


# -------------------------------
# Per-session parameter override menu (session-only)
# -------------------------------
def prompt_per_session_overrides(
    exp: Dict[str, Any], chosen_root: str, base_params_abs: Dict[str, str]
) -> Dict[str, str]:
    """Interactively override parameters for the current session only.

    Changes are NOT persisted to ``launcher_config.json``; they apply only
    to the session being built.

    Parameters
    ----------
    exp : dict
        Experiment config dict (read for ``ParameterOptions``).
    chosen_root : str
        Absolute path to the WorkflowRoot for resolving relative paths.
    base_params_abs : dict
        Starting parameter dict (absolute paths); a copy is modified and
        returned.

    Returns
    -------
    dict
        Updated parameter dict with any user-supplied overrides applied.
    """
    params = dict(base_params_abs)
    param_opts: Dict[str, List[str]] = exp.get("ParameterOptions", {}) or {}

    def to_abs(v: str) -> str:
        if not v:
            return v
        return norm(
            v if os.path.isabs(v) else os.path.join(chosen_root, v.lstrip("\\/"))
        )

    while True:
        names = sorted(params.keys())
        print("\n=== Per-Session Overrides ===")
        for i, k in enumerate(names, start=1):
            print(f"  {i}. {k}  [current={params[k]}]")
        add_i = len(names) + 1
        done_i = add_i + 1
        print(f"  {add_i}. Enter a new override (name=value)")
        print(f"  {done_i}. Done")

        sel = input(
            "Pick a parameter by number to override, or choose an option: "
        ).strip()
        if not sel.isdigit():
            print("Invalid selection. Try again.")
            continue

        sel_i = int(sel)
        if 1 <= sel_i <= len(names):
            k = names[sel_i - 1]
            options = param_opts.get(k, [])
            if options:
                print(f"\nSaved values for '{k}':")
                for j, opt in enumerate(options, start=1):
                    preview = to_abs(opt) if is_probably_path(opt) else opt
                    status = ""
                    if (
                        is_probably_path(opt)
                        and "://" not in opt
                        and not opt.startswith("@")
                    ):
                        status = " [OK]" if exists_path(preview) else " [MISSING]"
                    print(f"  {j}. {opt}{status}")
                print(f"  {len(options) + 1}. Enter a new value...")
                raw = input("Choose by number or type a new value: ").strip()
                if raw.isdigit():
                    r = int(raw)
                    if 1 <= r <= len(options):
                        val = options[r - 1]
                        params[k] = to_abs(val) if is_probably_path(val) else val
                        continue
                    elif r == len(options) + 1:
                        pass
                    else:
                        print("Invalid selection.")
                        continue
                newv = (
                    raw if raw and not raw.isdigit() else input("New value: ").strip()
                )
            else:
                newv = input(f"New value for '{k}': ").strip()

            if newv:
                params[k] = to_abs(newv) if is_probably_path(newv) else newv
            continue

        if sel_i == add_i:
            raw = input("Enter name=value: ").strip()
            if "=" not in raw:
                print("Please use the format name=value.")
                continue
            name, value = raw.split("=", 1)
            name = name.strip()
            value = value.strip()
            params[name] = to_abs(value) if is_probably_path(value) else value
            continue

        if sel_i == done_i:
            return params

        print("Invalid selection. Try again.")


# -------------------------------
# Launch utilities
# -------------------------------
def prompt_launch_mode() -> bool:
    """Prompt the user to choose between auto-starting Bonsai or opening it in the editor.

    Returns
    -------
    bool
        ``True`` to auto-start (``--start`` flag), ``False`` to open in editor.
    """
    while True:
        ans = (
            input("\nStart workflow now or open in editor? [S]tart / [O]pen: ")
            .strip()
            .lower()
        )
        if ans in ("s", "start"):
            return True
        if ans in ("o", "open"):
            return False
        print("Please type 'S' to Start or 'O' to Open.")


def prompt_run_mode() -> bool:
    """Prompt the user to choose between sequential and parallel session execution.

    Returns
    -------
    bool
        ``True`` for parallel, ``False`` for sequential.
    """
    while True:
        ans = (
            input("Run all sessions [S]equentially or in [P]arallel? ").strip().lower()
        )
        if ans in ("s", "seq", "sequential", "sequentially"):
            return False
        if ans in ("p", "par", "parallel"):
            return True
        print("Please type 'S' for sequential or 'P' for parallel.")


# ---- NEW: filter out non-Bonsai meta-parameters (e.g., LifealertRoot) ----
def filter_params_for_bonsai(parameters: Dict[str, str]) -> Dict[str, str]:
    """Return a copy of params excluding keys that should NOT be passed to Bonsai.

    Parameters
    ----------
    parameters : dict
        Full parameter dict including any meta-keys.

    Returns
    -------
    dict
        Filtered copy with Bonsai-unsafe keys removed (e.g. ``LifealertRoot``).
    """
    EXCLUDE = {"lifealertroot"}  # case-insensitive
    return {k: v for k, v in parameters.items() if k.lower() not in EXCLUDE}


def launch_bonsai(
    bonsai_cmd_full: str,
    workflow_path: str,
    parameters: Dict[str, str],
    auto_start: bool,
) -> subprocess.Popen:
    """Launch a Bonsai workflow process.

    Parameters
    ----------
    bonsai_cmd_full : str
        Absolute path to the Bonsai executable.
    workflow_path : str
        Absolute path to the ``.bonsai`` workflow file.
    parameters : Dict[str, str]
        Parameter dict to pass to Bonsai as ``-p Name=Value`` arguments.
        ``LifealertRoot`` and other meta-keys are automatically filtered out.
    auto_start : bool
        If ``True``, passes ``--start`` to Bonsai so it begins immediately.

    Returns
    -------
    subprocess.Popen
        The launched process handle.

    Raises
    ------
    SystemExit
        If the Bonsai executable cannot be found.
    """
    cmd = [bonsai_cmd_full, workflow_path]
    if auto_start:
        cmd.append("--start")
    # Filter out LifealertRoot (and any future non-Bonsai meta keys)
    safe_params = filter_params_for_bonsai(parameters)
    for k, v in safe_params.items():
        cmd.extend(["-p", f"{k}={v}"])  # Bonsai uses -p Name=Value
    print("\nLaunching:", " ".join(cmd))
    try:
        return subprocess.Popen(cmd)  # shell=False, argv list
    except FileNotFoundError:
        print(
            "Error: Could not start Bonsai. Verify BONSAI_CMD for this experiment in the config."
        )
        sys.exit(1)


def stop_lifealert():
    """Stop all running lifealert processes using taskkill and wait for exit.

    Returns
    -------
    None

    Raises
    ------
    RuntimeError
        If one or more lifealert processes do not exit within the timeout.
    """
    if not LIFEALERT_PROCS:
        print("No lifealert process is currently running.")
        return

    print("Stopping lifealert processes...")

    for p in LIFEALERT_PROCS[:]:
        try:
            # Kill process tree decisively
            subprocess.run(
                ["taskkill", "/PID", str(p.pid), "/F", "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            pass

    # CRITICAL: wait for Windows to release file handles
    gone, alive = psutil.wait_procs(LIFEALERT_PROCS, timeout=10)

    if alive:
        raise RuntimeError(
            f"Lifealert did not fully exit. Still alive: {[p.pid for p in alive]}"
        )

    LIFEALERT_PROCS.clear()

    # Extra grace period for Windows handle cleanup
    time.sleep(1.5)

    print("lifealert stopped cleanly.")


def start_lifealert_with_menu(
    cfg: Dict[str, Any], prepared_per_experiment: Dict[str, Dict[str, Any]]
) -> None:
    """Present the Lifealert directory menu and start the lifealert process.

    Searches ``cfg["EXPERIMENTS"]`` for an experiment whose key contains
    ``"pirouette"`` (case-insensitive) and uses that experiment's root.

    Parameters
    ----------
    cfg : dict
        Full launcher config.
    prepared_per_experiment : dict
        Mapping of exp_key to prepared info dict from the current run.

    Returns
    -------
    None
    """
    pir_key = next(
        (k for k in cfg["EXPERIMENTS"] if "pirouette" in k.lower()), None
    )
    if pir_key is None:
        print("No Pirouette experiment configured; cannot start lifealert.")
        return
    pir_info = prepared_per_experiment.get(pir_key)
    if not pir_info:
        print("Pirouette experiment was not selected for this run; cannot start lifealert.")
        return
    pir_exp = cfg["EXPERIMENTS"][pir_key]
    pir_root = pir_info["root"]
    choose_abs = lifealert_options_menu(pir_exp, pir_root, cfg)
    if not choose_abs:
        print("No lifealert directory chosen.")
        return
    if not exists_path(choose_abs):
        print(f"Selected lifealert directory does not exist: {choose_abs}")
        return
    try:
        print(f"Starting lifealert in: {choose_abs}")
        p = subprocess.Popen(["uv", "run", "lifealert"], cwd=norm(choose_abs))
        LIFEALERT_PROCS.append(p)
        print("lifealert started.")
    except FileNotFoundError:
        print("Could not start lifealert: 'uv' not found on PATH.")
    except Exception as e:
        print(f"Could not start lifealert: {e}")


def show_status():
    """Print the current status of all workflow and lifealert processes.

    Returns
    -------
    None
    """
    print("\n=== Status ===")
    if WORKFLOW_PROCS:
        alive = 0
        for i, p in enumerate(WORKFLOW_PROCS, start=1):
            code = p.poll()
            state = "RUNNING" if code is None else f"EXITED({code})"
            print(f"  Workflow[{i}]: PID={p.pid} {state}")
            if code is None:
                alive += 1
        if alive == 0:
            print("  All workflow processes have exited.")
    else:
        print("  No workflow processes launched.")

    if LIFEALERT_PROCS:
        for i, p in enumerate(LIFEALERT_PROCS, start=1):
            code = p.poll()
            state = "RUNNING" if code is None else f"EXITED({code})"
            print(f"  lifealert[{i}]: PID={p.pid} {state}")
    else:
        print("  lifealert: not running.")


def wait_for_workflows():
    """Block until all launched Bonsai workflow processes have finished.

    Returns
    -------
    None
    """
    if not WORKFLOW_PROCS:
        print("No workflow processes to wait for.")
        return
    print("Waiting for all workflow processes to finish (Ctrl+C to abort waiting)...")
    try:
        for p in WORKFLOW_PROCS:
            p.wait()
    except KeyboardInterrupt:
        print("\nStopped waiting. Workflows may still be running.")


def cleanup_temp_files_and_processes():
    """Stop lifealert processes and delete all registered temporary session files.

    Returns
    -------
    None
    """
    print("\nRunning cleanup routine...")

    # --- Close Bonsai workflow processes ---
    running = [p for p in WORKFLOW_PROCS if p.poll() is None]
    if running:
        print("Closing Bonsai workflow processes...")
        for p in running:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(p.pid), "/F", "/T"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception:
                pass
        try:
            psutil.wait_procs(running, timeout=10)
        except Exception:
            pass
        print("Bonsai workflows closed.")

    # --- Kill lifealert first (if running), same logic as stop_lifealert() ---
    if LIFEALERT_PROCS:
        print("Stopping lifealert processes...")
        procs_to_kill = list(LIFEALERT_PROCS)
        for p in procs_to_kill:
            try:
                # Kill process tree decisively
                subprocess.run(
                    ["taskkill", "/PID", str(p.pid), "/F", "/T"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception:
                pass
        try:
            psutil.wait_procs(procs_to_kill, timeout=5)
        except Exception:
            pass
        LIFEALERT_PROCS.clear()

    # --- Zero Pirouette recovery files (never delete them) ---
    if PIROUETTE_RECOVERY_FILES:
        print("Resetting Pirouette recovery files to 0:")
        for fp in PIROUETTE_RECOVERY_FILES:
            try:
                Path(fp).write_text("0", encoding="utf-8")
                print(f"  zeroed: {fp}")
            except Exception as e:
                print(f"  ERROR: could not zero {fp}: {e}")

    # --- NOW remove temp files ---
    if TEMP_SESSION_FILES:
        print("Cleaning up per-session override and temp files:")
        for fp in TEMP_SESSION_FILES:
            try:
                Path(fp).unlink(missing_ok=True)
                print(f"  removed: {fp}")
            except PermissionError:
                # Windows file lock — retry after short sleep
                import time

                time.sleep(0.2)
                try:
                    Path(fp).unlink(missing_ok=True)
                    print(f"  removed after retry: {fp}")
                except Exception as e:
                    print(f"  ERROR: could not remove {fp}: {e}")
            except Exception as e:
                print(f"  WARNING: could not remove {fp}: {e}")

    # --- Restore any .bonsai files that were patched in-place ---
    if BONSAI_ORIGINALS:
        print("Restoring patched Bonsai workflow files:")
        for fpath, original_text in BONSAI_ORIGINALS.items():
            try:
                Path(fpath).write_text(original_text, encoding="utf-8")
                print(f"  restored: {fpath}")
            except Exception as e:
                print(f"  WARNING: could not restore {fpath}: {e}")


# -------------------------------
# Pre-launch SUMMARY (hides LifealertRoot)
# -------------------------------
def print_prelaunch_summary(
    sessions_to_launch: List[Dict[str, Any]], auto_start: bool, parallel: bool
):
    """Print a formatted pre-launch summary of all sessions to be run.

    LifealertRoot is excluded from the parameter display to match exactly
    what will be passed to Bonsai.

    Parameters
    ----------
    sessions_to_launch : list of dict
        All assembled session dicts for this run.
    auto_start : bool
        Whether Bonsai will be auto-started.
    parallel : bool
        Whether sessions will run in parallel.

    Returns
    -------
    None
    """
    print("\n==================== PRE-LAUNCH SUMMARY ====================")
    print(f"Mode      : {'START (run)' if auto_start else 'OPEN (editor)'}")
    print(f"Scheduling: {'PARALLEL' if parallel else 'SEQUENTIAL'}")
    print("------------------------------------------------------------")
    for idx, s in enumerate(sessions_to_launch, start=1):
        sess = s["session"]
        print(
            f"[{idx}] Experiment: {sess['experiment_name']} | Experimenter={sess['experimenter']} | Subject={sess['subject_id']} | Start={sess['start_utc']}"
        )
        print(f"     Workflow : {s['workflow']}")
        print("     Parameters:")
        # Show only the params that will be passed to Bonsai (filter out LifealertRoot)
        display_params = filter_params_for_bonsai(s["params"])
        for k, v in sorted(display_params.items()):
            print(f"       - {k}: {v}")
    print("============================================================")


# -------------------------------
# Profile loading (JSON/YAML)
# -------------------------------
def resolve_profile_path(name_or_file: str) -> Optional[Path]:
    """Resolve a profile name or file path to an existing Path object.

    Searches ``experiment_configs/`` (``EXPERIMENTS_DIR``) first, then the
    directory containing ``launcher.py``, when a bare name (without extension)
    is given.

    Parameters
    ----------
    name_or_file : str
        A bare profile name (e.g. ``"delphi_experiment"``), a relative/absolute
        file path with a recognised extension, or an existing file path.

    Returns
    -------
    Path or None
        Resolved path if found, otherwise ``None``.
    """
    if not name_or_file:
        return None
    p = Path(name_or_file)
    if p.suffix.lower() in (".json", ".yaml", ".yml"):
        return p if p.exists() else None
    if p.exists() and p.is_file():
        return p
    # Treat as a bare NAME — search experiment_configs/ first, then launcher dir
    launcher_dir = Path(__file__).parent
    candidates = [
        EXPERIMENTS_DIR / f"{name_or_file}.json",
        EXPERIMENTS_DIR / f"{name_or_file}.yaml",
        EXPERIMENTS_DIR / f"{name_or_file}.yml",
        launcher_dir / f"{name_or_file}.json",   # fallback: next to launcher.py
        launcher_dir / f"{name_or_file}.yaml",
        launcher_dir / f"{name_or_file}.yml",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    return None


def load_profile(path: Path) -> Dict[str, Any]:
    """Load a profile from a JSON or YAML file.

    Parameters
    ----------
    path : Path
        Path to the profile file.

    Returns
    -------
    dict
        Parsed profile dictionary.

    Raises
    ------
    RuntimeError
        If a YAML file is provided but PyYAML is not installed, or if the
        file extension is not recognised.
    """
    if path.suffix.lower() == ".json":
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    # YAML
    if path.suffix.lower() in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError(
                "YAML profile provided but PyYAML is not available. Use JSON or install PyYAML."
            )
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    raise RuntimeError(f"Unsupported profile extension: {path.suffix}")


def apply_profile_to_config(
    profile: Dict[str, Any],
    cfg: Dict[str, Any],
    profile_dir: Optional[Path] = None,
) -> Tuple[List[str], Optional[bool], Optional[bool]]:
    """Apply a loaded profile to the launcher config, resolving relative paths.

    Relative ``root`` and ``LifealertRoot`` values in the profile are resolved
    relative to *profile_dir* (the directory containing the profile file).

    Parameters
    ----------
    profile : dict
        Parsed profile dictionary.
    cfg : dict
        Full launcher config (mutated in place with profile overrides).
    profile_dir : Path, optional
        Directory of the profile file, used to resolve relative paths in the
        profile.  When ``None``, relative paths are left unresolved.

    Returns
    -------
    tuple of (list of str, bool or None, bool or None)
        A 3-tuple of ``(selected_keys, auto_start, parallel)``.
        ``auto_start`` and ``parallel`` are ``None`` when not specified in
        the profile.
    """
    experiments = cfg["EXPERIMENTS"]
    # Selected experiments
    selected = profile.get("experiments", [])
    if isinstance(selected, str) and selected.lower() == "all":
        selected_keys = sorted(experiments.keys())
    elif isinstance(selected, list) and selected:
        selected_keys = []
        for k in selected:
            if k in experiments:
                selected_keys.append(k)
            else:
                print(
                    f"Warning: profile references unknown experiment '{k}'. Skipping."
                )
        if not selected_keys:
            selected_keys = sorted(experiments.keys())
    else:
        selected_keys = sorted(experiments.keys())

    # Apply per-experiment config
    per = profile.get("experiments_config", {}) or {}
    for k, exp_override in per.items():
        if k not in experiments:
            print(
                f"Warning: profile experiments_config has unknown experiment '{k}'. Skipping."
            )
            continue
        exp = experiments[k]

        # Set root
        root = exp_override.get("root")
        if root:
            root_path = Path(root)
            if not root_path.is_absolute() and profile_dir is not None:
                root = str((profile_dir / root_path).resolve())
            exp["WorkflowRoot"] = [root]

        # Set workflow
        wf = exp_override.get("workflow")
        if wf:
            exp["workflows"] = [wf]

        # Set parameters (merge/replace)
        params = exp_override.get("parameters", {})
        if params and isinstance(params, dict):
            if "LifealertRoot" in params and profile_dir is not None:
                lr = params["LifealertRoot"]
                if lr and not Path(lr).is_absolute():
                    params["LifealertRoot"] = str((profile_dir / lr).resolve())
            base_params = exp.get("parameters", {}) or {}
            base_params.update(params)
            exp["parameters"] = base_params

        experiments[k] = exp

    auto_start = profile.get("auto_start")
    parallel = profile.get("parallel")
    return selected_keys, auto_start, parallel


# -------------------------------
# Session planning (interactive or profile)
# -------------------------------
def plan_sessions_for_experiment(
    exp_key: str, profile_session_count: Optional[int] = None
) -> int:
    """Determine how many sessions to run for this experiment.

    - If 'profile_session_count' is provided (from a profile .json/.yaml), return it directly.
    - Otherwise, interactive prompt: ask the user.

    Parameters
    ----------
    exp_key : str
        Experiment key shown in the interactive prompt.
    profile_session_count : int, optional
        Session count from a loaded profile. When provided and valid
        (>= 1), returned directly without prompting.

    Returns
    -------
    int
        Number of sessions to run (>= 1).
    """

    # Profile override
    if profile_session_count is not None:
        if isinstance(profile_session_count, int) and profile_session_count >= 1:
            return profile_session_count
        else:
            print(
                f"Warning: Profile session_count for '{exp_key}' is invalid; defaulting to 1."
            )
            return 1

    # Interactive prompt mode
    while True:
        raw = input(f"\nHow many sessions for '{exp_key}'? [1]: ").strip()
        if not raw:
            return 1
        if raw.isdigit() and int(raw) >= 1:
            return int(raw)
        print("Please enter an integer >= 1.")


def apply_per_session_profile_parameters(
    exp_key: str, profile: dict, session_index: int
) -> dict:
    """Read per-session parameter overrides from a profile for a given session index.

    Reads ``experiments_config[exp_key].session_parameters[session_index]``
    and returns ``{}`` if none exist.

    Parameters
    ----------
    exp_key : str
        Experiment key to look up in the profile.
    profile : dict
        Parsed profile dictionary.
    session_index : int
        Zero-based index of the session within the experiment.

    Returns
    -------
    dict
        Parameter overrides for this session, or an empty dict if none are
        defined.
    """
    exp_cfg = profile.get("experiments_config", {}).get(exp_key, {})
    sess_params = exp_cfg.get("session_parameters", [])
    if isinstance(sess_params, list) and 0 <= session_index < len(sess_params):
        entry = sess_params[session_index]
        if isinstance(entry, dict):
            return entry
    return {}


# -------------------------------
# Main
# -------------------------------
def main():
    """Entry point for the multi-experiment Bonsai launcher.

    Parses CLI arguments, loads configuration, optionally applies a profile,
    builds sessions interactively or from the profile, and launches Bonsai
    workflows with an optional lifealert control menu.

    Returns
    -------
    None
    """
    args = parse_args()

    cfg = load_config()
    experiments = cfg.get("EXPERIMENTS", {})
    if not experiments:
        print("No experiments configured yet in launcher_config.json.")
        sys.exit(0)

    # (0) Profile?
    profile = None
    auto_start_from_profile = None
    parallel_from_profile = None

    if args.experiment:
        profile_path = resolve_profile_path(args.experiment)
        if not profile_path:
            print(f"\nCould not find experiment config: '{args.experiment}'")
            # Collect all available configs from experiment_configs/
            available = sorted(
                p for p in EXPERIMENTS_DIR.glob("*")
                if p.suffix.lower() in (".json", ".yaml", ".yml") and p.is_file()
            )
            if not available:
                print("No experiment configs found in experiment_configs/.")
                sys.exit(1)
            print("\nAvailable experiment configs:")
            for i, p in enumerate(available, start=1):
                print(f"  {i}. {p.stem}")
            while True:
                raw = input("Select by number: ").strip()
                if raw.isdigit() and 1 <= int(raw) <= len(available):
                    profile_path = available[int(raw) - 1]
                    break
                print("Invalid selection. Try again.")
        try:
            profile = load_profile(profile_path)
            selected_keys, auto_start_from_profile, parallel_from_profile = (
                apply_profile_to_config(profile, cfg, profile_dir=profile_path.parent)
            )
            print(f"Loaded profile: {profile_path}")
        except Exception as e:
            print(f"Error loading profile: {e}")
            sys.exit(1)
    else:
        selected_keys = select_experiments(cfg)

    # Determine "all experiments"
    all_keys_sorted = sorted(experiments.keys())
    selected_is_all = set(selected_keys) == set(all_keys_sorted)

    # Prepare each experiment
    prepared_per_experiment = {}

    for exp_key in selected_keys:
        exp = experiments[exp_key]
        print(f"\n--- Configure: {exp_key} — {exp.get('name', '')} ---")

        if args.experiment:
            # From profile
            roots = exp.get("WorkflowRoot", [])
            if not isinstance(roots, list) or not roots:
                print(f"Profile must specify 'root' for experiment '{exp_key}'.")
                sys.exit(1)
            chosen_root = norm(roots[0])
            if not exists_path(chosen_root):
                print(f"Root path does not exist: {chosen_root}")
                sys.exit(1)

            wfs = exp.get("workflows", [])
            if not isinstance(wfs, list) or not wfs:
                print(f"Profile must specify 'workflow' for experiment '{exp_key}'.")
                sys.exit(1)

            wf = wfs[0]
            workflow_path = norm(
                wf if os.path.isabs(wf) else os.path.join(chosen_root, wf.lstrip("\\/"))
            )
            workflow_paths = [workflow_path]

        else:
            # Interactive mode
            chosen_root = ensure_workflow_root(exp_key, exp, cfg)
            workflow_paths = ensure_workflows(exp_key, exp, chosen_root, cfg)
            prompt_parameters_menu(exp_key, exp, chosen_root, cfg)

        base_params = expand_and_validate_parameters(exp_key, exp, chosen_root, cfg)
        bonsai_cmd_full = resolve_bonsai_cmd(exp_key, exp, chosen_root, cfg)

        # Path check
        print("\n=== Final Path Check ===")
        print(
            "Bonsai exe: ",
            bonsai_cmd_full,
            " [OK]" if exists_path(bonsai_cmd_full) else " [MISSING]",
        )
        for wf in workflow_paths:
            print("Workflow:  ", wf, " [OK]" if exists_path(wf) else " [MISSING]")
        for k, v in base_params.items():
            if is_probably_path(v) and "://" not in v and not v.startswith("@"):
                print(f"Param[{k}]:", v, " [OK]" if exists_path(v) else " [MISSING]")

        prepared_per_experiment[exp_key] = {
            "exp": exp,
            "root": chosen_root,
            "workflow": workflow_paths[0],
            "base_params": base_params,
            "bonsai": bonsai_cmd_full,
        }

    # ===============================================
    # Session-count rules (Option C + your overrides)
    # ===============================================

    def delphi_profile_session_count(exp_key):
        cfg_entry = profile.get("experiments_config", {}).get(exp_key, {})
        sc = cfg_entry.get("session_count")
        if isinstance(sc, int) and sc >= 1:
            return sc
        return 1

    def apply_profile_session_parameters(exp_key: str, session_index: int) -> dict:
        exp_cfg = profile.get("experiments_config", {}).get(exp_key, {})
        sess_array = exp_cfg.get("session_parameters", [])
        if isinstance(sess_array, list) and 0 <= session_index < len(sess_array):
            entry = sess_array[session_index]
            return entry if isinstance(entry, dict) else {}
        return {}

    # ========================================
    # BUILD SESSIONS
    # ========================================
    sessions_to_launch = []

    try:
        is_delphi_only = (
            len(selected_keys) == 1
            and "delphi" in selected_keys[0].lower()
            and "pirouette" not in selected_keys[0].lower()
        )
        is_pirouette_only = (
            len(selected_keys) == 1 and "pirouette" in selected_keys[0].lower()
        )
        is_all_experiments = selected_is_all

        # Shared subject/experimenter in all-mode
        if is_all_experiments:
            print("\n=== 'All experiments' mode: shared Experimenter/Subject ===")
            shared_exp = prompt("Experimenter name")
            shared_sub = prompt_subject_id()

        last_exp_name = ""

        for exp_key in selected_keys:
            info = prepared_per_experiment[exp_key]
            exp = info["exp"]
            root = info["root"]

            # === Determine session_count ===
            if args.experiment:
                if is_delphi_only:
                    session_count = delphi_profile_session_count(exp_key)
                elif is_pirouette_only:
                    session_count = 1
                elif is_all_experiments:
                    session_count = 1
                else:
                    session_count = 1  # Mixed-profile case
            else:
                if "delphi" in exp_key.lower() and "pirouette" not in exp_key.lower():
                    session_count = plan_sessions_for_experiment(exp_key)
                else:
                    session_count = 1

            # === Build each session ===
            for i in range(session_count):
                # Build session
                if is_all_experiments:
                    session = build_session_with_values(exp_key, shared_exp, shared_sub)
                else:
                    session = build_session(exp_key, experimenter_default=last_exp_name)

                last_exp_name = session["experimenter"] or last_exp_name

                # Save log
                sf = save_session_record(session)
                if sf:
                    print(f"  Session log: {sf}")

                # Start params = base
                params_abs = dict(info["base_params"])

                # =====================================
                # PROFILE: per-session parameter override
                # =====================================
                if args.experiment and is_delphi_only:
                    overrides = apply_profile_session_parameters(exp_key, i)
                    for key, val in overrides.items():
                        if is_probably_path(val):
                            abs_val = (
                                val
                                if os.path.isabs(val)
                                else os.path.join(root, val.lstrip("\\/"))
                            )
                            val = norm(abs_val)
                        params_abs[key] = val
                    if overrides:
                        print(
                            f"  Applied per-session profile overrides (session {i + 1}): {overrides}"
                        )

                # Pirouette temp-file reset
                if "pirouette" in exp_key.lower():
                    zero_pirouette_temp_files(root)

                # Per-session schema copies (Delphi)
                if "delphi" in exp_key.lower() and "pirouette" not in exp_key.lower():
                    key = "HardwareSettings"
                    if key in params_abs and is_probably_path(params_abs[key]):
                        src = params_abs[key]
                        params_abs[key] = copy_and_patch_hardware_schema(
                            src, session["subject_id"], session["start_utc"]
                        )

                # Per-session schema copies (Pirouette)
                if "pirouette" in exp_key.lower():
                    if is_all_experiments:
                        exp_val = "delphi_pirouette"
                    else:
                        exp_val = "pirouette"
                    key = "SessionPath"
                    if key in params_abs and is_probably_path(params_abs[key]):
                        src = params_abs[key]
                        params_abs[key] = copy_and_patch_aind_session_json(
                            src,
                            session["subject_id"],
                            session["start_utc"],
                            experimenter=session["experimenter"],
                            experiment_value=exp_val,
                        )

                # Named overrides (stored in config)
                named = build_session_overrides_if_available(exp, session)
                if named:
                    params_abs.update(named)

                # Interactive overrides only in interactive mode
                if not args.experiment:
                    if prompt_yes_no(
                        "Override parameters for THIS SESSION?", default_yes=False
                    ):
                        params_abs = prompt_per_session_overrides(exp, root, params_abs)

                # Patch Bonsai XML default paths for the local machine in-place.
                # Bonsai deserialises XML before applying -p overrides, so any
                # stale absolute paths baked into the file as defaults must be
                # remapped first. The original is saved to BONSAI_ORIGINALS and
                # restored on exit — no new workflow files are created.
                patch_bonsai_xml(info["workflow"], root)

                # Add to launch list
                sessions_to_launch.append(
                    {
                        "exp_key": exp_key,
                        "bonsai_cmd": info["bonsai"],
                        "workflow": info["workflow"],
                        "params": params_abs,
                        "session": session,
                    }
                )

        # Auto-save generated config for recall
        if not args.experiment:
            saved_cfg_path = save_generated_config(
                selected_keys,
                prepared_per_experiment,
                sessions_to_launch,
                auto_start=False,   # will be confirmed below; use False as placeholder
                parallel=False,
            )
            if saved_cfg_path:
                print(f"\nConfig saved for recall: {saved_cfg_path}")

        # === Global launch choice ===
        if auto_start_from_profile is None:
            auto_start = prompt_launch_mode()
        else:
            auto_start = bool(auto_start_from_profile)
            print(f"\nAuto-start (from profile): {auto_start}")

        if parallel_from_profile is None:
            parallel = prompt_run_mode()
        else:
            parallel = bool(parallel_from_profile)
            print(f"Parallel (from profile): {parallel}")

        # === Summary & confirm ===
        print_prelaunch_summary(sessions_to_launch, auto_start, parallel)
        if not prompt_yes_no("Proceed to launch?", default_yes=True):
            print("Cancelled.")
            sys.exit(0)

        # === Launch Bonsai FIRST ===
        WORKFLOW_PROCS.clear()
        if parallel:
            # Open all workflows simultaneously — no delay between launches
            for s in sessions_to_launch:
                p = launch_bonsai(
                    s["bonsai_cmd"], s["workflow"], s["params"], auto_start
                )
                WORKFLOW_PROCS.append(p)
        else:
            # Open workflows one after another with a brief stagger so each
            # window has time to appear before the next one starts.
            for idx, s in enumerate(sessions_to_launch):
                p = launch_bonsai(
                    s["bonsai_cmd"], s["workflow"], s["params"], auto_start
                )
                WORKFLOW_PROCS.append(p)
                if idx < len(sessions_to_launch) - 1:
                    time.sleep(1.5)  # stagger — do NOT wait for close

        # === Lifealert control menu ===
        pir_selected = any(
            "pirouette" in s["exp_key"].lower() for s in sessions_to_launch
        )

        # === CONTROL MENU ===
        while True:
            try:
                print("\n=== Control Menu ===")
                print("  1. Start lifealert")
                print("  2. Stop lifealert")
                print("  3. Show status")
                print("  4. Wait for workflows")
                print("  5. Exit & cleanup")
                if not pir_selected:
                    print("  (Pirouette not selected — lifealert disabled)")

                sel = input("Select: ").strip()

                if sel == "1":
                    if pir_selected:
                        start_lifealert_with_menu(cfg, prepared_per_experiment)
                    else:
                        print("Pirouette not selected.")

                elif sel == "2":
                    stop_lifealert()  # Updated function will hard-kill uv + children

                elif sel == "3":
                    show_status()

                elif sel == "4":
                    wait_for_workflows()

                elif sel == "5":
                    break

                else:
                    print("Invalid selection.")

            except KeyboardInterrupt:
                print("\nCtrl+C detected — exiting control menu and cleaning up...")
                break

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        cleanup_temp_files_and_processes()


if __name__ == "__main__":
    main()
