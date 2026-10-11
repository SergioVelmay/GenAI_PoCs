"""Azure AI Content Safety: Prompt Shields (ataques directos e indirectos) + análisis de texto.

REST `2024-09-01` con cliente httpx inyectado. Autenticación con Entra ID (rol *Cognitive
Services User*, sin claves) o con clave solo en el tier gratuito/desarrollo.
Límites del servicio: 10 000 caracteres por prompt y como mucho 5 documentos por llamada.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import httpx

from headache_core.ports.guardrails import ShieldUnavailableError, ShieldVerdict

if TYPE_CHECKING:
    from azure.core.credentials_async import AsyncTokenCredential

type AuthHeaders = Callable[[], Awaitable[Mapping[str, str]]]

API_VERSION = "2024-09-01"
SCOPE = "https://cognitiveservices.azure.com/.default"
HARM_CATEGORIES = ("Hate", "SelfHarm", "Sexual", "Violence")
MAX_CHARS = 10_000
MAX_DOCUMENTS = 5


def api_key_auth(key: str) -> AuthHeaders:
    async def headers() -> Mapping[str, str]:
        return {"Ocp-Apim-Subscription-Key": key}

    return headers


def entra_id_auth(credential: AsyncTokenCredential) -> AuthHeaders:
    async def headers() -> Mapping[str, str]:
        token = await credential.get_token(SCOPE)
        return {"Authorization": f"Bearer {token.token}"}

    return headers


class AzureContentSafetyShield:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        endpoint: str,
        auth: AuthHeaders,
        # Severidad 0/2/4/6: desde 4 (media) se considera dañino.
        harm_threshold: int = 4,
        analyze_prompts: bool = True,
    ) -> None:
        self._client = client
        self._endpoint = endpoint.rstrip("/")
        self._auth = auth
        self._threshold = harm_threshold
        self._analyze_prompts = analyze_prompts

    async def _post(self, operation: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self._endpoint}/contentsafety/text:{operation}",
                params={"api-version": API_VERSION},
                headers=dict(await self._auth()),
                json=body,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ShieldUnavailableError(f"Content Safety {operation}: {exc}") from exc
        data: dict[str, Any] = response.json()
        return data

    async def _harm(self, text: str) -> tuple[str, ...]:
        data = await self._post(
            "analyze",
            {
                "text": text[:MAX_CHARS],
                "categories": list(HARM_CATEGORIES),
                "outputType": "FourSeverityLevels",
            },
        )
        return tuple(
            item["category"].lower()
            for item in data.get("categoriesAnalysis", [])
            if item.get("severity", 0) >= self._threshold
        )

    async def scan_prompt(self, text: str) -> ShieldVerdict:
        shield = self._post("shieldPrompt", {"userPrompt": text[:MAX_CHARS], "documents": []})
        if not self._analyze_prompts:
            data = await shield
            return ShieldVerdict(attack=bool(data["userPromptAnalysis"]["attackDetected"]))
        data, categories = await asyncio.gather(shield, self._harm(text))
        return ShieldVerdict(bool(data["userPromptAnalysis"]["attackDetected"]), categories)

    async def scan_documents(self, texts: Sequence[str]) -> list[ShieldVerdict]:
        per_document = MAX_CHARS // MAX_DOCUMENTS
        batches = [texts[i : i + MAX_DOCUMENTS] for i in range(0, len(texts), MAX_DOCUMENTS)]
        responses = await asyncio.gather(
            *(
                self._post(
                    "shieldPrompt",
                    {"userPrompt": "", "documents": [t[:per_document] for t in batch]},
                )
                for batch in batches
            )
        )
        verdicts: list[ShieldVerdict] = []
        for batch, data in zip(batches, responses, strict=True):
            analyses = data.get("documentsAnalysis", [])
            if len(analyses) != len(batch):
                raise ShieldUnavailableError(
                    "Prompt Shields devolvió un número de análisis distinto."
                )
            verdicts += [ShieldVerdict(attack=bool(a["attackDetected"])) for a in analyses]
        return verdicts

    async def scan_output(self, text: str) -> ShieldVerdict:
        return ShieldVerdict(categories=await self._harm(text))
