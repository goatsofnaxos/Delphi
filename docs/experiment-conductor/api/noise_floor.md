# noise_floor

Ephys noise-floor estimation from raw binary data.

Reads a short segment (default 10 s) of continuous binary data from an
Open Ephys `.dat` or SpikeGLX `.ap.bin` file and computes the
root-mean-square (RMS) amplitude per channel in µV.  The result is written
to `ecephys/noise_floor.json`.

## Supported formats

| Format | File pattern |
|--------|-------------|
| SpikeGLX | `*.ap.bin` + `*.meta` |
| Open Ephys classic | `continuous.dat` (1024-byte ASCII header) |
| Fallback | Any `*.dat` or `*.bin` file |

## Output JSON keys

| Key | Type | Description |
|-----|------|-------------|
| `channel_rms_uv` | list[float] | RMS amplitude per channel (µV) |
| `median_rms_uv` | float | Median RMS across all channels |
| `n_samples` | int | Number of samples used |
| `n_channels` | int | Number of channels processed |
| `sample_rate_hz` | float | Parsed sample rate |
| `uv_per_bit` | float | Scaling factor applied |
| `source_file` | str | Absolute path of the binary file |
| `timestamp` | str | ISO-8601 UTC timestamp of estimation |

::: experiment_conductor.noise_floor
