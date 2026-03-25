from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class LoadType(str, Enum):
    full_load = "full_load"
    incremental_load = "incremental_load"

    def is_incremental(self) -> bool:
        return self == LoadType.incremental_load


class DecimalSeparator(str, Enum):
    comma = "comma"
    dot = "dot"


class Credentials(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    api_key: str = Field(alias="#api_key")


class ReportSettings(BaseModel):
    eshop_id: str
    date_from: str
    date_to: str
    output_detail: bool = False


class Destination(BaseModel):
    table_name: str = ""
    load_type: LoadType = LoadType.incremental_load
    decimal_separator: DecimalSeparator = DecimalSeparator.comma


class Configuration(BaseModel):
    credentials: Credentials
    report_settings: ReportSettings
    destination: Destination = Destination()
