import hashlib
import os
import shutil
from datetime import datetime

from tqdm import tqdm


def is_timestamp_dir(name: str) -> bool:
    try:
        datetime.strptime(name, "%Y-%m-%dT%H-%M-%S")
        return True
    except ValueError:
        return False


def compute_sha256(file_path: str, chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def collect_run_dirs(session_root: str):
    return [
        os.path.join(session_root, d)
        for d in os.listdir(session_root)
        if os.path.isdir(os.path.join(session_root, d)) and is_timestamp_dir(d)
    ]


def find_earliest_run(run_dirs):
    return min(run_dirs, key=lambda p: os.path.basename(p))


def count_files(directory: str) -> int:
    """Count all files recursively."""
    total = 0
    for _, _, files in os.walk(directory):
        total += len(files)
    return total


def move_contents_with_progress(src_dir: str, dst_dir: str):
    """
    Move contents with:
    - checksum validation
    - tqdm progress bar
    """
    total_files = count_files(src_dir)

    if total_files == 0:
        return

    with tqdm(
        total=total_files,
        desc=f"Consolidating data; Moving {os.path.basename(src_dir)}",
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
                    # Skip but still advance progress
                    pbar.update(1)
                    continue

                try:
                    src_hash = compute_sha256(src_file)
                except Exception:
                    pbar.update(1)
                    continue

                shutil.move(src_file, dst_file)

                try:
                    dst_hash = compute_sha256(dst_file)
                    if src_hash != dst_hash:
                        print(f"\nWARNING: checksum mismatch: {dst_file}")
                except Exception:
                    print(f"\nWARNING: failed checksum after move: {dst_file}")

                pbar.update(1)


def remove_all_empty_dirs(directory: str):
    """
    Remove ALL empty directories recursively, including those that
    only contain empty subdirectories.
    """
    removed_any = True

    # Repeat until no more empty dirs can be removed
    while removed_any:
        removed_any = False
        for root, dirs, files in os.walk(directory, topdown=False):
            if not dirs and not files:
                try:
                    os.rmdir(root)
                    removed_any = True
                except OSError:
                    pass


def consolidate_session_runs(session_root: str):
    """
    Consolidate all run directories into the earliest run with:
    - progress bars
    - checksum validation
    - full cleanup of empty directories
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

        # Strong cleanup (removes nested empty dirs too)
        remove_all_empty_dirs(run_dir)

        # Final check: remove run dir if now empty or only contained empties
        if os.path.exists(run_dir):
            try:
                if not os.listdir(run_dir):
                    os.rmdir(run_dir)
                    print(f"Removed empty run dir: {run_dir}")
                else:
                    # Try one more cleanup pass
                    remove_all_empty_dirs(run_dir)
                    if not os.listdir(run_dir):
                        os.rmdir(run_dir)
                        print(f"Removed empty run dir: {run_dir}")
            except OSError:
                pass

    print("\nConsolidation complete.")
