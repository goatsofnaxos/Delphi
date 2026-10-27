"""Ephys noise-floor estimation from raw binary data.

Reads a short segment of continuous binary data from an Open Ephys or
SpikeGLX recording and computes the root-mean-square (RMS) amplitude per
channel.  The result is written to ``ecephys/noise_floor.json`` alongside
the raw data and returned as a dict.

Supported formats
-----------------
* **Open Ephys** — ``.dat`` files with a 1024-byte ASCII header.
* **SpikeGLX** — ``.ap.bin`` / ``.lf.bin`` files paired with a ``.meta``
  file in the same directory.

Neuropixels calibration
-----------------------
The raw int16 values are scaled to micro-volts using the approximate
bit-to-µV factor for Neuropixels 1.0 / 2.0 probes at the default AP-band
gain.  If the actual gain differs, pass the per-µV factor via
``uv_per_bit``.  The factor is also stored in the output JSON so that
downstream analysis can re-scale if needed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

ECEPHYS_DIRNAME = "ecephys"
NOISE_FLOOR_FILENAME = "noise_floor.json"

# Open Ephys classic: 1024-byte ASCII header before sample data
_OE_HEADER_BYTES = 1024

# Default µV-per-bit scaling for Neuropixels 1.0 / 2.0 at default AP gain.
# Neuropixels 1.0 AP gain = 500  →  0.195 µV/bit (as documented by Imec).
# Neuropixels 2.0 has no internal gain stage; scaling is ~0.0125 µV/bit.
# We default to the NP1 value; pass ``uv_per_bit`` explicitly to override.
_DEFAULT_UV_PER_BIT = 0.195  # µV / LSB, Neuropixels 1.0 AP-band


# ── Format detection helpers ──────────────────────────────────────────────────

def _find_continuous_file(ecephys_dir: Path) -> Path | None:
    """Return the first continuous binary data file found under *ecephys_dir*.

    Searches for SpikeGLX ``.ap.bin`` files first, then Open Ephys
    ``continuous.dat`` files, then any ``.dat`` or ``.bin`` file.

    Parameters
    ----------
    ecephys_dir : Path
        Root of the ecephys data directory.

    Returns
    -------
    Path or None
        Path to the first matching file, or *None* if nothing is found.
    """
    # 1. SpikeGLX AP band
    for p in sorted(ecephys_dir.rglob("*.ap.bin")):
        return p
    # 2. Open Ephys classic continuous file
    for p in sorted(ecephys_dir.rglob("continuous.dat")):
        return p
    # 3. Any .dat or .bin file
    for suffix in (".dat", ".bin"):
        for p in sorted(ecephys_dir.rglob(f"*{suffix}")):
            return p
    return None


def _read_oe_header(path: Path) -> dict[str, str]:
    """Parse the 1024-byte ASCII header from an Open Ephys ``.dat`` file."""
    try:
        with path.open("rb") as fh:
            raw = fh.read(_OE_HEADER_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return {}
    info: dict[str, str] = {}
    for part in raw.split("\n"):
        part = part.strip().rstrip(";")
        if "=" in part:
            k, _, v = part.partition("=")
            info[k.strip()] = v.strip()
    return info


def _read_spikeglx_meta(bin_path: Path) -> dict[str, str]:
    """Parse a SpikeGLX ``.meta`` file adjacent to *bin_path*."""
    meta_path = bin_path.with_suffix(".meta")
    if not meta_path.exists():
        return {}
    info: dict[str, str] = {}
    try:
        for line in meta_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                info[k.strip()] = v.strip()
    except OSError:
        pass
    return info


def _parse_int(d: dict, *keys: str, default: int = 384) -> int:
    for k in keys:
        try:
            return int(d[k])
        except (KeyError, ValueError):
            pass
    return default


def _parse_float(d: dict, *keys: str, default: float | None = None) -> float | None:
    for k in keys:
        try:
            return float(d[k])
        except (KeyError, ValueError):
            pass
    return default


# ── Public API ────────────────────────────────────────────────────────────────

def estimate_noise_floor(
    data_root: Path,
    *,
    n_seconds: float = 10.0,
    max_channels: Optional[int] = None,
    uv_per_bit: float = _DEFAULT_UV_PER_BIT,
) -> Optional[dict]:
    """Estimate the RMS noise floor per channel from raw ephys data.

    Scans *data_root* for an ``ecephys/`` sub-directory and reads a short
    segment of the first continuous binary data file found.  Returns *None*
    (without raising) if no ephys data exists — this is expected for
    Delphi-only sessions that have no electrophysiology recording.

    The result is also written to ``<ecephys_dir>/noise_floor.json`` so
    that downstream analysis scripts can consume it without re-running the
    estimation.

    Parameters
    ----------
    data_root : Path
        Session directory (session root or run directory after consolidation).
        The function searches recursively for ``ecephys/``.
    n_seconds : float
        Duration of data (from the start of the recording) used for the
        estimate.  Default is 10 s, which is usually sufficient.
    max_channels : int, optional
        If set, only the first *max_channels* channels are processed.  Useful
        for large probes when a quick estimate is all that is needed.
    uv_per_bit : float
        Scaling factor applied to convert raw int16 LSB values to micro-volts.
        Default is 0.195 µV/bit (Neuropixels 1.0 AP-band at gain=500).

    Returns
    -------
    dict or None
        On success: a dict with keys

        ``"channel_rms_uv"`` (list[float])
            RMS amplitude per channel in micro-volts.
        ``"median_rms_uv"`` (float)
            Median RMS across channels.
        ``"n_samples"`` (int)
            Number of samples used for the estimate.
        ``"n_channels"`` (int)
            Number of channels processed.
        ``"sample_rate_hz"`` (float or None)
            Parsed sample rate, or *None* if unavailable.
        ``"uv_per_bit"`` (float)
            Scaling factor used.
        ``"source_file"`` (str)
            Absolute path to the binary file that was read.
        ``"timestamp"`` (str)
            ISO-8601 UTC timestamp of when the estimate was computed.

        Returns *None* when no ephys data is found.

    Notes
    -----
    The function gracefully handles files that are shorter than the requested
    ``n_seconds`` by processing whatever samples are available.
    """
    # ── Locate ecephys directory ───────────────────────────────────────────────
    ecephys_dirs = list(data_root.rglob(ECEPHYS_DIRNAME))
    if not ecephys_dirs:
        log.debug("No '%s/' directory found under %s.", ECEPHYS_DIRNAME, data_root)
        return None

    ecephys_dir = ecephys_dirs[0]
    continuous_file = _find_continuous_file(ecephys_dir)
    if continuous_file is None:
        log.debug("No continuous data file found in %s.", ecephys_dir)
        return None

    log.info("Estimating noise floor from %s", continuous_file)

    # ── Parse header / metadata ────────────────────────────────────────────────
    is_spikeglx = continuous_file.name.endswith(".bin")

    if is_spikeglx:
        meta = _read_spikeglx_meta(continuous_file)
        n_channels_file = _parse_int(meta, "nSavedChans", "nChans", default=385)
        sample_rate = _parse_float(meta, "imSampRate", default=30000.0)
        header_offset = 0
    else:
        header = _read_oe_header(continuous_file)
        n_channels_file = _parse_int(header, "num_channels", "channel_count", default=384)
        sample_rate = _parse_float(header, "sampleRate", "sample_rate", default=30000.0)
        # Skip the ASCII header if this looks like an OE classic file
        header_offset = _OE_HEADER_BYTES if header else 0

    n_channels = (
        min(n_channels_file, max_channels) if max_channels else n_channels_file
    )
    sample_rate = sample_rate or 30000.0

    # ── Read raw data ──────────────────────────────────────────────────────────
    bytes_per_frame = n_channels_file * 2  # int16
    n_frames_target = int(n_seconds * sample_rate)
    read_bytes = n_frames_target * bytes_per_frame

    try:
        with continuous_file.open("rb") as fh:
            fh.seek(header_offset)
            raw = fh.read(read_bytes)
    except OSError as exc:
        log.error("Could not read %s: %s", continuous_file, exc)
        return None

    if len(raw) < bytes_per_frame:
        log.warning(
            "Continuous file too small to estimate noise floor: %s", continuous_file
        )
        return None

    # ── Compute RMS ────────────────────────────────────────────────────────────
    n_frames_actual = len(raw) // bytes_per_frame
    # Reshape to (n_frames, n_channels_file), then take first n_channels columns
    data_all = np.frombuffer(
        raw[: n_frames_actual * bytes_per_frame], dtype=np.int16
    ).reshape(n_frames_actual, n_channels_file)
    data = data_all[:, :n_channels].astype(np.float32)

    rms_uv = (np.sqrt(np.mean(data ** 2, axis=0)) * uv_per_bit).tolist()
    median_rms_uv = float(np.median(rms_uv))

    # ── Build result ───────────────────────────────────────────────────────────
    result = {
        "channel_rms_uv": rms_uv,
        "median_rms_uv": median_rms_uv,
        "n_samples": n_frames_actual,
        "n_channels": n_channels,
        "sample_rate_hz": sample_rate,
        "uv_per_bit": uv_per_bit,
        "source_file": str(continuous_file.resolve()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    log.info(
        "Noise floor estimate: %d channels, median RMS = %.2f µV (from %d samples).",
        n_channels,
        median_rms_uv,
        n_frames_actual,
    )

    # ── Write to disk ──────────────────────────────────────────────────────────
    out_path = ecephys_dir / NOISE_FLOOR_FILENAME
    try:
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        log.info("Noise floor saved: %s", out_path)
    except OSError as exc:
        log.warning("Could not write %s: %s", out_path, exc)

    return result
