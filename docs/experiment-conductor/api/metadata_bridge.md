# metadata_bridge

AIND-compliant metadata generation and post-hoc updates.

Wraps `metadata-generator` to produce `subject.json`, `acquisition.json`,
`procedures.json`, and `instrument.json`.  Also handles updating the
acquisition end time and verifying `probe.json` for Pirouette experiments.

::: experiment_conductor.metadata_bridge
