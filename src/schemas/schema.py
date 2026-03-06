import json
from typing import List
from pydantic import BaseModel, Field
from pathlib import Path
import os


class DelphiController(BaseModel):
    final_valve_energized_time_us: int = Field(ge=0)
    max_odor_delivery_time_us: int = Field(ge=0)
    min_poke_time_us: int = Field(ge=0)
    min_odor_delivery_time_us: int = Field(ge=0)
    odor_transition_time_us: int = Field(ge=0)
    poke_pin: int = Field(default=22, ge=0)
    vacuum_setup_time_us: int = Field(ge=0)
    vacuum_close_time_us: int = Field(ge=0)
    com_port: str


class CameraSettings(BaseModel):
    camera_name: str
    frame_rate: int = Field(ge=0)
    exposure: int = Field(ge=0)
    duty_cycle: float = Field(ge=0, le=1)
    serial_number: str
    ffmpeg_input: str
    ffmpeg_output: str


class CameraSettings1(BaseModel):
    camera_name: str
    frame_rate: int = Field(ge=0)
    exposure: int = Field(ge=0)
    duty_cycle: float = Field(ge=0, le=1)
    serial_number: str
    ffmpeg_input: str
    ffmpeg_output: str


class HardwareSchema(BaseModel):
    subject_id: str
    session_time: str
    logging_root_path: str
    remote_transfer_root_path: str
    robocopy_script_path: str
    delphi_controller: DelphiController
    camera_settings: CameraSettings
    camera_settings1: CameraSettings1


class StateDefinition(BaseModel):
    name: str
    odor_index: int
    transitions_to: List[str]


class DelphiRule(BaseModel):
    rule_alias: str
    sample_with_replacement: bool
    init_odor_index: int
    state_definitions: List[StateDefinition]


class RuleSchema(BaseModel):
    rule: DelphiRule


if __name__ == "__main__":
    hardware_schema = HardwareSchema.model_json_schema()
    Path("src\schemas\hardware-schema.json").write_text(
        json.dumps(hardware_schema, indent=2)
    )
    os.system(
        "dotnet bonsai.sgen "
        "src\schemas\hardware-schema.json"
        " -o src\Extensions --serializer yaml"
    )

    rule_schema = RuleSchema.model_json_schema()
    Path("src\schemas\\rule-schema.json").write_text(json.dumps(rule_schema, indent=2))
    os.system(
        "dotnet bonsai.sgen "
        "src\schemas\\rule-schema.json"
        " -o src\Extensions --serializer yaml"
    )
