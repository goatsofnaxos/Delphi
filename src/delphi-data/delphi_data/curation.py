import hashlib
import os
import pathlib
import shutil
from datetime import datetime

from tqdm import tqdm


def is_timestamp_dir(name: str) -> bool:
    """Return ``True`` if *name* matches the ``YYYY-MM-DDTHH-MM-SS`` format.

    Parameters
    ----------
    name:
        Directory name to test.

    Returns
    -------
    bool
        ``True`` when *name* can be parsed as ``%Y-%m-%dT%H-%M-%S``.
    """
    try:
        datetime.strptime(name, "%Y-%m-%dT%H-%M-%S")
        return True
    except ValueError:
        return False


def compute_sha256(file_path: str, chunk_size: int = 1024 * 1024) -> str:
    """Compute the SHA-256 hex digest of a file.

    Parameters
    ----------
    file_path:
        Absolute or relative path to the file.
    chunk_size:
        Read chunk size in bytes (default 1 MiB).

    Returns
    -------
    str
        Lowercase hexadecimal SHA-256 digest.
    """
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def same_filesystem(path1: str, path2: str) -> bool:
    """Check if two paths reside on the same filesystem.

    Parameters
    ----------
    path1:
        First path (must exist).
    path2:
        Second path.  Its parent directory is used when *path2* itself does
        not yet exist.

    Returns
    -------
    bool
        ``True`` when both paths share the same ``st_dev`` device number.
    """
    try:
        return os.stat(path1).st_dev == os.stat(os.path.dirname(path2)).st_dev
    except FileNotFoundError:
        return os.stat(path1).st_dev == os.stat(os.path.dirname(path2)).st_dev


def fast_move_with_optional_checksum(src_file: str, dst_file: str) -> None:
    """Move a file efficiently, verifying integrity only on cross-filesystem moves.

    On a same-filesystem move an atomic ``os.replace`` is used (instant, no
    hashing).  On a cross-filesystem move the file is copied via
    ``shutil.move`` and SHA-256 digests are compared; a warning is printed on
    mismatch.

    Parameters
    ----------
    src_file:
        Absolute path of the source file.
    dst_file:
        Absolute path of the destination file.
    """
    if same_filesystem(src_file, dst_file):
        # Fast path (no copy, no hashing)
        os.replace(src_file, dst_file)
        return

    # Cross-filesystem: verify integrity
    src_hash = compute_sha256(src_file)

    shutil.move(src_file, dst_file)

    dst_hash = compute_sha256(dst_file)

    if src_hash != dst_hash:
        print(f"\nWARNING: checksum mismatch: {dst_file}")


def collect_run_dirs(session_root: str) -> list:
    """Return a list of timestamp-named run directories inside *session_root*.

    Parameters
    ----------
    session_root:
        Path to the session directory that may contain multiple run sub-dirs.

    Returns
    -------
    list of str
        Absolute paths of run directories whose names match the timestamp format.
    """
    return [
        os.path.join(session_root, d)
        for d in os.listdir(session_root)
        if os.path.isdir(os.path.join(session_root, d)) and is_timestamp_dir(d)
    ]


def find_earliest_run(run_dirs: list) -> str:
    """Return the run directory with the lexicographically earliest name.

    Parameters
    ----------
    run_dirs:
        List of run-directory paths (names must be ISO-format timestamps).

    Returns
    -------
    str
        Path of the earliest run directory.
    """
    return min(run_dirs, key=lambda p: os.path.basename(p))


def count_files(directory: str) -> int:
    """Count all files recursively under *directory*.

    Parameters
    ----------
    directory:
        Root directory to walk.

    Returns
    -------
    int
        Total number of files found.
    """
    total = 0
    for _, _, files in os.walk(directory):
        total += len(files)
    return total


def move_contents_with_progress(src_dir: str, dst_dir: str) -> None:
    """Recursively move the contents of *src_dir* into *dst_dir* with a progress bar.

    Uses :func:`fast_move_with_optional_checksum` for each file, so same-
    filesystem moves are instant while cross-filesystem moves are checksum-
    verified.  Existing destination files are skipped.

    Parameters
    ----------
    src_dir:
        Source directory whose contents will be moved.
    dst_dir:
        Destination directory.  Created automatically if it does not exist.
    """
    total_files = count_files(src_dir)

    if total_files == 0:
        return

    with tqdm(
        total=total_files,
        desc=f"Consolidating data: Moving {os.path.basename(src_dir)}",
        unit="file",
    ) as pbar:
        for root, _, files in os.walk(src_dir):
            rel_path = os.path.relpath(root, src_dir)
            target_root = os.path.join(dst_dir, rel_path)

            os.makedirs(target_root, exist_ok=True)

            for file in files:
                src_file = os.path.join(root, file)
                dst_file = os.path.join(target_root, file)

                if os.path.exists(dst_file):
                    pbar.update(1)
                    continue

                try:
                    fast_move_with_optional_checksum(src_file, dst_file)
                except Exception as e:
                    print(f"\nERROR moving {src_file}: {e}")

                pbar.update(1)


