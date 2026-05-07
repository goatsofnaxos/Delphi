# metadata-generator

`metadata-generator` is a Python package and CLI pipeline for generating **AIND v2–compliant metadata** for chronic and delphi experiments.  
It produces standardized metadata JSON files required for AIND ingestion.

The package is designed to align closely with internal AIND metadata‑generation workflows, while remaining flexible.

---

## Generated metadata files

The pipeline can generate the following files:

- **`subject.json`**
  - Primary source: AIND Metadata Service
  - Optional fallback: minimal schema‑valid Subject (for offline/development use)

- **`instrument.json`**
  - Full hardware topology
  - Cameras, ephys assemblies, harps, olfactometer, enclosure, connections

- **`acquisition.json`**
  - Session‑specific metadata
  - Data streams, stimulus epochs, device configurations
  - Supports **Pirouette**, **Delphi**, and **hybrid (Delphi + Pirouette)** experiments

- **`procedures.json`**
  - Parsed from surgery notes (DOCX)
  - Craniotomy, implantation, headframe, anesthesia
  - Probe serial number is parsed here and reused downstream

Each file can be generated independently or skipped via configuration.

---

## Installation

### Requirements

- Python 3.10+
- `uv` (recommended)
- AIND network/VPN (optional, for `subject.json`)

### Install in editable mode

uv pip install -e .

---

## Configuration

Configuration is resolved using three layers, with clear precedence:

CLI arguments

   ↓ override

.env file

   ↓ override

derived defaults in code

### .env configuration

Place .env in the repository root (same directory as pyproject.toml).
Example:

#### Core identifiers
- SUBJECT_ID=xxxxxxx
- PROTOCOL_ID=xxxx
- CURRENT_EXPERIMENT=delphi_pirouette   # delphi | pirouette | delphi_pirouette

#### Paths
- DATASET_ROOT=\\allen\aind\stage\chronic\data\[expt date]
- METADATA_OUTPUT_PATH=example_metadata

#### Instrument / acquisition
- INSTRUMENT_ID=ChronicRig
- EXPERIMENT_ROOM=xxx
- ACQUISITION_TYPE=Longterm chronic recording
- DELPHI_COMPUTER_ID=xxxxxxxx

#### People
- EXPERIMENTERS=Brandon Pratt
- SURGEONS=Carl Schoonover

#### Enable / disable metadata generation
- GENERATE_SUBJECT=true
- GENERATE_INSTRUMENT=true
- GENERATE_PROCEDURES=true
- GENERATE_ACQUISITION=true

### CLI usage

#### Basic execution
uv run python scripts/generate_all_metadata.py

#### Override configuration from CLI
Examples:

uv run scripts/generate_all_metadata.py --metadata-output-path test

uv run python scripts/generate_all_metadata.py \
  --surgery-notes-path \\path\to\surgery_notes.docx

#### Skip individual metadata steps
Each metadata file can be enabled or disabled independently:

MetadataGenerationConfig(

    generate_subject=True,

    generate_instrument=True,

    generate_procedures=True,

    generate_acquisition=True,

)

uv run python scripts/generate_all_metadata.py \
  --skip-subject \
  --skip-procedures

---

## Subject metadata behavior
- **Primary path**: fetched from the AIND Metadata Service
- **Fallback path**: minimal schema‑valid Subject if fetch fails (optional)

---

## Validation & schema compliance
- All metadata objects are round‑trip serialized and re‑validated
- Output is compatible with AIND v2 ingestion requirements

---





