# API Reference

Module-by-module reference for `experiment_conductor`.

| Module | Purpose |
|--------|---------|
| [conductor](conductor.md) | Main orchestration logic and entry point |
| [config](config.md) | `.env` + CLI configuration loading |
| [state](state.md) | Thread-safe phase/state management |
| [pipeline_bridge](pipeline_bridge.md) | delphi-data pipeline integration |
| [metadata_bridge](metadata_bridge.md) | AIND metadata generation and updates |
| [uploader_bridge](uploader_bridge.md) | S3 upload job submission with pause/resume |
| [hotkeys](hotkeys.md) | Global hotkey listener |
