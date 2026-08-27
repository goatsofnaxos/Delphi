# API Reference — experiment-conductor

| Module | Description |
|--------|-------------|
| [cli](cli.md) | Command-line entry point (`conductor` command) |
| [config](config.md) | `ConductorConfig` dataclass and `build_config()` loader |
| [session](session.md) | `SessionPhase` enum and `SessionState` dataclass |
| [session_manager](session_manager.md) | `SessionManager` — multi-session orchestrator and main loop |
| [watcher](watcher.md) | Network-drive session discovery (`discover_sessions`) |
| [pipeline_bridge](pipeline_bridge.md) | delphi-data consolidation and dataset building |
| [metadata_bridge](metadata_bridge.md) | AIND metadata check and generation |
| [noise_floor](noise_floor.md) | Ephys noise-floor estimation from raw binary data |
| [uploader_bridge](uploader_bridge.md) | S3 upload job submission with duplicate-submission protection |
