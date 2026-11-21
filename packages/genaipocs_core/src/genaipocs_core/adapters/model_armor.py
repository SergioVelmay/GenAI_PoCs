"""Model Armor (GCP) como escudo de contenido del harness, fase 8.G1.

Equivale a Prompt Shields + análisis de texto (Azure) y a `ApplyGuardrail` (AWS): una plantilla
(`model_armor.tf`) reúne los filtros y cada llamada devuelve el resultado de cada uno, sin
invocar ningún modelo nuestro:

- `pi_and_jailbreak`: prompt injection y jailbreak → `attack`.
- `rai`: odio, acoso, sexual explícito, peligroso → categorías normalizadas.
- `sdp`: Sensitive Data Protection (en la plantilla, solo datos de pago) → `pii:<infotype>`.
- `malicious_uris` y `csam` → `malicious_uri` y `csam`.

Tres diferencias con los otros dos escudos, para defenderlas:

- **Sin modo documento**: cada documento se evalúa como un prompt (`sanitizeUserPrompt`), en
  paralelo, como en Bedrock. Model Armor evalúa cada texto suelto, sin historial.
- **Endpoint regional**: la plantilla vive en una región (`europe-west4`) y solo se puede usar
  desde `modelarmor.<región>.rep.googleapis.com`; el endpoint global solo gestiona las *floor
  settings*.
- **Un "no encontré nada" solo vale si corrieron todos los filtros**: si algún filtro se salta
  (`invocation_result` PARTIAL o FAILURE, p. ej. por pasarse de tokens) y nada coincidió, el
  escudo se declara no disponible y el harness decide (por defecto, falla cerrado). Lo que sí
  coincidió bloquea aunque otro filtro fallara.

Límites del servicio (documentación, 21-11-2026): con menos de 3 palabras el filtro de inyección
no detecta nada; no decodifica base64 ni hex; el español está entre los idiomas probados.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import GoogleAPIError
from google.cloud.modelarmor_v1 import (
    DataItem,
    FilterMatchState,
    FilterResult,
    InvocationResult,
    SanitizationResult,
    SanitizeModelResponseRequest,
    SanitizeModelResponseResponse,
    SanitizeUserPromptRequest,
    SanitizeUserPromptResponse,
)

from genaipocs_core.config import Settings
from genaipocs_core.ports.guardrails import ShieldUnavailableError, ShieldVerdict

# Mismos nombres que los otros escudos donde hay equivalente (Azure: `hate`, `sexual`).
RAI_CATEGORIES = {
    "HATE_SPEECH": "hate",
    "SEXUALLY_EXPLICIT": "sexual",
    "HARASSMENT": "harassment",
    "DANGEROUS": "dangerous",
}
MATCH = FilterMatchState.MATCH_FOUND


class ModelArmorClient(Protocol):
    async def sanitize_user_prompt(
        self, *, request: SanitizeUserPromptRequest
    ) -> SanitizeUserPromptResponse: ...

    async def sanitize_model_response(
        self, *, request: SanitizeModelResponseRequest
    ) -> SanitizeModelResponseResponse: ...


def model_armor_options(location: str) -> ClientOptions:
    """Endpoint regional de la plantilla: con el global, `sanitize*` no la encuentra."""
    return ClientOptions(api_endpoint=f"modelarmor.{location}.rep.googleapis.com")


class ModelArmorShield:
    def __init__(
        self, client: ModelArmorClient, *, template: str, timeout_seconds: float = 5.0
    ) -> None:
        self._client = client
        # `projects/<proyecto>/locations/<región>/templates/<id>`.
        self._template = template
        self._timeout = timeout_seconds

    async def _prompt(self, text: str) -> ShieldVerdict:
        request = SanitizeUserPromptRequest(
            name=self._template, user_prompt_data=DataItem(text=text)
        )
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._client.sanitize_user_prompt(request=request)
        except (GoogleAPIError, TimeoutError) as exc:
            raise ShieldUnavailableError(f"Model Armor sanitizeUserPrompt: {exc}") from exc
        return verdict_from(response.sanitization_result)

    async def scan_prompt(self, text: str) -> ShieldVerdict:
        return await self._prompt(text)

    async def scan_documents(self, texts: Sequence[str]) -> list[ShieldVerdict]:
        return list(await asyncio.gather(*(self._prompt(t) for t in texts)))

    async def scan_output(self, text: str) -> ShieldVerdict:
        request = SanitizeModelResponseRequest(
            name=self._template, model_response_data=DataItem(text=text)
        )
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._client.sanitize_model_response(request=request)
        except (GoogleAPIError, TimeoutError) as exc:
            raise ShieldUnavailableError(f"Model Armor sanitizeModelResponse: {exc}") from exc
        return verdict_from(response.sanitization_result)


def _filter_categories(result: FilterResult) -> tuple[bool, list[str]]:
    """(¿ataque?, categorías) de un filtro. Solo uno de los campos viene relleno (`oneof`)."""
    if result.pi_and_jailbreak_filter_result.match_state == MATCH:
        return True, []
    rai = result.rai_filter_result
    if rai.match_state == MATCH:
        matched = [
            RAI_CATEGORIES.get(item.filter_type.name, item.filter_type.name.lower())
            for item in rai.rai_filter_type_results.values()
            if item.match_state == MATCH
        ]
        return False, matched or ["rai"]
    sdp = result.sdp_filter_result
    if sdp.inspect_result.match_state == MATCH:
        found = [f"pii:{f.info_type.lower()}" for f in sdp.inspect_result.findings]
        return False, found or ["pii"]
    if sdp.deidentify_result.match_state == MATCH:
        found = [f"pii:{t.lower()}" for t in sdp.deidentify_result.info_types]
        return False, found or ["pii"]
    if result.malicious_uri_filter_result.match_state == MATCH:
        return False, ["malicious_uri"]
    if result.csam_filter_filter_result.match_state == MATCH:
        return False, ["csam"]
    if result.virus_scan_filter_result.match_state == MATCH:
        return False, ["virus"]
    return False, []


def verdict_from(result: SanitizationResult) -> ShieldVerdict:
    attack = False
    categories: list[str] = []
    for item in result.filter_results.values():
        is_attack, found = _filter_categories(item)
        attack = attack or is_attack
        categories += found
    # Los resultados vienen en mapas (sin orden garantizado): orden alfabético, estable.
    verdict = ShieldVerdict(attack, tuple(sorted(set(categories))))
    if verdict.flagged:
        return verdict
    if result.invocation_result != InvocationResult.SUCCESS:
        state = InvocationResult(result.invocation_result).name
        raise ShieldUnavailableError(f"Model Armor no ejecutó todos los filtros ({state}).")
    return verdict


def template_name(settings: Settings) -> str:
    gcp, armor = settings.gcp, settings.gcp_model_armor
    if not (gcp.project and armor.template):
        raise ValueError("Faltan GCP__PROJECT y GCP_MODEL_ARMOR__TEMPLATE.")
    location = armor.location or gcp.region
    return f"projects/{gcp.project}/locations/{location}/templates/{armor.template}"
