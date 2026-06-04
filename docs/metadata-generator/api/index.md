# API Reference

The `metadata_generator` package is organised into one module per AIND metadata type,
plus shared helpers.

| Module | Purpose |
|--------|---------|
| [`cli`](cli.md) | CLI argument parser |
| [`config`](config.md) | Pipeline configuration dataclass and builder |
| [`subject`](subject.md) | Fetch and write AIND Subject metadata |
| [`instrument`](instrument.md) | Build AIND Instrument metadata |
| [`acquisition`](acquisition.md) | Build AIND Acquisition metadata |
| [`procedures`](procedures.md) | Parse surgery notes and build AIND Procedures metadata |
| [`utils`](utils.md) | Shared helpers for instrument introspection |
