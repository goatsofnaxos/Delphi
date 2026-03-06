import json
from typing import List
from pydantic import BaseModel, Field
from pathlib import Path
import os


class DelphiController(BaseModel):
    com_port: str
    poke_pin: int
    max_odor_delivery_time_us: int = Field(ge=0)
    min_poke_time_us: int = Field(ge=0)
    min_odor_delivery_time_us: int = Field(ge=0)
    odor_setup_time_us: int = Field(ge=0)
    odor_dwell_time_us: int = Field(ge=0)

class FluidicSettings(BaseModel):
    leak_flowmeter_adc: int = Field(ge=0)
    leak_threshold: float = Field(ge=0)
    manual_flometer_adc: int = Field(ge=0)
    manual_flowmeter_target_flow_rate: float = Field(ge=0)
    manual_flowmeter_flow_rate_tolerance: float = Field(ge=0)
    proportional_valve_flowmeter_0_adc: int = Field(ge=0)
    proportional_valve_flowmeter_0_target_flowrate: float = Field(ge=0)
    proportional_valve_flowmeter_1_adc: int = Field(ge=0)
    proportional_valve_flowmeter_1_target_flowrate: float = Field(ge=0)
    proportional_valve_flowmeter_2_adc: int = Field(ge=0)
    proportional_valve_flowmeter_2_target_flowrate: float = Field(ge=0)

class DeLuxDriver(BaseModel):
    com_port: str
    ch1_current: int = Field(ge=0)
    ch1_pulse_frequency: int = Field(ge=0)
    ch1_pulse_duty_cycle: float = Field(ge=0)
    ch2_current: int = Field(ge=0)
    ch2_pulse_frequency: int = Field(ge=0)
    ch2_pulse_duty_cycle: float = Field(ge=0)
    ch3_current: int = Field(ge=0)
    ch3_pulse_frequency: int = Field(ge=0)
    ch3_pulse_duty_cycle: float = Field(ge=0)

class WhiteRabbit(BaseModel):
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
    rule_name: str
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
