#!/usr/bin/env python3
"""
load_pokeClips.py

Walk a root directory, find session folders (containing behavior/,
behavior-videos/, and metadata/), extract poke-triggered video clips for
each 1-hour data chunk, save them to behavior-videos/PokeClips/, and delete
the source PortCamera chunk files once all clips have exported successfully.

Usage:
    python load_pokeClips.py /input_path
    python load_pokeClips.py /input_path --output /output_path
    python load_pokeClips.py /input_path --no-delete           # skip source file deletion
    python load_pokeClips.py /input_path --no-delete-corrupted # keep source if clips failed

    where {rel} is the session path relative to /input_path,
    e.g. data_mouse3/2026-05-07_12-00-00

    Without --output: writes to /input_path/{rel}/behavior-videos/PokeClips/
    With --output:    writes to /output_path/{rel}/behavior-videos/PokeClips/
"""

# ── imports ────────────────────────────────────────────────────────────────────

import argparse
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

import swc.aeon.io.reader as reader
import swc.aeon.io.api as aeon_api

# ── constants ──────────────────────────────────────────────────────────────────

HALF_WINDOW       = np.timedelta64(4, "s")  # clip window: ±4 s around each poke
DELETE_BUFFER_HRS = 3                       # don't delete a chunk until this long after it ends

CHUNK_RE    = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})")
POKE_READER = reader.Harp("DelphiController_61*", columns=["PokeState"])

_VIDEO_FPS_CACHE = {}  # cached per path so ffprobe is only called once per file
_VIDEO_DIM_CACHE = {}

# ── video I/O ──────────────────────────────────────────────────────────────────

def _probe_video_fps(path):
    """Return FPS for a video file, cached per path."""
    path = str(path)
    if path not in _VIDEO_FPS_CACHE:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0 or not r.stdout.strip():
            raise ValueError(f"ffprobe failed for {path}: {r.stderr.strip()}")
        num, den = r.stdout.strip().split("/")
        _VIDEO_FPS_CACHE[path] = int(num) / int(den)
    return _VIDEO_FPS_CACHE[path]


def _frames_ffmpeg(df, fps=None):
    """Yield BGR frames for rows in df, seeking once per source file then reading sequentially.

    Rows in df are expected to be ordered by time. Consecutive rows sharing the
    same source file are batched into a single ffmpeg call — seeking to the first
    frame and reading forward — which is much faster than seeking to each frame
    individually.
    """
    paths = df["_path"].values
    i = 0
    while i < len(paths):
        # collect the run of consecutive rows that share the same source file
        path = str(paths[i])
        j = i
        while j < len(paths) and str(paths[j]) == path:
            j += 1
        n = j - i

        # seek to the first frame by converting frame index to seconds
        path_fps     = fps if fps is not None else _probe_video_fps(path)
        start_sec    = int(df["_frame"].values[i]) / path_fps
        duration_sec = n / path_fps

        # probe frame dimensions once per file so we know how many bytes to read per frame
        if path not in _VIDEO_DIM_CACHE:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode != 0 or not r.stdout.strip():
                raise ValueError(f"ffprobe failed for {path}: {r.stderr.strip()}")
            _VIDEO_DIM_CACHE[path] = tuple(map(int, r.stdout.strip().split(",")))
        w, h       = _VIDEO_DIM_CACHE[path]
        frame_size = w * h * 3  # bytes per raw BGR frame

        # pipe raw BGR frames out of ffmpeg and yield them one at a time
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-ss", f"{start_sec:.6f}", "-i", path,
             "-t", f"{duration_sec:.6f}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        frames_read = 0
        for _ in range(n):
            raw = proc.stdout.read(frame_size)
            if len(raw) < frame_size:
                break  # file ended earlier than expected
            yield np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3).copy()
            frames_read += 1
        proc.stdout.close()
        proc.wait()

        if frames_read == 0:
            raise ValueError(f"FFmpeg yielded no frames from {path} at {start_sec:.1f}s: "
                             f"{proc.stderr.read().decode(errors='replace')}")
        i = j


