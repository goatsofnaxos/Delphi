# metadata_bridge

AIND metadata check and generation.

`check_metadata_present` verifies that all four required JSON files exist in
the `metadata/` sub-directory of the run dir.  `generate_metadata` calls the
`metadata_generator` package to produce `subject.json`, `instrument.json`,
`acquisition.json`, and `procedures.json`, with each file written
independently so a failure in one does not prevent the others.

::: experiment_conductor.metadata_bridge
