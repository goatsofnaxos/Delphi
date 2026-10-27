"""Poke-triggered video clip extraction for Delphi behavioral sessions.

Walks a root directory for session folders, then for each 1-hour PortCamera
chunk loads the poke-onset timestamps, slices the video frame index into
per-poke windows, and writes MP4 clips with JSON sidecar files.

Directory structure expected::

    <session_dir>/
    ├── behavior/
    │   └── DelphiController/       ← Harp poke-state streams
    ├── behavior-videos/
    │   ├── PortCamera/             ← per-chunk .mp4 + .csv frame indices
    │   └── OverheadCamera/         ← optional; horizontally appended to clips
    └── metadata/
        ├── subject.json            ← preferred subject ID source
        └── HardwareSettings*.jsonl ← fallback subject ID source

Each exported clip is saved as::

    <pokeclips_dir>/poke_<subject_id>_<timestamp>.mp4
    <pokeclips_dir>/poke_<subject_id>_<timestamp>.json  ← sidecar with timestamps

System requirements
-------------------
``ffmpeg`` and ``ffprobe`` must be available on ``PATH``.

Optional Python dependencies
-----------------------------
``opencv-python`` (``cv2``) is required only at clip-export time.
Install with::

    pip install delphi-data[video]

Typical usage
-------------
From Python::

    from delphi_data.video_processing import find_session_dirs, process_chunk, get_chunk_timestamps, get_subject_id
    from pathlib import Path

    root = Path("/data/my_experiment")
    for session_dir in find_session_dirs(root):
        subject_id    = get_subject_id(session_dir / "metadata")
        pokeclips_dir = session_dir / "behavior-videos" / "PokeClips"
        port_cam_dir  = session_dir / "behavior-videos" / "PortCamera"
        for chunk_ts in get_chunk_timestamps(port_cam_dir):
            process_chunk(session_dir, chunk_ts, pokeclips_dir, subject_id)

From the command line::

    python -m delphi_data.video_processing /data/my_experiment
    python -m delphi_data.video_processing /data/my_experiment --output /scratch/clips
    python -m delphi_data.video_processing /data/my_experiment --no-delete --workers 8
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Generator, Iterator, List, Optional, Tuple

import numpy as np
import pandas as pd
import swc.aeon.io.api as aeon_api
import swc.aeon.io.reader as reader

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HALF_WINDOW: np.timedelta64 = np.timedelta64(4, "s")
"""Clip half-window duration.  Each clip spans ``[poke_onset - HALF_WINDOW, poke_onset + HALF_WINDOW]``."""

DELETE_BUFFER_HRS: int = 3
"""Hours after a chunk ends before its source files may be deleted.

