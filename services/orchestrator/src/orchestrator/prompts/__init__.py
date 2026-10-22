"""Prompts versionados de este paquete (fase 12.6). Ver `headache_core.application.prompts`."""

from pathlib import Path

from headache_core.application.prompts import PromptCatalog

PROMPTS = PromptCatalog.from_directory(Path(__file__).parent)
