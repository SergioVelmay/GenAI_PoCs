"""Detector de PII con Microsoft Presidio (extra opcional `pii`).

Instalación: `uv sync --all-packages --extra pii`.

Aporta lo que una regex no puede: nombres de persona (NER de spaCy) en español e inglés, además
de reconocedores con checksum (NIF/NIE, IBAN, tarjetas). Modelos `*_core_*_sm`: pequeños y
rápidos; en producción, `_lg` o transformers si la precisión de nombres lo justifica.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cached_property
from typing import Any

from harness.pii import PiiSpan

# Sin LOCATION ni DATE_TIME: ciudades y fechas son datos de negocio (eventos), no PII aquí.
DEFAULT_ENTITIES = (
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "ES_NIF",
    "ES_NIE",
    "IP_ADDRESS",
)
MODELS = {"es": "es_core_news_sm", "en": "en_core_web_sm"}


class PresidioPiiDetector:
    def __init__(
        self,
        *,
        languages: Sequence[str] = ("es", "en"),
        entities: Sequence[str] = DEFAULT_ENTITIES,
        threshold: float = 0.4,
    ) -> None:
        self._languages = tuple(languages)
        self._entities = list(entities)
        self._threshold = threshold

    @cached_property
    def _engine(self) -> Any:
        # Import perezoso: spaCy tarda en cargar y solo existe con el extra instalado.
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [
                    {"lang_code": lang, "model_name": MODELS[lang]} for lang in self._languages
                ],
            }
        )
        return AnalyzerEngine(
            nlp_engine=provider.create_engine(), supported_languages=list(self._languages)
        )

    def analyze(self, text: str, language: str) -> list[PiiSpan]:
        results = self._engine.analyze(
            text=text,
            language=language if language in self._languages else self._languages[0],
            entities=self._entities,
            score_threshold=self._threshold,
        )
        return [PiiSpan(r.entity_type, r.start, r.end, r.score) for r in results]