Prevents deleting a chunk that the acquisition system may still be writing.
"""

CHUNK_RE: re.Pattern = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})")
"""Regex matching the ``YYYY-MM-DDTHH-MM-SS`` timestamp in PortCamera filenames."""

POKE_READER: reader.Harp = reader.Harp("DelphiController_61*", columns=["PokeState"])
"""Harp reader for the poke-state register (address 61)."""

# Module-level caches so ffprobe is only called once per file.
_VIDEO_FPS_CACHE: Dict[str, float] = {}
_VIDEO_DIM_CACHE: Dict[str, Tuple[int, int]] = {}


# ---------------------------------------------------------------------------
# Video I/O
# ---------------------------------------------------------------------------


def _probe_video_fps(path: str | Path) -> float:
    """Return the frame rate of a video file, cached per path.

    Uses ``ffprobe`` to read the ``r_frame_rate`` field of the first video
    stream.  Results are cached in :data:`_VIDEO_FPS_CACHE` so the subprocess
    is only spawned once per unique path.

    Parameters
    ----------
    path:
        Absolute path to the video file.

    Returns
    -------
    float
        Frames per second of the first video stream.

    Raises
    ------
    ValueError
        If ``ffprobe`` returns a non-zero exit code or produces no output.
    """
    path = str(path)
    if path not in _VIDEO_FPS_CACHE:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate",
                "-of", "csv=p=0",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise ValueError(
                f"ffprobe failed for {path}: {result.stderr.strip()}"
            )
        num, den = result.stdout.strip().split("/")
        _VIDEO_FPS_CACHE[path] = int(num) / int(den)
    return _VIDEO_FPS_CACHE[path]


def _frames_ffmpeg(
    df: pd.DataFrame,
    fps: Optional[float] = None,
) -> Generator[np.ndarray, None, None]:
    """Yield BGR frames for rows in *df*, batching consecutive same-file seeks.

    Rows sharing the same ``_path`` column value are batched into a single
    ``ffmpeg`` call — seeking once to the first frame and reading forward.
    This is substantially faster than seeking to each frame individually.

    Parameters
    ----------
    df:
        Frame index DataFrame with ``_path`` (source video path) and
        ``_frame`` (zero-based frame index within that file) columns.
        Rows must be ordered by time.
    fps:
        Frame rate to use when converting frame indices to seek times.
        When ``None``, :func:`_probe_video_fps` is called for each source file.

    Yields
    ------
    np.ndarray
        BGR uint8 array of shape ``(height, width, 3)`` for each frame.

    Raises
    ------
    ValueError
        If ``ffmpeg`` yields no frames for a given seek position, or if
        ``ffprobe`` fails to read dimensions.
    """
    paths = df["_path"].values
    i = 0
    while i < len(paths):
        # Collect consecutive rows that share the same source file.
        path = str(paths[i])
        j = i
        while j < len(paths) and str(paths[j]) == path:
            j += 1
        n = j - i

        path_fps = fps if fps is not None else _probe_video_fps(path)
        start_sec = int(df["_frame"].values[i]) / path_fps
        duration_sec = n / path_fps

        # Probe frame dimensions once per file.
        if path not in _VIDEO_DIM_CACHE:
            r = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=width,height",
                    "-of", "csv=p=0",
                    path,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r.returncode != 0 or not r.stdout.strip():
                raise ValueError(
                    f"ffprobe failed for {path}: {r.stderr.strip()}"
                )
            _VIDEO_DIM_CACHE[path] = tuple(map(int, r.stdout.strip().split(",")))

        w, h = _VIDEO_DIM_CACHE[path]
        frame_size = w * h * 3  # bytes per raw BGR frame

        proc = subprocess.Popen(
            [
                "ffmpeg", "-v", "error",
                "-ss", f"{start_sec:.6f}",
                "-i", path,
                "-t", f"{duration_sec:.6f}",
                "-f", "rawvideo",
                "-pix_fmt", "bgr24",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        frames_read = 0
        for _ in range(n):
            raw = proc.stdout.read(frame_size)
            if len(raw) < frame_size:
                break
            yield np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3).copy()
            frames_read += 1
        proc.stdout.close()
        proc.wait()

        if frames_read == 0:
            raise ValueError(
                f"FFmpeg yielded no frames from {path} at {start_sec:.1f}s: "
                f"{proc.stderr.read().decode(errors='replace')}"
            )
        i = j


def _open_ffmpeg_writer(
    outfile: Path,
    fps: float,
    width: int,
    height: int,
    use_nvenc: bool = True,
) -> subprocess.Popen:
    """Open an ffmpeg subprocess that accepts raw BGR frames on stdin and writes an MP4.

    When *use_nvenc* is ``True`` the GPU ``h264_nvenc`` encoder is used for
    fast, high-quality encoding.  When ``False`` the fallback ``mjpeg`` codec
    is used (no GPU required, produces larger files).

    A ``crop`` filter ensures width and height are both divisible by 2, which
    is required by most H.264/MJPEG encoders.

    Parameters
    ----------
    outfile:
        Destination MP4 file path.
    fps:
        Output frame rate.
    width:
        Frame width in pixels (before cropping to even dimensions).
    height:
        Frame height in pixels (before cropping to even dimensions).
    use_nvenc:
        When ``True``, use ``h264_nvenc`` (NVIDIA GPU encoder).
        When ``False``, fall back to ``mjpeg``.

    Returns
    -------
    subprocess.Popen
        Running ffmpeg process with ``stdin`` open for raw BGR frame bytes.
    """
    codec_args = (
        [
            "-c:v", "h264_nvenc",
            "-rc", "constqp",
            "-qp", "18",
            "-preset", "p7",
            "-profile:v", "main",
            "-bf", "0",
        ]
        if use_nvenc
        else ["-c:v", "mjpeg", "-q:v", "2", "-huffman", "optimal"]
    )
    return subprocess.Popen(
        [
            "ffmpeg", "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{width}x{height}",
            "-pix_fmt", "bgr24",
            "-r", str(int(fps)),
            "-i", "pipe:",
            "-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
            *codec_args,
            str(outfile),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


# ---------------------------------------------------------------------------
# Clip windowing
# ---------------------------------------------------------------------------


def relaxed_trigger_clip(
    data: pd.DataFrame,
    events: pd.Index | pd.DataFrame,
    before: Optional[pd.Timedelta | str] = None,
    after: Optional[pd.Timedelta | str] = None,
) -> pd.DataFrame:
    """Slice video frame data into per-event windows and concatenate.

    Called *relaxed* because it does not raise an error when a clip window
    extends beyond the available data — it simply returns fewer frames for
    that clip.

    Parameters
    ----------
    data:
        Video frame index DataFrame with a DatetimeIndex.
    events:
        Event timestamps to centre each clip window on.  Either a
        ``DatetimeIndex`` or a DataFrame whose ``.index`` is used.
    before:
        Time before each event to include.  Accepts anything
        ``pd.Timedelta`` accepts (e.g. ``"4s"``, ``pd.Timedelta(4, "s")``).
        Defaults to zero.
    after:
        Time after each event to include.  Defaults to zero.

    Returns
    -------
    pd.DataFrame
        Concatenated frame data for all clips.  Two extra columns are added:

        - ``clip_sequence`` — zero-based index of the poke event.
        - ``frame_sequence`` — zero-based frame index within each clip.
    """
    before = pd.Timedelta(0) if before is None else pd.Timedelta(before)
    after = pd.Timedelta(0) if after is None else pd.Timedelta(after)
    events = events.index if not isinstance(events, pd.Index) else events

    clips: List[pd.DataFrame] = []
    for i, ts in enumerate(events):
        clip = data[
            (data.index >= ts - before) & (data.index <= ts + after)
        ].copy()
        clip["frame_sequence"] = range(len(clip))
        clip["clip_sequence"] = i
        clips.append(clip)

    return pd.concat(clips) if clips else pd.DataFrame()


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------


def find_session_dirs(root: str | Path) -> Iterator[Path]:
    """Yield session directories containing ``behavior/``, ``behavior-videos/``, and ``metadata/``.

    Paths that contain the word ``clips`` (case-insensitive) are skipped so
    that already-extracted clip output directories are never re-processed when
    the output is written inside the input tree.

    Parameters
    ----------
    root:
        Root directory to search recursively.

    Returns
    -------
    Iterator[pathlib.Path]
        Absolute paths of qualifying session directories.
    """
    for behavior in Path(root).rglob("behavior"):
        session = behavior.parent
        if "clips" in str(session).lower():
            continue
        if (session / "behavior-videos").is_dir() and (session / "metadata").is_dir():
            yield session


def get_subject_id(metadata_dir: str | Path) -> str:
    """Extract the subject ID from session metadata files.

    Checks sources in priority order:

    1. ``subject.json`` — explicit file written by the acquisition system.
    2. ``HardwareSettings*.jsonl`` — parses the ``remoteTransferRootPath``
       field which encodes the mouse ID in the folder name
       (e.g. ``data_mouse3``).

    Parameters
    ----------
    metadata_dir:
        Session ``metadata/`` directory containing ``subject.json`` and/or
        ``HardwareSettings*.jsonl`` files.

    Returns
    -------
    str
        Subject ID string, or ``"unknown"`` if no ID can be found.
    """
    metadata_dir = Path(metadata_dir)

    # Preferred: explicit subject.json written by the acquisition system.
    subject_json = metadata_dir / "subject.json"
    if subject_json.exists():
        try:
            sid = json.loads(subject_json.read_text()).get("subject_id")
            if sid:
                return str(sid)
        except Exception:
            pass

    # Fallback: parse the mouse ID from the remoteTransferRootPath field.
    for hw_file in metadata_dir.glob("HardwareSettings*.jsonl"):
        try:
            for line in hw_file.read_text().splitlines():
                if "remoteTransferRootPath" not in line:
                    continue
                try:
                    path = (
                        json.loads(line)
                        .get("value", {})
                        .get("remoteTransferRootPath", "")
                    )
                except Exception:
                    continue
                m = re.search(r"data_(mouse\w+)", path, re.IGNORECASE)
                if m:
                    return m.group(1)
        except Exception:
            pass

    return "unknown"


def get_chunk_timestamps(port_cam_dir: str | Path) -> List[pd.Timestamp]:
    """Return sorted chunk start timestamps parsed from PortCamera CSV filenames.

    Each 1-hour recording chunk produces a file named
    ``PortCamera_YYYY-MM-DDTHH-MM-SS.csv``.  This function reads the
    timestamps from those filenames without opening the files.

    Parameters
    ----------
    port_cam_dir:
        Path to the ``behavior-videos/PortCamera/`` directory.

    Returns
    -------
    list of pd.Timestamp
        Sorted list of chunk start timestamps (UTC-naive).
    """
    chunks: set = set()
    for f in Path(port_cam_dir).glob("PortCamera_*.csv"):
        m = CHUNK_RE.search(f.stem)
        if m:
            date, h, mn, s = m.groups()
            chunks.add(pd.Timestamp(f"{date}T{h}:{mn}:{s}"))
    return sorted(chunks)


def load_camera(
    cam_dir: str | Path,
    cam_name: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> Optional[pd.DataFrame]:
    """Load the video frame index for one camera within a time window.

    Uses the Aeon API to read the camera's CSV frame-index files.  The
    ``.avi`` extension that the Aeon library assumes is replaced with
    ``.mp4`` to match the actual Delphi output format.

    Parameters
    ----------
    cam_dir:
        Directory containing the camera's ``.csv`` frame-index files
        (e.g. ``behavior-videos/PortCamera/``).
    cam_name:
        Camera name prefix used to glob the index files
        (e.g. ``"PortCamera"``).
    start:
        Window start timestamp (inclusive).
    end:
        Window end timestamp (inclusive).

    Returns
    -------
    pd.DataFrame or None
        Frame index DataFrame with ``_path`` and ``_frame`` columns and a
        ``DatetimeIndex``, or ``None`` if the camera has no data in the
        requested window.
    """
    try:
        data = aeon_api.load(
            cam_dir,
            reader.Video(f"{cam_name}_*"),
            start=start,
            end=end,
        )
    except Exception:
        return None

    if data is None or data.empty:
        return None

    data["_path"] = data["_path"].str.replace(".avi", ".mp4", regex=False)
    return data


# ---------------------------------------------------------------------------
# Chunk management
# ---------------------------------------------------------------------------


def _chunk_too_recent(chunk_ts: pd.Timestamp) -> bool:
    """Return ``True`` if the chunk may still be actively recording.

    A chunk is considered too recent if its notional end time
    (``chunk_ts + 1 h``) plus :data:`DELETE_BUFFER_HRS` is still in the
    future.  This prevents deleting a file the acquisition system is still
    writing to.

    Parameters
    ----------
    chunk_ts:
        Chunk start timestamp (UTC-naive).

    Returns
    -------
    bool
        ``True`` when deletion should be deferred.
    """
    chunk_end = pd.Timestamp(chunk_ts) + pd.Timedelta(hours=1)
    return chunk_end + pd.Timedelta(hours=DELETE_BUFFER_HRS) > pd.Timestamp.now(
        "UTC"
    ).replace(tzinfo=None)


def delete_port_chunk(port_cam_dir: str | Path, chunk_ts: pd.Timestamp) -> None:
    """Delete the ``.mp4`` and ``.csv`` source files for one PortCamera chunk.

    Only the PortCamera files for this specific chunk are removed.  Behavior
    data, metadata, and any other camera files are left untouched.

    Parameters
    ----------
    port_cam_dir:
        Path to the ``behavior-videos/PortCamera/`` directory.
    chunk_ts:
        Chunk start timestamp, used to construct the expected filenames.
    """
    ts_str = chunk_ts.strftime("%Y-%m-%dT%H-%M-%S")
    for ext in (".mp4", ".csv"):
        f = Path(port_cam_dir) / f"PortCamera_{ts_str}{ext}"
        if f.exists():
            try:
                f.unlink()
                print(f"    [deleted] {f.name}")
            except PermissionError:
                print(f"    [warn] no permission to delete {f.name}")


def _maybe_delete(
    port_cam_dir: Path,
    chunk_ts: pd.Timestamp,
    no_delete: bool,
) -> None:
    """Conditionally delete chunk source files based on flags and recency.

    Parameters
    ----------
    port_cam_dir:
        Path to the ``behavior-videos/PortCamera/`` directory.
    chunk_ts:
        Chunk start timestamp.
    no_delete:
        When ``True``, skip deletion unconditionally.
    """
    if no_delete:
        return
    if _chunk_too_recent(chunk_ts):
        print("  [skip delete] chunk may still be recording")
    else:
        delete_port_chunk(port_cam_dir, chunk_ts)


# ---------------------------------------------------------------------------
# Clip export
# ---------------------------------------------------------------------------


def export_clip(
    clip_id: int,
    per_cam_dfs: List[pd.DataFrame],
    camera_names: List[str],
    poke_times: pd.DataFrame,
    pokeclips_dir: Path,
    subject_id: str,
) -> str:
    """Write one MP4 clip and a JSON sidecar for a single poke event.

    If multiple cameras are active their frames are horizontally concatenated
    into a single video.  The JSON sidecar records the camera list, FPS, and
    per-frame UTC timestamps so downstream code can identify frames without
    re-parsing the video.

    Already-exported clips (non-empty ``.mp4`` already on disk) are skipped,
    allowing interrupted runs to be safely resumed.

    Parameters
    ----------
    clip_id:
        Zero-based index into *poke_times* identifying the poke event.
    per_cam_dfs:
        List of frame-index DataFrames, one per camera, each pre-filtered to
        the clip window for this poke event.
    camera_names:
        Camera name labels corresponding to *per_cam_dfs*.
    poke_times:
        Full poke-onset DataFrame for the current chunk (DatetimeIndex).
    pokeclips_dir:
        Directory where clip files are written.
    subject_id:
        Subject identifier embedded in the output filename.

    Returns
    -------
    str
        Status string describing the outcome:

        - ``"exported"``  — clip written successfully.
        - ``"existing"``  — clip already on disk; skipped.
        - ``"skipped"``   — no camera frames available for this event.
        - ``"empty"``     — frame generator produced zero frames.
        - ``"failed"``    — ffmpeg returned an error or an exception occurred.
    """
    try:
        import cv2
    except ImportError as exc:
        raise ImportError(
            "opencv-python is required for clip export.  "
            "Install with: pip install delphi-data[video]"
        ) from exc

    active = [
        (df, name)
        for df, name in zip(per_cam_dfs, camera_names)
        if not df.empty
    ]
    if not active:
        return "skipped"

    poke_ts = poke_times.index[clip_id]
    ts_str = pd.Timestamp(poke_ts).strftime("%Y-%m-%d_%H-%M-%S-%f")
    outfile = pokeclips_dir / f"poke_{subject_id}_{ts_str}.mp4"
    sidecar = outfile.with_suffix(".json")

    if outfile.exists() and outfile.stat().st_size > 0:
        print(f"    [skip existing] {outfile.name}")
        return "existing"

    active_dfs, active_names = zip(*active)

    def _hstack(frames: List[np.ndarray]) -> np.ndarray:
        """Horizontally stack *frames*, resizing to a common height first."""
        h = frames[0].shape[0]
        return np.concatenate(
            [
                f
                if f.shape[0] == h
                else cv2.resize(f, (int(f.shape[1] * h / f.shape[0]), h))
                for f in frames
            ],
            axis=1,
        )

    proc = None
    frame_timestamps: List[str] = []

    try:
        fps = _probe_video_fps(active_dfs[0]["_path"].iloc[0])
        iters = [_frames_ffmpeg(df, fps) for df in active_dfs]
        ts_iter = iter(active_dfs[0].index)

        for frames_tuple, ts in zip(zip(*iters), ts_iter):
            merged = _hstack(list(frames_tuple))
            frame_timestamps.append(pd.Timestamp(ts).isoformat())
            if proc is None:
                h, w = merged.shape[:2]
                proc = _open_ffmpeg_writer(outfile, fps, w, h, use_nvenc=False)
            if proc.poll() is not None:
                raise RuntimeError("FFmpeg exited early")
            proc.stdin.write(merged.tobytes())

        if proc is None:
            return "empty"

        proc.stdin.close()
        proc.stdin = None
        _, stderr = proc.communicate()

        if proc.returncode != 0:
            outfile.unlink(missing_ok=True)
            print(
                f"    [failed export] {outfile.stem}  |  "
                f"ffmpeg error: {stderr.decode().strip()}"
            )
            return "failed"

        sidecar.write_text(
            json.dumps(
                {
                    "cameras": list(active_names),
                    "clip_fps": fps,
                    "frame_timestamps_utc": frame_timestamps,
                }
            )
        )
        print(f"    [exported] {outfile.name}")
        return "exported"

    except Exception as exc:
        if proc is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.wait()
        outfile.unlink(missing_ok=True)
        # Strip verbose ffmpeg path noise from the message.
        err = re.sub(r"ffprobe failed for [^:]+: ", "", str(exc))
        err = re.sub(r"\[[^\]]+@ 0x[0-9a-f]+\] ", "", err)
        err = re.sub(r"/\S+\.mp4: ", "", err)
        print(f"    [failed export] {outfile.stem}  |  {err.splitlines()[0].strip()}")
        return "failed"


# ---------------------------------------------------------------------------
# Chunk-level pipeline
# ---------------------------------------------------------------------------


def process_chunk(
    session_dir: str | Path,
    chunk_ts: pd.Timestamp,
    pokeclips_dir: str | Path,
    subject_id: str,
    n_workers: int = 4,
    no_delete: bool = False,
    delete_corrupted: bool = False,
) -> Dict[str, int]:
    """Extract and export all poke clips for one 1-hour PortCamera chunk.

    Loads poke-onset timestamps from the Harp streams, loads the PortCamera
    (and optional OverheadCamera) frame index, slices per-poke clip windows,
    and writes MP4 clips in parallel via a thread pool.

    Source chunk files are deleted after successful export unless *no_delete*
    is ``True`` or the chunk is too recent (see :data:`DELETE_BUFFER_HRS`).

    Parameters
    ----------
    session_dir:
        Run-level session directory containing ``behavior/``,
        ``behavior-videos/``, and ``metadata/`` sub-directories.
    chunk_ts:
        Start timestamp of the 1-hour chunk to process.
    pokeclips_dir:
        Directory where exported clips are written.
    subject_id:
        Subject identifier embedded in each clip filename.
    n_workers:
        Number of parallel clip-export threads.
    no_delete:
        When ``True``, source ``.mp4`` and ``.csv`` chunk files are never
        deleted, regardless of export outcome.
    delete_corrupted:
        When ``True``, delete source files even if some clips failed to
        export.  Ignored when *no_delete* is ``True``.

    Returns
    -------
    dict
        Status-count mapping, e.g.
        ``{"exported": 42, "existing": 3, "failed": 1}``.
        Keys are only present when the count is non-zero.
    """
    session_dir = Path(session_dir)
    pokeclips_dir = Path(pokeclips_dir)
    port_cam_dir = session_dir / "behavior-videos" / "PortCamera"

    chunk_start = pd.Timestamp(chunk_ts)
    chunk_end = chunk_start + pd.Timedelta(hours=1)

    # Load poke-onset times for this chunk.
    try:
        poke_times = aeon_api.load(
            session_dir / "behavior" / "DelphiController",
            POKE_READER,
            start=chunk_start,
            end=chunk_end,
        )
    except Exception as exc:
        print(f"  [warn] could not load poke times for {chunk_ts}: {exc}")
        _maybe_delete(port_cam_dir, chunk_ts, no_delete)
        return {}

    poke_times = poke_times[poke_times["PokeState"] == 1]
    if poke_times.empty:
        print(
            f"  chunk {chunk_ts.strftime('%Y-%m-%dT%H-%M-%S')}: "
            "0 pokes — skipping"
        )
        _maybe_delete(port_cam_dir, chunk_ts, no_delete)
        return {}

    print(
        f"  chunk {chunk_ts.strftime('%Y-%m-%dT%H-%M-%S')}: "
        f"{len(poke_times)} pokes"
    )

    # Load frame indices with a margin so clips near chunk boundaries are complete.
    margin = pd.Timedelta(seconds=4)
    port_data = load_camera(
        port_cam_dir, "PortCamera", chunk_start - margin, chunk_end + margin
    )
    if port_data is None:
        print(f"  [warn] no PortCamera data for {chunk_ts}")
        _maybe_delete(port_cam_dir, chunk_ts, no_delete)
        return {}

    # OverheadCamera is optional; when present its frames are appended to the right.
    cam_data_list = [port_data]
    cam_names = ["PortCamera"]
    overhead_dir = session_dir / "behavior-videos" / "OverheadCamera"
    if overhead_dir.is_dir():
        oh_data = load_camera(
            overhead_dir, "OverheadCamera", chunk_start - margin, chunk_end + margin
        )
        if oh_data is not None:
            cam_data_list.append(oh_data)
            cam_names.append("OverheadCamera")

    # Slice each camera's frame index into per-poke windows.
    clips_list = [
        relaxed_trigger_clip(cd, poke_times, before=HALF_WINDOW, after=HALF_WINDOW)
        for cd in cam_data_list
    ]
    pokeclips_dir.mkdir(parents=True, exist_ok=True)
    clip_ids = pd.concat(
        [df["clip_sequence"] for df in clips_list if not df.empty]
    ).unique()

    # Export all clips in parallel.
    def _run(cid: int) -> str:
        return export_clip(
            cid,
            [df[df["clip_sequence"] == cid] for df in clips_list],
            cam_names,
            poke_times,
            pokeclips_dir,
            subject_id,
        )

    counts: Dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_run, cid): cid for cid in clip_ids}
        for fut in as_completed(futures):
            try:
                status = fut.result()
            except Exception as exc:
                status = "failed"
                print(f"    [unexpected error] clip {futures[fut]}: {exc}")
            counts[status] = counts.get(status, 0) + 1

    extras = {k: v for k, v in counts.items() if k != "exported" and v > 0}
    extra_str = (
        "  (" + ", ".join(f"{k}: {v}" for k, v in sorted(extras.items())) + ")"
        if extras
        else ""
    )
    print(
        f"    -> exported {counts.get('exported', 0)} "
        f"of {len(clip_ids)} pokes{extra_str}"
    )

    # Delete source files once all clips have been exported.
    if not no_delete:
        if _chunk_too_recent(chunk_ts):
            print("  [skip delete] chunk may still be recording")
        elif counts.get("failed", 0) > 0 and not delete_corrupted:
            print(
                f"  [warn] skipping delete — {counts['failed']} clip(s) failed "
                "(pass delete_corrupted=True to force)"
            )
        else:
            delete_port_chunk(port_cam_dir, chunk_ts)

    return counts


# ---------------------------------------------------------------------------
# Session-level pipeline
# ---------------------------------------------------------------------------


def process_session(
    session_dir: str | Path,
    output_base: Optional[str | Path] = None,
    n_workers: int = 4,
    no_delete: bool = False,
    delete_corrupted: bool = False,
) -> None:
    """Extract poke clips for every chunk in one session directory.

    Iterates over all PortCamera chunk timestamps found in
    ``behavior-videos/PortCamera/`` and calls :func:`process_chunk` for each.

    Parameters
    ----------
    session_dir:
        Run-level session directory.
    output_base:
        Root directory under which clip output is written, preserving the
        session path relative to the original input tree.  When ``None``,
        clips are written inside *session_dir* at
        ``behavior-videos/PokeClips/``.
    n_workers:
        Parallel clip-export threads per chunk.
    no_delete:
        When ``True``, source chunk files are never deleted.
    delete_corrupted:
        When ``True``, delete source chunks even when some clips failed.
    """
    session_dir = Path(session_dir)
    port_cam_dir = session_dir / "behavior-videos" / "PortCamera"

    if not port_cam_dir.is_dir():
        print(f"  [skip] no PortCamera folder in {session_dir}")
        return

    subject_id = get_subject_id(session_dir / "metadata")
    pokeclips_dir = (
        Path(output_base) / "behavior-videos" / "PokeClips"
        if output_base
        else session_dir / "behavior-videos" / "PokeClips"
    )

    chunk_timestamps = get_chunk_timestamps(port_cam_dir)
    print(f"  Subject: {subject_id}  |  {len(chunk_timestamps)} chunk(s)")

    for chunk_ts in chunk_timestamps:
        process_chunk(
            session_dir,
            chunk_ts,
            pokeclips_dir,
            subject_id,
            n_workers=n_workers,
            no_delete=no_delete,
            delete_corrupted=delete_corrupted,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments for the video processing script.

    Parameters
    ----------
    argv:
        Argument list.  ``None`` reads from ``sys.argv[1:]``.

    Returns
    -------
    argparse.Namespace
        Parsed namespace with ``root``, ``output``, ``workers``,
        ``no_delete``, and ``no_delete_corrupted`` attributes.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Extract poke-triggered video clips from Delphi session data.\n\n"
            "Walks ROOT for session directories (containing behavior/, "
            "behavior-videos/, and metadata/), then for each PortCamera chunk "
            "exports one MP4 clip per poke event."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("root", help="Root directory to search for sessions.")
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output base directory.  Clips are written to "
            "<output>/<rel_session>/behavior-videos/PokeClips/.  "
            "Defaults to writing inside each session directory."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel clip-export threads per chunk (default: 4).",
    )
    parser.add_argument(
        "--no-delete",
        action="store_true",
        help="Do not delete PortCamera source files after exporting clips.",
    )
    parser.add_argument(
        "--no-delete-corrupted",
        action="store_true",
        help=(
            "Keep the source chunk even when some clips failed to export "
            "(default: skip deletion when any clip failed)."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None) -> None:
    """Entry point for the ``delphi-data create-clips`` CLI and ``python -m`` invocation.

    Searches *root* for valid session directories and processes every
    PortCamera chunk found.

    Parameters
    ----------
    argv:
        Argument list.  ``None`` reads from ``sys.argv[1:]``.
    """
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    output_base = Path(args.output).resolve() if args.output else None

    session_dirs = list(find_session_dirs(root))
    print(f"Found {len(session_dirs)} session(s) under {root}")

    for session_dir in session_dirs:
        rel = session_dir.relative_to(root)
        out = (output_base / rel) if output_base else None
        print(f"\n{'=' * 60}")
        print(f"Session: {rel}")
        process_session(
            session_dir,
            output_base=out,
            n_workers=args.workers,
            no_delete=args.no_delete,
            delete_corrupted=not args.no_delete_corrupted,
        )


if __name__ == "__main__":
    main()
