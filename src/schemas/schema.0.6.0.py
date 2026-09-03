import json
import os
from pathlib import Path

from pydantic import BaseModel, Field


class DelphiController(BaseModel):
    poke_pin: int = Field(default=22, ge=0)
    vacuum_close_time_us: int = Field(default=20000, ge=0)
    final_valve_energized_time_us: int = Field(default=110000, ge=0)
    max_odor_delivery_time_us: int = Field(default=10000000, ge=0)
    min_poke_time_us: int = Field(default=10000, ge=10)
    min_odor_delivery_time_us: int = Field(default=10000, ge=0)
    odor_transition_time_us: int = Field(default=30000, ge=0)
    vacuum_setup_time_us: int = Field(default=20000, ge=0)
    odor_dwell_time_us: int = Field(default=0, ge=0)
    com_port: str


class CameraSettings(BaseModel):
    camera_name: str
    frame_rate: int = Field(ge=0)
    exposure: int = Field(ge=0)
    duty_cycle: float = Field(ge=0, le=1)
    serial_number: str
    ffmpeg_input: str
    ffmpeg_output: str


class FirmwareVersion(BaseModel):
    expected_major: int = Field(ge=0)
    expected_minor: int = Field(ge=0)
    expected_patch: int = Field(ge=0)


class HardwareSchema(BaseModel):
    subject_id: str
    session_time: str
    logging_root_path: str
    remote_transfer_root_path: str
    robocopy_script_path: str
    delphi_controller: DelphiController
    camera_settings: CameraSettings
    firmware_version: FirmwareVersion


class StateDefinition(BaseModel):
    name: str
    odor_index: int
    transitions_to: list[str]


class DelphiRule(BaseModel):
    rule_alias: str
    sample_with_replacement: bool
    init_odor_index: int
    state_definitions: list[StateDefinition]


class RuleSchema(BaseModel):
    rule: DelphiRule


if __name__ == "__main__":
    hardware_schema = HardwareSchema.model_json_schema()
    Path(r"src\schemas\hardware-schema.json").write_text(
        json.dumps(hardware_schema, indent=2)
    )
    os.system(
        "dotnet bonsai.sgen "
        r"src\schemas\hardware-schema.json"
        r" -o src\Extensions --serializer yaml"
    )

    rule_schema = RuleSchema.model_json_schema()
    Path("src\\schemas\\rule-schema.json").write_text(json.dumps(rule_schema, indent=2))
    os.system(
        "dotnet bonsai.sgen "
        "src\\schemas\\rule-schema.json"
        r" -o src\Extensions --serializer yaml"
    )
