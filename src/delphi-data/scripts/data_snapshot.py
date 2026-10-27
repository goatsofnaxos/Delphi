"""Router that dispatches to an experiment-specific snapshot script.

Usage::

    python data_snapshot.py --experiment bonhoeffer --data-root /path/to/run_dir
    python data_snapshot.py --experiment bonhoeffer --data-root /path/to/run_dir --tau 600

This script is also available via the installed ``delphi-data`` CLI::

    delphi-data snapshot --experiment bonhoeffer --data-root /path/to/run_dir

Available experiment types are registered in
:data:`snapshots.REGISTRY`.  To add a new type, create
``scripts/snapshots/<name>.py`` with a ``run_snapshot`` function and add the
name to the registry.

Passing extra flags
-------------------
All flags after ``--experiment`` are forwarded to the experiment module's
``run_snapshot`` function.  Run::

    python data_snapshot.py --experiment <name> --help

to see the full option list for a specific experiment.
"""

from __future__ import annotations

import argparse
import importlib
import pathlib
import sys

# Ensure both the package root and the scripts/ directory are importable.
_here = pathlib.Path(__file__).resolve().parent
_pkg_root = _here.parent
for _p in [str(_pkg_root), str(_here)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from snapshots import REGISTRY


def main(argv=None) -> None:
    """Parse the ``--experiment`` flag and delegate to the matching snapshot module.

    Parameters
    ----------
    argv:
        Argument list.  ``None`` reads from ``sys.argv[1:]``.
    """
    # Pre-parse just the --experiment flag so we can load the right module
    # and let it own the rest of the argument parsing.
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument(
        "--experiment",
        choices=list(REGISTRY.keys()),
        default=None,
        metavar="EXPERIMENT",
    )
    pre_args, remaining = pre_parser.parse_known_args(argv)

    if pre_args.experiment is None:
        # No experiment specified — run the common/universal snapshot.
        pre_args.experiment = "common"

    module_path = REGISTRY[pre_args.experiment]
    mod = importlib.import_module(module_path)

    # Delegate fully to the experiment module's own argument parser so that
    # --help shows the experiment-specific flags.
    mod_argv = remaining if argv is not None else None
    if hasattr(mod, "_parse_args"):
        # Always pass `remaining` explicitly so the experiment module's parser
        # never falls back to sys.argv (which still contains --experiment).
        args = mod._parse_args(remaining)
        kwargs = {
            k: v for k, v in vars(args).items()
            if v is not None and k != "experiment"
        }
        mod.run_snapshot(**kwargs)
    else:
        # Fallback: call run_snapshot with just data_root if the module
        # doesn't expose _parse_args.
        fallback = argparse.ArgumentParser(add_help=True)
        fallback.add_argument("--data-root", required=True)
        fb_args = fallback.parse_args(remaining)
        mod.run_snapshot(data_root=fb_args.data_root)


if __name__ == "__main__":
    main()
