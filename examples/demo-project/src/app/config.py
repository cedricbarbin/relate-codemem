"""Configuration loading."""
import os

from .util import read_yaml


class Settings:
    """Runtime settings."""

    def __init__(self, path):
        self.path = path

    def reload(self):
        return parse_config(self.path)


def parse_config(path: str, strict: bool = False) -> dict:
    """Load the YAML configuration file."""
    return read_yaml(os.path.expanduser(path))
