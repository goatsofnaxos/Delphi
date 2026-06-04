"""Audit docstrings and regenerate / build the MkDocs API documentation.

This script has two jobs:

1. **Docstring audit** — scans every ``.py`` file in the project for functions
   and methods that are missing docstrings, ``Parameters`` sections, or
   ``Returns`` sections, and prints a structured report.

2. **Docs regeneration** — rewrites every ``docs/api/*.md`` and
   ``docs/scripts/**/*.md`` file from a manifest so the navigation stays in
   sync with the source.  Existing hand-written prose in ``docs/index.md`` and
   ``docs/*/index.md`` is **not** overwritten.

3. **Site build** — optionally runs ``mkdocs build`` (or ``mkdocs serve``) so
   you can preview or publish the rendered site.

Usage::

    # Audit only (no files written)
    python scripts/update_api_docs.py --audit

    # Audit + regenerate .md files
    python scripts/update_api_docs.py --regen

    # Audit + regenerate + build site into site/
    python scripts/update_api_docs.py --build

    # Audit + regenerate + serve with live-reload at http://localhost:8000
    python scripts/update_api_docs.py --serve

    # Skip the audit and just build
    python scripts/update_api_docs.py --build --no-audit

Exit codes
----------
0   All checks passed (or --no-audit was set).
1   One or more docstring issues found (only when --audit is active).
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from typing import List


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS_ROOT = REPO_ROOT / "docs"

# Private (single-underscore) functions that should still be documented
# because they are exposed in the API pages.
PRIVATE_ALLOWLIST = {
    "_parse_args",
    "_build_parser",
    "_common",
}

# Files / directories to skip during the audit.
AUDIT_SKIP_PATTERNS = {".venv", "sandbox", "__pycache__", "site", ".git"}


# ---------------------------------------------------------------------------
# Docstring auditor
# ---------------------------------------------------------------------------


@dataclass
class DocIssue:
    """A single docstring issue found during the audit.

    Attributes
    ----------
    file:
        Path to the source file, relative to *REPO_ROOT*.
    line:
        Line number of the function definition.
    name:
        Function or method name.
    missing:
        List of missing items, e.g. ``["Parameters", "Returns"]`` or
        ``["MISSING DOCSTRING"]``.
    """

    file: pathlib.Path
    line: int
    name: str
    missing: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        tag = ", ".join(self.missing)
        return f"  L{self.line:4d}  {self.name}()  -- missing: {tag}"


def _should_skip(path: pathlib.Path) -> bool:
    """Return ``True`` if *path* should be excluded from the audit.

    Parameters
    ----------
    path:
        File path to test (relative or absolute).

    Returns
    -------
    bool
        ``True`` when any component of *path* matches :data:`AUDIT_SKIP_PATTERNS`.
    """
    return any(part in AUDIT_SKIP_PATTERNS for part in path.parts)


def _is_private(name: str) -> bool:
    """Return ``True`` for private names not in the allowlist.

    Parameters
    ----------
    name:
        Function or method name.

    Returns
    -------
    bool
        ``True`` when *name* starts with ``_`` but is not in
        :data:`PRIVATE_ALLOWLIST`.
    """
    return name.startswith("_") and name not in PRIVATE_ALLOWLIST


def audit_file(path: pathlib.Path) -> List[DocIssue]:
    """Audit a single Python file and return all docstring issues.

    Checks every non-dunder function and method for:

    - Missing docstring entirely.
    - Missing ``Parameters`` section when the function has non-self arguments.
    - Missing ``Returns`` section when the function has a non-``None`` return
      annotation.

    Parameters
    ----------
    path:
        Path to the Python source file.

    Returns
    -------
    list of DocIssue
        All issues found in *path*.  Empty list when the file is clean.
    """
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        print(f"  Warning: could not parse {path}: {exc}", file=sys.stderr)
        return []

    issues: List[DocIssue] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Skip dunder methods and non-allowlisted private helpers.
        if node.name.startswith("__") and node.name.endswith("__"):
            continue
        if _is_private(node.name):
            continue

        doc = ast.get_docstring(node) or ""
        missing: List[str] = []

        if not doc:
            issues.append(
                DocIssue(
                    file=path.relative_to(REPO_ROOT),
                    line=node.lineno,
                    name=node.name,
                    missing=["MISSING DOCSTRING"],
                )
            )
            continue

        # Parameters check
        all_args = (
            [a.arg for a in node.args.posonlyargs]
            + [a.arg for a in node.args.args if a.arg != "self"]
            + ([node.args.vararg.arg] if node.args.vararg else [])
            + [a.arg for a in node.args.kwonlyargs]
            + ([node.args.kwarg.arg] if node.args.kwarg else [])
        )
        if all_args:
            has_params = any(
                kw in doc for kw in ("Parameters", "Args", ":param ", "Parameters\n")
            )
            if not has_params:
                missing.append("Parameters")

        # Returns check
        has_return_ann = node.returns is not None and not (
            isinstance(node.returns, ast.Constant) and node.returns.value is None
        )
        if has_return_ann:
            has_returns = any(
                kw in doc
                for kw in ("Returns", ":returns:", ":rtype:", "Return\n", "Returns\n")
            )
            if not has_returns:
                missing.append("Returns")

        if missing:
            issues.append(
                DocIssue(
                    file=path.relative_to(REPO_ROOT),
                    line=node.lineno,
                    name=node.name,
                    missing=missing,
                )
            )

    return issues


def run_audit(verbose: bool = True) -> int:
    """Audit all Python files in the project and print a report.

    Parameters
    ----------
    verbose:
        When ``True``, print a per-file breakdown.  When ``False``, only
        print the summary line.

    Returns
    -------
    int
        Total number of issues found.  ``0`` means the project is clean.
    """
    files = sorted(REPO_ROOT.rglob("*.py"), key=str)
    files = [f for f in files if not _should_skip(f.relative_to(REPO_ROOT))]

    all_issues: List[DocIssue] = []
    for path in files:
        file_issues = audit_file(path)
        if file_issues:
            if verbose:
                print(f"\n{path.relative_to(REPO_ROOT)}")
                for issue in file_issues:
                    print(issue)
            all_issues.extend(file_issues)

    if all_issues:
        print(f"\n{'-' * 60}")
        print(f"  {len(all_issues)} docstring issue(s) found across {len(files)} files.")
        print(f"{'-' * 60}")
    else:
        print(f"  Docstring audit passed — {len(files)} files checked, 0 issues.")

    return len(all_issues)


# ---------------------------------------------------------------------------
# Docs page manifest and regeneration
# ---------------------------------------------------------------------------

# Each entry: (output_path_relative_to_docs, page_title, list_of_::: directives)
# 'directives' is a list of (heading, list_of_autoref_strings).
# An empty heading string means no heading is emitted before the refs.

_API_PAGES = [
    (
        "api/poke_metrics.md",
        "poke_metrics",
        "Behavioral metrics for poke-port experiments.",
        [
            ("Data loading", ["delphi_data.poke_metrics.load_csvs_with_subject_id"]),
            ("Odor-change detection", ["delphi_data.poke_metrics.odor_change_events"]),
            ("Poke-rate estimators", [
                "delphi_data.poke_metrics.poke_rate_exponential_decay_binned",
                "delphi_data.poke_metrics.poke_rate_exponential_decay_sliding",
                "delphi_data.poke_metrics.poke_rate_exponential_decay_convolution",
            ]),
            ("Session-level poke statistics", ["delphi_data.poke_metrics.compute_poke_stats"]),
            ("Windowed poke-rate extraction", [
                "delphi_data.poke_metrics.extract_poke_rate_windows",
                "delphi_data.poke_metrics.extract_poke_rate_relative_to_odor_switch",
            ]),
            ("Fold-change and delta-rate metrics", [
                "delphi_data.poke_metrics.raw_fold_change",
                "delphi_data.poke_metrics.log_fold_change",
                "delphi_data.poke_metrics.compute_fold_change_day0_vs_baseline",
                "delphi_data.poke_metrics.compute_fold_change_day0_vs_baseline_ratio",
            ]),
            ("Inter-poke interval threshold estimation", [
                "delphi_data.poke_metrics.estimate_robust_ipi_threshold",
                "delphi_data.poke_metrics.compute_ipi_thresholds",
            ]),
            ("Bout-centric baseline fold change", [
                "delphi_data.poke_metrics.filter_baseline_pokes_by_ipi",
                "delphi_data.poke_metrics.compute_timeseries_fold_change_exponential",
            ]),
            ("Cumulative poke counts", [
                "delphi_data.poke_metrics.compute_windowed_cumulative_poke_count"
            ]),
            ("Poke-duration analysis", [
                "delphi_data.poke_metrics.extract_durations",
                "delphi_data.poke_metrics.compute_poke_duration_comparison",
            ]),
        ],
    ),
    (
        "api/visualization.md",
        "visualization",
        "Visualization functions for poke-port behavioral data.\n\n"
        "All functions return a `matplotlib.figure.Figure` object.",
        [
            ("Legend helpers", ["delphi_data.visualization.build_day_legend"]),
            ("Session overview", [
                "delphi_data.visualization.plot_poke_rate_timeseries",
                "delphi_data.visualization.plot_ipi_distributions",
                "delphi_data.visualization.plot_poke_duration_by_odor",
                "delphi_data.visualization.plot_daily_poke_count",
            ]),
            ("Odor-change windows (poke-event aligned)", [
                "delphi_data.visualization.plot_multiday_poke_rate_windows"
            ]),
            ("Odor-switch aligned (2nd-poke alignment)", [
                "delphi_data.visualization.plot_odor_switch_aligned_poke_rates",
                "delphi_data.visualization.plot_odor_switch_aligned_rate_difference",
                "delphi_data.visualization.plot_odor_switch_aligned_fold_change",
                "delphi_data.visualization.plot_odor_switch_aligned_fold_change_log",
            ]),
            ("Bout-centric fold change", [
                "delphi_data.visualization.plot_odor_switch_aligned_poke_rates_from_fc_exp",
                "delphi_data.visualization.plot_odor_switch_aligned_fold_change_exponential",
            ]),
            ("Cumulative counts and duration", [
                "delphi_data.visualization.plot_cumulative_poke_counts",
                "delphi_data.visualization.plot_poke_duration_comparison",
            ]),
        ],
    ),
    (
        "api/video_processing.md",
        "video_processing",
        "Poke-triggered video clip extraction for Delphi behavioral sessions.\n\n"
        "!!! note \"System requirements\"\n    `ffmpeg` and `ffprobe` must be on `PATH`.\n\n"
        "!!! note \"Python requirements\"\n    ```bash\n    pip install delphi-data[video]\n    ```",
        [
            ("Session-level pipeline", [
                "delphi_data.video_processing.process_session",
                "delphi_data.video_processing.process_chunk",
            ]),
            ("Clip export", [
                "delphi_data.video_processing.export_clip",
                "delphi_data.video_processing.relaxed_trigger_clip",
            ]),
            ("Session discovery", [
                "delphi_data.video_processing.find_session_dirs",
                "delphi_data.video_processing.get_subject_id",
                "delphi_data.video_processing.get_chunk_timestamps",
                "delphi_data.video_processing.load_camera",
            ]),
            ("Chunk management", ["delphi_data.video_processing.delete_port_chunk"]),
            ("CLI entry point", ["delphi_data.video_processing.main"]),
            ("Constants", [
                "delphi_data.video_processing.HALF_WINDOW",
                "delphi_data.video_processing.DELETE_BUFFER_HRS",
            ]),
        ],
    ),
    (
        "api/ingestion.md",
        "ingestion",
        "Raw Harp hardware stream ingestion pipeline.",
        [
            ("Public API", ["delphi_data.ingestion.ingest"]),
            ("Pipeline internals", [
                "delphi_data.ingestion.build_dataframe",
                "delphi_data.ingestion.parse_data",
            ]),
            ("Readers and data loading", [
                "delphi_data.ingestion.build_readers",
                "delphi_data.ingestion.load_data",
            ]),
            ("Register and odor helpers", [
                "delphi_data.ingestion.parse_register_map",
                "delphi_data.ingestion.extract_constant_registers",
                "delphi_data.ingestion.build_odor_map",
            ]),
            ("Utilities", ["delphi_data.ingestion.get_package_root"]),
        ],
    ),
    (
        "api/curation.md",
        "curation",
        "Session directory consolidation utilities.",
        [
            ("Main entry point", ["delphi_data.curation.consolidate_session_runs"]),
            ("Directory helpers", [
                "delphi_data.curation.collect_run_dirs",
                "delphi_data.curation.find_earliest_run",
                "delphi_data.curation.is_timestamp_dir",
            ]),
            ("File-move helpers", [
                "delphi_data.curation.move_contents_with_progress",
                "delphi_data.curation.fast_move_with_optional_checksum",
                "delphi_data.curation.same_filesystem",
            ]),
            ("Cleanup helpers", [
                "delphi_data.curation.remove_all_empty_dirs",
                "delphi_data.curation.count_files",
                "delphi_data.curation.compute_sha256",
            ]),
        ],
    ),
    (
        "api/config.md",
        "config",
        "Firmware register set definitions and resolution logic.",
        [
            ("Register resolution", [
                "delphi_data.config.get_all_registers",
                "delphi_data.config.resolve_firmware_registers",
            ]),
            ("Constants", [
                "delphi_data.config.CORE_REGISTERS",
                "delphi_data.config.FIRMWARE_CONFIG",
                "delphi_data.config.VIDEO_CONFIG",
                "delphi_data.config.DEFAULT_TIMING_REGISTERS",
            ]),
        ],
    ),
    (
        "api/cli.md",
        "cli",
        "`delphi-data` command-line interface.",
        [
            ("Entry point", ["delphi_data.cli.main"]),
            ("Argument parser", ["delphi_data.cli._build_parser"]),
        ],
    ),
]

_SCRIPT_PAGES = [
    (
        "scripts/full_processing_pipeline.md",
        "full_processing_pipeline",
        "Full three-step processing pipeline for a single Delphi behavioral session.",
        [("", [
            "full_processing_pipeline.run_pipeline",
            "full_processing_pipeline.run_build_dataset",
            "full_processing_pipeline.run_create_clips",
            "full_processing_pipeline.run_snapshot",
            "full_processing_pipeline._parse_args",
        ])],
        "```bash\ndelphi-data pipeline\ndelphi-data pipeline --skip-clips\n```",
    ),
    (
        "scripts/build_dataset.md",
        "build_dataset",
        "Build a Delphi behavioral dataset CSV from a raw session directory.",
        [("", ["build_dataset.build_dataset", "build_dataset._parse_args"])],
        "```bash\ndelphi-data build-dataset --data-root /path/to/run --firmware 0.1.0\n```",
    ),
    (
        "scripts/data_snapshot.md",
        "data_snapshot",
        "Router that dispatches to the correct experiment-specific snapshot module.",
        [("", ["data_snapshot.main"])],
        "```bash\ndelphi-data snapshot --experiment bonhoeffer --data-root /path/to/run\n```",
    ),
    (
        "scripts/create_poke_clips.md",
        "create_poke_clips",
        "Thin wrapper around :mod:`delphi_data.video_processing`.\n\n"
        "All logic lives in the package module.  See the\n"
        "[video_processing API page](../api/video_processing.md) for the full reference.",
        [("", ["delphi_data.video_processing.main"])],
        '!!! note "System requirements"\n    `ffmpeg` and `ffprobe` must be on `PATH`.\n\n'
        '!!! note "Python requirements"\n    ```bash\n    pip install delphi-data[video]\n    ```',
    ),
    (
        "scripts/snapshots/bonhoeffer.md",
        "bonhoeffer",
        "Behavioral snapshot for Bonhoeffer olfactory learning experiments.",
        [("", ["snapshots.bonhoeffer.run_snapshot", "snapshots.bonhoeffer._parse_args"])],
        "```bash\ndelphi-data snapshot --experiment bonhoeffer --data-root /path/to/run\n```",
    ),
    (
        "scripts/snapshots/common.md",
        "_common",
        "Shared utilities for all experiment snapshot scripts.",
        [
            ("Data loading", [
                "snapshots._common.load_dataset",
                "snapshots._common.infer_subject_id",
                "snapshots._common.build_odor_mapping",
            ]),
            ("Poke-stats computation", ["snapshots._common.build_poke_stats"]),
            ("Figure saving", [
                "snapshots._common.save_figure",
                "snapshots._common.try_save",
            ]),
        ],
        "",
    ),
]


def _render_api_page(title: str, description: str, sections: list) -> str:
    """Render a single API documentation page as a Markdown string.

    Parameters
    ----------
    title:
        Page / module title (used as the H1 heading).
    description:
        Short description placed below the title.
    sections:
        List of ``(heading, [autoref, ...])`` tuples.  An empty heading
        string emits no ``##`` heading.

    Returns
    -------
    str
        Complete Markdown content for the page.
    """
    lines = [f"# {title}", "", description, ""]
    for heading, autorefs in sections:
        if heading:
            lines += ["---", "", f"## {heading}", ""]
        for ref in autorefs:
            # Private members need the filter override
            if ref.rsplit(".", 1)[-1].startswith("_"):
                lines += [f"::: {ref}", "    options:", "      filters: []", ""]
            else:
                lines += [f"::: {ref}", ""]
    return "\n".join(lines)


def _render_script_page(
    title: str,
    description: str,
    sections: list,
    preamble: str,
) -> str:
    """Render a script documentation page as a Markdown string.

    Parameters
    ----------
    title:
        Page title (H1 heading).
    description:
        Short description placed below the title.
    sections:
        List of ``(heading, [autoref, ...])`` tuples.
    preamble:
        Optional Markdown block inserted before the ``:::`` directives
        (e.g. a code example or admonition).

    Returns
    -------
    str
        Complete Markdown content for the page.
    """
    lines = [f"# {title}", "", description, ""]
    if preamble:
        lines += [preamble, ""]
    lines += ["---", ""]
    for heading, autorefs in sections:
        if heading:
            lines += [f"## {heading}", ""]
        for ref in autorefs:
            if ref.rsplit(".", 1)[-1].startswith("_"):
                lines += [f"::: {ref}", "    options:", "      filters: []", ""]
            else:
                lines += [f"::: {ref}", ""]
    return "\n".join(lines)


def regenerate_docs(dry_run: bool = False) -> int:
    """Regenerate all API and script documentation pages from the manifest.

    Index pages (``*/index.md``) and ``docs/index.md`` are **not** touched.

    Parameters
    ----------
    dry_run:
        When ``True``, print what would be written but do not write any files.

    Returns
    -------
    int
        Number of files written (or that would be written in dry-run mode).
    """
    written = 0

    # API pages
    for rel_path, title, description, sections in _API_PAGES:
        content = _render_api_page(title, description, sections)
        out_path = DOCS_ROOT / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if dry_run:
            print(f"  [dry-run] would write {out_path.relative_to(REPO_ROOT)}")
        else:
            out_path.write_text(content, encoding="utf-8")
            print(f"  Written: {out_path.relative_to(REPO_ROOT)}")
        written += 1

    # Script pages
    for rel_path, title, description, sections, preamble in _SCRIPT_PAGES:
        content = _render_script_page(title, description, sections, preamble)
        out_path = DOCS_ROOT / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if dry_run:
            print(f"  [dry-run] would write {out_path.relative_to(REPO_ROOT)}")
        else:
            out_path.write_text(content, encoding="utf-8")
            print(f"  Written: {out_path.relative_to(REPO_ROOT)}")
        written += 1

    return written


# ---------------------------------------------------------------------------
# MkDocs build / serve
# ---------------------------------------------------------------------------


def run_mkdocs(command: str) -> int:
    """Run a mkdocs sub-command (``build`` or ``serve``) from the project root.

    Parameters
    ----------
    command:
        The mkdocs sub-command to run, e.g. ``"build"`` or ``"serve"``.

    Returns
    -------
    int
        The process return code.  ``0`` indicates success.
    """
    cmd = [sys.executable, "-m", "mkdocs", command]
    print(f"\nRunning: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    return result.returncode


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments for the update_api_docs script.

    Parameters
    ----------
    argv:
        Argument list.  ``None`` reads from ``sys.argv[1:]``.

    Returns
    -------
    argparse.Namespace
        Parsed namespace with ``audit``, ``regen``, ``build``, ``serve``,
        ``no_audit``, and ``dry_run`` attributes.
    """
    parser = argparse.ArgumentParser(
        description=textwrap.dedent("""\
            Audit docstrings and regenerate / build the MkDocs API documentation.

            Examples
            --------
            Audit only (no files written):
              python scripts/update_api_docs.py --audit

            Regenerate .md files:
              python scripts/update_api_docs.py --regen

            Full pipeline (audit + regen + build):
              python scripts/update_api_docs.py --build

            Live-reload preview:
              python scripts/update_api_docs.py --serve
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--audit",
        action="store_true",
        help="Run the docstring audit and report issues (no files written).",
    )
    mode.add_argument(
        "--regen",
        action="store_true",
        help="Regenerate all API .md pages from the manifest (no mkdocs build).",
    )
    mode.add_argument(
        "--build",
        action="store_true",
        help="Audit + regenerate + run 'mkdocs build' to produce the site/ directory.",
    )
    mode.add_argument(
        "--serve",
        action="store_true",
        help="Audit + regenerate + run 'mkdocs serve' for live-reload preview.",
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        default=False,
        help="Skip the docstring audit step (applies with --build or --serve).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="With --regen/--build/--serve: print what would be written, don't write.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> None:
    """Entry point for the update_api_docs script.

    Parameters
    ----------
    argv:
        Argument list.  ``None`` reads from ``sys.argv[1:]``.
    """
    args = _parse_args(argv)
    exit_code = 0

    # --- Audit ---
    run_audit_step = not args.no_audit
    if args.audit:
        n_issues = run_audit()
        sys.exit(1 if n_issues else 0)

    if run_audit_step:
        print("=== Docstring audit ===")
        n_issues = run_audit()
        if n_issues:
            print(
                "\nFix the issues above before regenerating docs, "
                "or pass --no-audit to skip.\n"
            )
            exit_code = 1
        else:
            print()

    # --- Regenerate ---
    if args.regen or args.build or args.serve:
        print("=== Regenerating docs pages ===")
        n = regenerate_docs(dry_run=args.dry_run)
        print(f"  {n} pages {'would be ' if args.dry_run else ''}written.\n")

    # --- Build / serve ---
    if not args.dry_run:
        if args.build:
            rc = run_mkdocs("build")
            if rc != 0:
                exit_code = rc
        elif args.serve:
            run_mkdocs("serve")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
