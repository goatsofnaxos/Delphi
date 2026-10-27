# pipeline_bridge

Integration layer for the `delphi-data` package.

`run_pipeline` invokes the full processing pipeline as a subprocess (build-dataset
+ snapshot).  `run_consolidation` merges Bonsai restart run directories.
`move_delphi_metadata` relocates JSONL files to `behavior/metadata/` so the
metadata generator can find them.  `has_delphi_controller_file` checks
whether Delphi Harp data is present before attempting a pipeline run.

::: experiment_conductor.pipeline_bridge
