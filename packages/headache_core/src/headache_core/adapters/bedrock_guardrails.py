"""Amazon Bedrock Guardrails vía `ApplyGuardrail`: una llamada evalúa ataques de prompt, filtros
de contenido, temas vetados, palabras y PII, sin invocar ningún modelo.

El filtro PROMPT_ATTACK solo actúa sobre contenido de ENTRADA marcado como `guard_content`;
para la inyección indirecta se evalúa cada documento como entrada (una llamada por documento,
se cobra por unidades de texto de 1000 caracteres).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from botocore.exceptions import BotoCoreError, ClientError

from headache_core.ports.guardrails import ShieldUnavailableError, ShieldVerdict

if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime import BedrockRuntimeClient
    from mypy_boto3_bedrock_runtime.type_defs import ApplyGuardrailResponseTypeDef


class BedrockGuardrailShield:
    def __init__(
        self, client: BedrockRuntimeClient, *, guardrail_id: str, version: str = "DRAFT"
    ) -> None:
        self._client = client
        self._id = guardrail_id
        self._version = version

    async def _apply(self, text: str, source: Literal["INPUT", "OUTPUT"]) -> ShieldVerdict:
        try:
            response = await asyncio.to_thread(
                self._client.apply_guardrail,
                guardrailIdentifier=self._id,
                guardrailVersion=self._version,
                source=source,
                content=[{"text": {"text": text, "qualifiers": ["guard_content"]}}],
            )
        except (BotoCoreError, ClientError) as exc:
            raise ShieldUnavailableError(f"Bedrock Guardrails: {exc}") from exc
        return verdict_from(response)

    async def scan_prompt(self, text: str) -> ShieldVerdict:
        return await self._apply(text, "INPUT")

    async def scan_documents(self, texts: Sequence[str]) -> list[ShieldVerdict]:
        return list(await asyncio.gather(*(self._apply(t, "INPUT") for t in texts)))

    async def scan_output(self, text: str) -> ShieldVerdict:
        return await self._apply(text, "OUTPUT")


def verdict_from(response: ApplyGuardrailResponseTypeDef) -> ShieldVerdict:
    """Solo cuenta lo BLOQUEADO: lo anonimizado ya viene corregido en `outputs`."""
    if response["action"] != "GUARDRAIL_INTERVENED":
        return ShieldVerdict()
    attack = False
    categories: list[str] = []
    for assessment in response.get("assessments", []):
        for item in assessment.get("contentPolicy", {}).get("filters", []):
            if item.get("action") != "BLOCKED":
                continue
            if item["type"] == "PROMPT_ATTACK":
                attack = True
            else:
                categories.append(item["type"].lower())
        topics = assessment.get("topicPolicy", {}).get("topics", [])
        categories += [f"topic:{t['name']}" for t in topics if t.get("action") == "BLOCKED"]
        words = assessment.get("wordPolicy", {})
        if any(
            w.get("action") == "BLOCKED"
            for w in (*words.get("customWords", []), *words.get("managedWordLists", []))
        ):
            categories.append("word")
        pii = assessment.get("sensitiveInformationPolicy", {}).get("piiEntities", [])
        categories += [f"pii:{p['type'].lower()}" for p in pii if p.get("action") == "BLOCKED"]
    return ShieldVerdict(attack, tuple(dict.fromkeys(categories)))
