"""Prompts versionados de este paquete (fase 12.6). Ver `genaipocs_core.application.prompts`."""

from pathlib import Path

from genaipocs_core.application.prompts import PromptCatalog

PROMPTS = PromptCatalog.from_directory(Path(__file__).parent)
