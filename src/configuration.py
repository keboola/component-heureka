import dataclasses
import json
from dataclasses import dataclass
from typing import List
import dataconf


class ConfigurationBase:
    @staticmethod
    def _convert_private_value(value: str):
        return value.replace('"#', '"pswd_')

    @staticmethod
    def _convert_private_value_inv(value: str):
        if value and value.startswith("pswd_"):
            return value.replace("pswd_", "#", 1)
        else:
            return value

    @classmethod
    def load_from_dict(cls, configuration: dict):
        """
        Initialize the configuration dataclass object from dictionary.
        Args:
            configuration: Dictionary loaded from json configuration.

        Returns:

        """
        json_conf = json.dumps(configuration)
        json_conf = ConfigurationBase._convert_private_value(json_conf)
        return dataconf.loads(json_conf, cls, ignore_unexpected=True)

    @classmethod
    def get_dataclass_required_parameters(cls) -> List[str]:
        """
        Return list of required parameters based on the dataclass definition (no default value)
        Returns: List[str]

        """
        return [cls._convert_private_value_inv(f.name)
                for f in dataclasses.fields(cls)
                if f.default == dataclasses.MISSING
                and f.default_factory == dataclasses.MISSING
                ]


@dataclass
class Credentials(ConfigurationBase):
    email: str = ""
    pswd_password: str = ""


@dataclass
class ReportSettings(ConfigurationBase):
    eshop_id: str = ""
    date_from: str = ""
    date_to: str = ""


@dataclass
class Destination(ConfigurationBase):
    table_name: str = ""
    incremental_load: bool = True


@dataclass
class Configuration(ConfigurationBase):
    credentials: Credentials
    report_settings: ReportSettings
    destination: Destination
    country: str = ""