def remove_all_empty_dirs(directory: str) -> None:
    """Remove all empty directories under *directory*, including nested empties.

    The walk is repeated until no more empty directories remain, so directories
    that become empty only after their children are removed are also deleted.

    Parameters
    ----------
    directory:
        Root directory to clean up.  The directory itself is removed if it
        ends up empty.
    """
    removed_any = True

    while removed_any:
        removed_any = False
        for root, dirs, files in os.walk(directory, topdown=False):
            if not dirs and not files:
                try:
                    os.rmdir(root)
                    removed_any = True
                except OSError:
                    pass


def consolidate_session_runs(session_root: str) -> None:
    """Consolidate multiple run sub-directories in *session_root* into the earliest one.

    Identifies all timestamp-named run directories, moves the contents of
    every later run into the earliest run using
    :func:`move_contents_with_progress`, then removes empty directories.

    Parameters
    ----------
    session_root:
        Path to the session directory containing one or more timestamp-named
        run sub-directories (e.g. ``2026-03-20T20-23-37``).

    Raises
    ------
    ValueError
        If *session_root* is not a valid directory.
    """

    session_root = os.path.abspath(session_root)

    if not os.path.isdir(session_root):
        raise ValueError(f"Invalid session root: {session_root}")

    run_dirs = collect_run_dirs(session_root)

    if len(run_dirs) < 2:
        print("Nothing to do (need at least 2 run directories).")
        return

    earliest_run = find_earliest_run(run_dirs)
    print(f"Earliest run: {earliest_run}")

    for run_dir in run_dirs:
        if run_dir == earliest_run:
            continue

        print(f"\nProcessing: {run_dir}")
        move_contents_with_progress(run_dir, earliest_run)

        # Cleanup empty structure
        remove_all_empty_dirs(run_dir)

        # Remove run directory if empty
        if os.path.exists(run_dir):
            try:
                if not os.listdir(run_dir):
                    os.rmdir(run_dir)
                    print(f"Removed empty run dir: {run_dir}")
                else:
                    remove_all_empty_dirs(run_dir)
                    if not os.listdir(run_dir):
                        os.rmdir(run_dir)
                        print(f"Removed empty run dir: {run_dir}")
            except OSError:
                pass

    print("\nConsolidation complete.")


# ---------------------------------------------------------------------------
# Metadata consolidation
# ---------------------------------------------------------------------------

#: File glob patterns that belong inside ``behavior/metadata/`` but are
#: sometimes written to a top-level ``metadata/`` directory by older versions
#: of the acquisition software.
METADATA_PATTERNS: tuple = (
    "HardwareSettings*.jsonl",
    "RuleSettings*.jsonl",
)


def consolidate_metadata_files(data_root: str | pathlib.Path) -> list:
    """Move ``RuleSettings`` and ``HardwareSettings`` files to ``behavior/metadata/``.

    Older versions of the Delphi acquisition software write these JSONL files
    to a top-level ``<data_root>/metadata/`` directory.  The ingestion pipeline
    and quality-control functions expect them at
    ``<data_root>/behavior/metadata/``.  This function moves any matching files
    and removes the top-level ``metadata/`` directory when it is left empty.

    Patterns moved:

    - ``HardwareSettings*.jsonl``
    - ``RuleSettings*.jsonl``

    Files that already exist at the destination are **not** overwritten; a
    warning is printed instead.

    Parameters
    ----------
    data_root:
        Run-level session directory that may contain a top-level ``metadata/``
        sub-directory alongside ``behavior/``.

    Returns
    -------
    list of str
        Absolute paths of files that were successfully moved.
    """
    data_root = pathlib.Path(data_root)
    src_meta = data_root / "metadata"
    dst_meta = data_root / "behavior" / "metadata"

    if not src_meta.is_dir():
        return []

    # Collect candidate files matching any of the known patterns
    candidates: list = []
    for pattern in METADATA_PATTERNS:
        candidates.extend(sorted(src_meta.glob(pattern)))

    if not candidates:
        return []

    dst_meta.mkdir(parents=True, exist_ok=True)
    moved: list = []

    for src_file in candidates:
        dst_file = dst_meta / src_file.name

        if dst_file.exists():
            print(f"  [warn] skipping {src_file.name} — already exists in behavior/metadata/")
            continue

        try:
            fast_move_with_optional_checksum(str(src_file), str(dst_file))
            print(f"  [moved] {src_file.name} -> behavior/metadata/")
            moved.append(str(dst_file))
        except Exception as exc:
            print(f"  [error] could not move {src_file.name}: {exc}")

    # Remove the top-level metadata/ directory if it is now empty
    if src_meta.is_dir() and not any(src_meta.iterdir()):
        try:
            src_meta.rmdir()
            print(f"  [removed] empty metadata/ directory")
        except OSError:
            pass

    return moved
