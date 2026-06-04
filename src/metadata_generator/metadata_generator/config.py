from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
from metadata_generator.cli import parse_args
import os


# ----------------------------------------
# Instrument metadata generation utilities
# ----------------------------------------
@dataclass
class MetadataGenerationConfig:
    generate_subject: bool = True
    generate_instrument: bool = True
    generate_procedures: bool = True
    generate_acquisition: bool = True


# ----------------------------------------
# Metadata generation configuration
# ----------------------------------------
# Load environment variables from .env file
load_dotenv()  # loads .env automatically


def _as_bool(value: str | None, default: bool = False) -> bool:
    """
    Convert a string environment-variable value to bool.

    Parameters
    ----------
    value : str or None
        Raw string value (e.g. from ``os.getenv``). ``None`` triggers the default.
    default : bool, optional
        Value returned when *value* is ``None``. Default is ``False``.

    Returns
    -------
    bool
        ``True`` if *value* is one of ``{"1", "true", "yes", "y"}`` (case-insensitive),
        otherwise ``False`` (or *default* when *value* is ``None``).
    """
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y"}


@dataclass
class PipelineConfig:
    subject_id: str
    protocol_id: str
    current_experiment: str

    dataset_root: Path
    metadata_output_path: Path
    surgery_notes_path: Path

    instrument_id: str
    experiment_room: str
    acquisition_type: str
    delphi_computer_id: str

    experimenters: list[str]
    surgeons: list[str]

    generate_subject: bool
    generate_instrument: bool
    generate_procedures: bool
    generate_acquisition: bool


def build_config():
    """
    Build a ``PipelineConfig`` from CLI arguments and environment variables.

    CLI arguments take precedence over environment variables. Missing required
    fields (e.g. ``SUBJECT_ID``) will raise at construction time if neither
    source provides a value.

    Returns
    -------
    PipelineConfig
        Fully populated pipeline configuration dataclass.
    """
    args = parse_args()

    subject_id = args.subject_id or os.getenv("SUBJECT_ID")
    protocol_id = args.protocol_id or os.getenv("PROTOCOL_ID")
    current_experiment = args.current_experiment or os.getenv("CURRENT_EXPERIMENT")

    dataset_root = Path(args.dataset_root or os.getenv("DATASET_ROOT"))

    metadata_output_path = Path(args.metadata_output_path or os.getenv("METADATA_OUTPUT_PATH"))

    # Surgery notes path: derive if not explicitly provided
    if args.surgery_notes_path:
        surgery_notes_path = Path(args.surgery_notes_path)
    elif os.getenv("SURGERY_NOTES_PATH"):
        surgery_notes_path = (
            Path(os.getenv("SURGERY_NOTES_PATH"))
            / subject_id
            / f"{subject_id}_craniotomy-implantation.docx"
        )
    else:
        surgery_notes_path = (
            Path(r"\\allen\aind\scratch\chronos\surgeryNotes")
            / subject_id
            / f"{subject_id}_craniotomy-implantation.docx"
        )

    experimenters = (
        args.experimenters.split(",")
        if args.experimenters
        else os.getenv("EXPERIMENTERS", "").split(",")
    )

    surgeons = args.surgeons.split(",") if args.surgeons else os.getenv("SURGEONS", "").split(",")

    return PipelineConfig(
        subject_id=subject_id,
        protocol_id=protocol_id,
        current_experiment=current_experiment,
        dataset_root=dataset_root,
        metadata_output_path=metadata_output_path,
        surgery_notes_path=surgery_notes_path,
        instrument_id=args.instrument_id or os.getenv("INSTRUMENT_ID"),
        experiment_room=args.experiment_room or os.getenv("EXPERIMENT_ROOM"),
        acquisition_type=args.acquisition_type or os.getenv("ACQUISITION_TYPE"),
        delphi_computer_id=args.delphi_computer_id or os.getenv("DELPHI_COMPUTER_ID"),
        experimenters=[e for e in experimenters if e],
        surgeons=[s for s in surgeons if s],
        generate_subject=not args.skip_subject and _as_bool(os.getenv("GENERATE_SUBJECT"), True),
        generate_instrument=not args.skip_instrument
        and _as_bool(os.getenv("GENERATE_INSTRUMENT"), True),
        generate_procedures=not args.skip_procedures
        and _as_bool(os.getenv("GENERATE_PROCEDURES"), True),
        generate_acquisition=not args.skip_acquisition
        and _as_bool(os.getenv("GENERATE_ACQUISITION"), True),
    )
