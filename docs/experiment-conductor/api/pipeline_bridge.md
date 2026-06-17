# pipeline_bridge

Integration layer for the `delphi-data` processing pipeline.

`run_pipeline` invokes the full pipeline as a subprocess (always with `--append` so
new Harp rows accumulate in the existing CSV); `run_consolidation` handles
pirouette-only sessions that have no Delphi controller data; `move_delphi_metadata`
relocates Delphi metadata files from `metadata/` to `behavior/metadata/`.

::: experiment_conductor.pipeline_bridge
