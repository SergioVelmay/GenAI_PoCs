"""Seguridad del harness: prompt injection directa e indirecta y contenido dañino.

- Directa: el mensaje del cliente intenta saltarse las reglas -> se bloquea antes del agente.
- Indirecta: texto de terceros (documentos del RAG, descripciones de partners) trae órdenes ->
  se retiene ese fragmento ANTES de que el modelo lo lea; el resto de la salida sigue.
- Salida: la respuesta final pasa por el escudo (contenido dañino) antes de mostrarse.

El escudo es un puerto: `HeuristicShield` (local, coste 0), Azure Prompt Shields o Bedrock
Guardrails. Ninguno es perfecto: la defensa real es que el modelo no tenga poder (identidad
fuera del modelo, guardas de tools, aprobación humana) aunque lo engañen.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from harness.executor import TurnTrace
from harness.runtime import TurnRecord
from harness.validation import Validator, Violation
from headache_core.application.tools import Tool, ToolContext, ToolExecution, ToolExecutor
from headache_core.ports.guardrails import (
    ContentShieldPort,
    ShieldUnavailableError,
    ShieldVerdict,
)

logger = logging.getLogger(__name__)

QUARANTINE_MARK = "[contenido retenido por seguridad: contenía instrucciones no confiables]"

_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_INJECTION = [
    re.compile(p)
    for p in (
        # Anular instrucciones (es/en). Se compara sin tildes y en minúsculas.
        r"\b(?:ignore|disregard|forget|override)\b.{0,30}\b(?:instructions?|rules|prompts?|guidelines)\b",
        r"\b(?:ignora|olvida|omite|salta(?:te)?)\b.{0,30}\b(?:instrucciones|reglas|indicaciones|normas)\b",
        # Jailbreaks conocidos.
        r"\b(?:jailbreak|developer mode|modo desarrollador|do anything now|god mode|modo dios)\b",
        # Exfiltrar el prompt de sistema.
        r"\b(?:reveal|show|print|repeat|leak)\b.{0,20}\b(?:system prompt|your instructions)\b",
        r"\b(?:muestra|revela|repite|ensena)(?:me)?\b.{0,20}\b(?:prompt|tus instrucciones)\b",
        # Marcadores de rol o de plantilla de chat incrustados.
        r"<\|?(?:im_start|im_end|system|assistant)\|?>|\[/?inst\]|</?system>",
        r"(?m)^\s*#{0,3}\s*(?:system|sistema|assistant|asistente)\s*:",
        r"\bsystem (?:annotation|override|message)\b",
        # Dirigirse a la IA desde un documento y forzar tools o exfiltración.
        r"\b(?:if you are an ai|ai assistant reading|asistente que lea)\b",
        r"\bsi eres (?:una ia|un asistente)\b",
        r"\b(?:request_refund|remember_preference|load_skill)\b",
        r"\b(?:send|forward|envia|reenvia|manda)\b.{0,60}(?:https?://|[\w.+-]+@[\w-]+\.)",
    )
]


def _fold(text: str) -> str:
    text = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[ \t]+", " ", stripped.casefold())


def looks_like_injection(text: str) -> bool:
    folded = _fold(text)
    return any(pattern.search(folded) for pattern in _INJECTION)


class HeuristicShield:
    """Escudo local por patrones (es/en). Barato y explicable; fácil de esquivar por un
    atacante decidido: es la primera capa y la que corre en CI, no la única."""

    async def scan_prompt(self, text: str) -> ShieldVerdict:
        return ShieldVerdict(attack=looks_like_injection(text))

    async def scan_documents(self, texts: Sequence[str]) -> list[ShieldVerdict]:
        return [ShieldVerdict(attack=looks_like_injection(t)) for t in texts]

    async def scan_output(self, text: str) -> ShieldVerdict:
        return ShieldVerdict()


def prompt_guard(
    shield: ContentShieldPort, *, fail_open: bool = False
) -> Validator[str, ToolContext]:
    async def validate(message: str, _: ToolContext) -> list[Violation]:
        try:
            verdict = await shield.scan_prompt(message)
        except ShieldUnavailableError:
            return [] if fail_open else [_unavailable()]
        violations: list[Violation] = []
        if verdict.attack:
            violations.append(
                Violation(
                    "prompt_injection", "Intento de manipular al asistente.", correctable=False
                )
            )
        violations += [_category(c) for c in verdict.categories]
        return violations

    return validate


def output_guard(
    shield: ContentShieldPort, *, fail_open: bool = False
) -> Validator[TurnRecord, ToolContext]:
    async def validate(record: TurnRecord, _: ToolContext) -> list[Violation]:
        try:
            verdict = await shield.scan_output(record.answer)
        except ShieldUnavailableError:
            return [] if fail_open else [_unavailable()]
        return [_category(c) for c in verdict.categories]

    return validate


def _category(category: str) -> Violation:
    return Violation(f"unsafe_content:{category}", "Contenido no permitido.", correctable=False)


def _unavailable() -> Violation:
    return Violation("shield_unavailable", "No se pudo verificar el contenido.", correctable=False)


@dataclass(frozen=True, slots=True)
class DocumentShield:
    """Guarda de salida de tools: retiene fragmentos de terceros con instrucciones incrustadas."""

    shield: ContentShieldPort
    # Tools cuya salida trae texto de terceros (no confiable).
    tools: frozenset[str]
    # Las cadenas cortas (ids, fechas, importes) no pueden llevar una inyección útil.
    min_chars: int = 40
    fail_open: bool = False

    async def inspect(
        self, tool: Tool[Any], execution: ToolExecution, context: ToolContext, trace: TurnTrace
    ) -> ToolExecution:
        if tool.name not in self.tools or execution.is_error:
            return execution
        try:
            data = json.loads(execution.output)
        except json.JSONDecodeError:
            return execution
        leaves = [(path, text) for path, text in _strings(data) if len(text) >= self.min_chars]
        if not leaves:
            return execution
        try:
            verdicts = await self.shield.scan_documents([text for _, text in leaves])
        except ShieldUnavailableError:
            if self.fail_open:
                return execution
            trace.findings.append("shield_unavailable")
            return ToolExecutor.failure(
                execution.call,
                "shield_unavailable",
                "No se pudo verificar el contenido de esta fuente; no lo uses.",
                time.perf_counter(),
            )
        flagged = [
            path for (path, _), verdict in zip(leaves, verdicts, strict=True) if verdict.attack
        ]
        if not flagged:
            return execution
        logger.warning("Inyección indirecta retenida en %s: %d fragmentos", tool.name, len(flagged))
        trace.findings.append("indirect_injection")
        for path in flagged:
            data = _replace(data, path, QUARANTINE_MARK)
        output = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return replace(execution, output=output)


type _Path = tuple[str | int, ...]


def _strings(value: Any, path: _Path = ()) -> list[tuple[_Path, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [item for k, v in value.items() for item in _strings(v, (*path, k))]
    if isinstance(value, list):
        return [item for i, v in enumerate(value) for item in _strings(v, (*path, i))]
    return []


def _replace(value: Any, path: _Path, new: str) -> Any:
    if not path:
        return new
    head, *rest = path
    if isinstance(value, dict):
        return {k: _replace(v, tuple(rest), new) if k == head else v for k, v in value.items()}
    if isinstance(value, list):
        return [_replace(v, tuple(rest), new) if i == head else v for i, v in enumerate(value)]
    return value