def _open_ffmpeg_writer(outfile, fps, width, height, use_nvenc=True):
    """Open an ffmpeg subprocess that accepts raw BGR frames on stdin and writes an mp4.

    use_nvenc=True uses the GPU h264_nvenc encoder (fast, high quality).
    use_nvenc=False falls back to mjpeg (no GPU required, larger files).
    The crop filter ensures width/height are divisible by 2, required by most codecs.
    """
    codec_args = (
        ["-c:v", "h264_nvenc", "-rc", "constqp", "-qp", "18", "-preset", "p7", "-profile:v", "main", "-bf", "0"]
        if use_nvenc else
        ["-c:v", "mjpeg", "-q:v", "2", "-huffman", "optimal"]
    )
    return subprocess.Popen(
        ["ffmpeg", "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
         "-s", f"{width}x{height}", "-pix_fmt", "bgr24", "-r", str(int(fps)), "-i", "pipe:",
         "-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
         *codec_args, str(outfile)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def relaxedtriggerclip(data, events, before=None, after=None):
    """Slice video frame data into per-poke windows around each event timestamp.

    Returns a concatenated DataFrame tagged with clip_sequence (poke index)
    and frame_sequence (frame index within clip). Called 'relaxed' because it
    does not raise an error if a clip window extends beyond the available data.
    """
    before = pd.Timedelta(0) if before is None else pd.Timedelta(before)
    after  = pd.Timedelta(0) if after  is None else pd.Timedelta(after)
    events = events.index if not isinstance(events, pd.Index) else events
    clips  = []
    for i, ts in enumerate(events):
        clip = data[(data.index >= ts - before) & (data.index <= ts + after)].copy()
        clip["frame_sequence"] = range(len(clip))
        clip["clip_sequence"]  = i
        clips.append(clip)
    return pd.concat(clips)

# ── discovery ──────────────────────────────────────────────────────────────────

def find_session_dirs(root):
    """Yield session dirs containing behavior/, behavior-videos/, and metadata/.

    Skips paths containing 'clips' to avoid re-processing already-extracted clips
    if the output is written inside the input tree.
    """
    for behavior in Path(root).rglob("behavior"):
        session = behavior.parent
        if "clips" in str(session).lower():
            continue
        if (session / "behavior-videos").is_dir() and (session / "metadata").is_dir():
            yield session


def get_subject_id(metadata_dir):
    """Extract subject ID from subject.json, falling back to HardwareSettings*.jsonl.

    subject.json is written by the acquisition system and is the preferred source.
    The HardwareSettings fallback parses the remoteTransferRootPath field, which
    encodes the mouse ID in the folder name (e.g. .../data_mouse3/...).
    """
    # preferred: explicit subject.json written by acquisition system
    subject_json = metadata_dir / "subject.json"
    if subject_json.exists():
        try:
            sid = json.loads(subject_json.read_text()).get("subject_id")
            if sid:
                return str(sid)
        except Exception:
            pass

    # fallback: parse mouse ID out of the remoteTransferRootPath in hardware settings
    for hw_file in metadata_dir.glob("HardwareSettings*.jsonl"):
        try:
            for line in hw_file.read_text().splitlines():
                if "remoteTransferRootPath" not in line:
                    continue
                try:
                    path = json.loads(line).get("value", {}).get("remoteTransferRootPath", "")
                except Exception:
                    continue
                m = re.search(r"data_(mouse\w+)", path, re.IGNORECASE)
                if m:
                    return m.group(1)
        except Exception:
            pass

    return "unknown"


def get_chunk_timestamps(port_cam_dir):
    """Return sorted list of chunk timestamps parsed from PortCamera CSV filenames.

    Each 1-hour chunk produces a PortCamera_YYYY-MM-DDTHH-MM-SS.csv index file.
    """
    chunks = set()
    for f in port_cam_dir.glob("PortCamera_*.csv"):
        m = CHUNK_RE.search(f.stem)
        if m:
            date, h, mn, s = m.groups()
            chunks.add(pd.Timestamp(f"{date}T{h}:{mn}:{s}"))
    return sorted(chunks)


def load_camera(cam_dir, cam_name, start, end):
    """Load video frame index data for one camera within [start, end].

    Returns None if the camera folder has no data in this window. The aeon
    library assumes .avi extensions; we replace them with .mp4.
    """
    try:
        data = aeon_api.load(cam_dir, reader.Video(f"{cam_name}_*"), start=start, end=end)
    except Exception:
        return None
    if data.empty:
        return None
    data["_path"] = data["_path"].str.replace(".avi", ".mp4", regex=False)
    return data

# ── processing ─────────────────────────────────────────────────────────────────

def _chunk_too_recent(chunk_ts):
    """Return True if the chunk end is within DELETE_BUFFER_HRS of now.

    Prevents deleting a chunk that may still be actively recording — the
    acquisition system writes to the current chunk continuously.
    """
    chunk_end = pd.Timestamp(chunk_ts) + pd.Timedelta(hours=1)
    return chunk_end + pd.Timedelta(hours=DELETE_BUFFER_HRS) > pd.Timestamp.now("UTC").replace(tzinfo=None)


def _maybe_delete(port_cam_dir, chunk_ts, no_delete):
    """Delete the chunk source files unless deletion is disabled or the chunk is too recent."""
    if no_delete:
        return
    if _chunk_too_recent(chunk_ts):
        print(f"  [skip delete] chunk may still be recording")
    else:
        delete_port_chunk(port_cam_dir, chunk_ts)


def delete_port_chunk(port_cam_dir, chunk_ts):
    """Delete the .mp4 and .csv for this chunk from the PortCamera folder.

    Only the PortCamera files are deleted — the behavior data and any other
    cameras are left untouched.
    """
    ts_str = chunk_ts.strftime("%Y-%m-%dT%H-%M-%S")
    for ext in (".mp4", ".csv"):
        f = port_cam_dir / f"PortCamera_{ts_str}{ext}"
        if f.exists():
            try:
                f.unlink()
                print(f"    [deleted] {f.name}")
            except PermissionError:
                print(f"    [warn] no permission to delete {f.name}")


def export_clip(clip_id, per_cam_dfs, camera_names, poke_times, pokeclips_dir, subject_id):
    """Write one merged clip mp4 and sidecar JSON for a single poke event.

    If multiple cameras are active, their frames are horizontally concatenated
    side-by-side into a single video. The sidecar JSON records the camera list,
    fps, and per-frame UTC timestamps so downstream code can identify frames
    without re-parsing the video.

    Returns a status string: 'exported', 'existing', 'skipped', 'empty', or 'failed'.
    """
    import cv2

    # skip cameras that have no frames for this particular clip window
    active = [(df, name) for df, name in zip(per_cam_dfs, camera_names) if not df.empty]
    if not active:
        return "skipped"

    poke_ts = poke_times.index[clip_id]
    ts_str  = pd.Timestamp(poke_ts).strftime("%Y-%m-%d_%H-%M-%S-%f")
    outfile = pokeclips_dir / f"poke_{subject_id}_{ts_str}.mp4"
    sidecar = outfile.with_suffix(".json")

    # skip if already exported (allows resuming interrupted runs)
    if outfile.exists() and outfile.stat().st_size > 0:
        print(f"    [skip existing] {outfile.name}")
        return "existing"

    active_dfs, active_names = zip(*active)

    def hstack(frames):
        # resize all frames to the first frame's height before concatenating
        h = frames[0].shape[0]
        return np.concatenate([
            f if f.shape[0] == h else cv2.resize(f, (int(f.shape[1] * h / f.shape[0]), h))
            for f in frames
        ], axis=1)

    proc, frame_timestamps = None, []
    try:
        # read frames from all cameras in lockstep, merging each set side-by-side
        fps     = _probe_video_fps(active_dfs[0]["_path"].iloc[0])
        iters   = [_frames_ffmpeg(df, fps) for df in active_dfs]
        ts_iter = iter(active_dfs[0].index)
        for frames_tuple, ts in zip(zip(*iters), ts_iter):
            merged = hstack(list(frames_tuple))
            frame_timestamps.append(pd.Timestamp(ts).isoformat())
            if proc is None:
                # open the writer on the first frame so we know the output dimensions
                h, w = merged.shape[:2]
                proc = _open_ffmpeg_writer(outfile, fps, w, h, use_nvenc=False)
            if proc.poll() is not None:
                raise RuntimeError("FFmpeg exited early")
            proc.stdin.write(merged.tobytes())

        if proc is None:
            return "empty"

        # flush stdin and wait for ffmpeg to finish encoding
        proc.stdin.close()
        proc.stdin = None
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            outfile.unlink(missing_ok=True)
            print(f"    [failed export] {outfile.stem}  |  ffmpeg error: {stderr.decode().strip()}")
            return "failed"

        # write sidecar with metadata needed to interpret the clip later
        sidecar.write_text(json.dumps({
            "cameras":              list(active_names),
            "clip_fps":             fps,
            "frame_timestamps_utc": frame_timestamps,
        }))
        print(f"    [exported] {outfile.name}")
        return "exported"

    except Exception as e:
        if proc is not None:
            try: proc.stdin.close()
            except Exception: pass
            proc.wait()
        outfile.unlink(missing_ok=True)
        # strip verbose ffmpeg/ffprobe path noise from the error message
        err = re.sub(r"ffprobe failed for [^:]+: ", "", str(e))
        err = re.sub(r"\[[^\]]+@ 0x[0-9a-f]+\] ", "", err)
        err = re.sub(r"/\S+\.mp4: ", "", err)
        print(f"    [failed export] {outfile.stem}  |  {err.splitlines()[0].strip()}")
        return "failed"


def process_chunk(session_dir, chunk_ts, pokeclips_dir, subject_id, n_workers, no_delete, delete_corrupted=False):
    port_cam_dir = session_dir / "behavior-videos" / "PortCamera"
    chunk_start  = pd.Timestamp(chunk_ts)
    chunk_end    = chunk_start + pd.Timedelta(hours=1)

    # load poke onset times for this 1-hour chunk
    try:
        poke_times = aeon_api.load(
            session_dir / "behavior" / "DelphiController", POKE_READER,
            start=chunk_start, end=chunk_end,
        )
    except Exception as e:
        print(f"  [warn] could not load poke times for {chunk_ts}: {e}")
        _maybe_delete(port_cam_dir, chunk_ts, no_delete)
        return

    poke_times = poke_times[poke_times["PokeState"] == 1]
    if poke_times.empty:
        print(f"  chunk {chunk_ts.strftime('%Y-%m-%dT%H-%M-%S')}: 0 pokes — skipping")
        _maybe_delete(port_cam_dir, chunk_ts, no_delete)
        return

    print(f"  chunk {chunk_ts.strftime('%Y-%m-%dT%H-%M-%S')}: {len(poke_times)} pokes")

    # load frame index data with a margin so clips near chunk boundaries have enough frames
    margin    = pd.Timedelta(seconds=4)
    port_data = load_camera(port_cam_dir, "PortCamera", chunk_start - margin, chunk_end + margin)
    if port_data is None:
        print(f"  [warn] no PortCamera data for {chunk_ts}")
        _maybe_delete(port_cam_dir, chunk_ts, no_delete)
        return

    # OverheadCamera is optional; if present its frames are appended to the right of each clip
    cam_data_list, cam_names = [port_data], ["PortCamera"]
    overhead_dir = session_dir / "behavior-videos" / "OverheadCamera"
    if overhead_dir.is_dir():
        oh_data = load_camera(overhead_dir, "OverheadCamera", chunk_start - margin, chunk_end + margin)
        if oh_data is not None:
            cam_data_list.append(oh_data)
            cam_names.append("OverheadCamera")

    # slice each camera's frame index into per-poke clip windows
    clips_list = [relaxedtriggerclip(cd, poke_times, before=HALF_WINDOW, after=HALF_WINDOW)
                  for cd in cam_data_list]
    pokeclips_dir.mkdir(parents=True, exist_ok=True)
    clip_ids = pd.concat([df["clip_sequence"] for df in clips_list]).unique()

    # export all clips in parallel
    def _run(cid):
        return export_clip(cid, [df[df["clip_sequence"] == cid] for df in clips_list],
                           cam_names, poke_times, pokeclips_dir, subject_id)

    counts = {}
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_run, cid): cid for cid in clip_ids}
        for fut in as_completed(futures):
            try:
                status = fut.result()
            except Exception as e:
                status = "failed"
                print(f"    [unexpected error] clip {futures[fut]}: {e}")
            counts[status] = counts.get(status, 0) + 1

    extras    = {k: v for k, v in counts.items() if k != "exported" and v > 0}
    extra_str = ("  (" + ", ".join(f"{k}: {v}" for k, v in sorted(extras.items())) + ")") if extras else ""
    print(f"    → exported {counts.get('exported', 0)} of {len(clip_ids)} pokes{extra_str}")

    # delete the source chunk only once all clips have been successfully exported
    if not no_delete:
        if _chunk_too_recent(chunk_ts):
            print(f"  [skip delete] chunk may still be recording")
        elif counts.get("failed", 0) > 0 and not delete_corrupted:
            print(f"  [warn] skipping delete — {counts['failed']} clip(s) failed (use --no-delete-corrupted to force)")
        else:
            delete_port_chunk(port_cam_dir, chunk_ts)

# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Extract poke-triggered clips from Delphi session data.")
    parser.add_argument("root",                    help="Root directory to search for sessions")
    parser.add_argument("--output",                help="Output base directory (default: write alongside source data)")
    parser.add_argument("--workers",    type=int, default=4, help="Parallel export workers per chunk (default: 4)")
    parser.add_argument("--no-delete",  action="store_true", help="Do not delete PortCamera source files after processing")
    parser.add_argument("--no-delete-corrupted", action="store_true", help="Keep source chunk even if some clips failed")
    args = parser.parse_args()

    root        = Path(args.root).resolve()
    output_base = Path(args.output).resolve() if args.output else None

    session_dirs = list(find_session_dirs(root))
    print(f"Found {len(session_dirs)} session(s) under {root}")

    for session_dir in session_dirs:
        rel           = session_dir.relative_to(root)
        pokeclips_dir = (output_base / rel if output_base else session_dir) / "behavior-videos" / "PokeClips"
        subject_id    = get_subject_id(session_dir / "metadata")
        port_cam_dir  = session_dir / "behavior-videos" / "PortCamera"

        print(f"\n{'='*60}")
        print(f"Session: {rel}")

        if not port_cam_dir.is_dir():
            print("  [skip] no PortCamera folder")
            continue

        chunk_timestamps = get_chunk_timestamps(port_cam_dir)
        print(f"  {len(chunk_timestamps)} chunk(s) to process")

        for chunk_ts in chunk_timestamps:
            process_chunk(session_dir, chunk_ts, pokeclips_dir, subject_id,
                          n_workers=args.workers, no_delete=args.no_delete,
                          delete_corrupted=not args.no_delete_corrupted)


if __name__ == "__main__":
    main()
