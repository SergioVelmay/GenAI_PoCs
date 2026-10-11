"""Harness de producción del Knowledge & Booking Agent: el mismo para los siete sabores."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from booking_agent.runtime import AgentRuntime, JournaledExecutor
from booking_agent.spec import AgentSpec
from harness import GuardedExecutor, GuardedRuntime
from harness.pii import PiiDetector, RegexPiiDetector, pii_filter
from harness.rules.refunds import refund_claims_grounded, refund_tool_guards
from harness.security import DocumentShield, HeuristicShield, output_guard, prompt_guard
from headache_core.config import Settings
from headache_core.ports.guardrails import ContentShieldPort

# Tools que devuelven texto de terceros (documentos del RAG, descripciones de partners).
UNTRUSTED_TOOLS = frozenset({"search_knowledge", "get_event", "search_events"})


def guarded_executor(
    spec: AgentSpec, shield: ContentShieldPort | None = None, *, fail_open: bool = False
) -> JournaledExecutor:
    documents = DocumentShield(shield or HeuristicShield(), UNTRUSTED_TOOLS, fail_open=fail_open)
    inner = GuardedExecutor(spec.tools, refund_tool_guards(), output_guards=[documents])
    return JournaledExecutor(spec.tools, inner=inner)


def guarded_agent(
    agent: AgentRuntime,
    *,
    shield: ContentShieldPort | None = None,
    pii: PiiDetector | None = None,
    fail_open: bool = False,
) -> GuardedRuntime:
    shield = shield or HeuristicShield()
    return GuardedRuntime(
        agent,
        input_guards=[prompt_guard(shield, fail_open=fail_open)],
        output_guards=[refund_claims_grounded, output_guard(shield, fail_open=fail_open)],
        # Entrada: el detector configurado (Presidio detecta nombres). Salida: solo patrones,
        # para no borrar por error títulos de eventos que un NER confunda con personas.
        input_filters=[pii_filter(pii or RegexPiiDetector())],
        output_filters=[pii_filter(RegexPiiDetector())],
    )


def build_pii_detector(settings: Settings) -> PiiDetector:
    if settings.guardrails.pii == "presidio":
        from harness.pii_presidio import PresidioPiiDetector

        return PresidioPiiDetector()
    return RegexPiiDetector()


@asynccontextmanager
async def build_shield(settings: Settings) -> AsyncIterator[ContentShieldPort]:
    """Escudo configurado. Los de nube tienen coste por llamada y exigen la infraestructura."""
    kind = settings.guardrails.shield
    if kind == "heuristic":
        yield HeuristicShield()
    elif kind == "azure":
        import httpx
        from azure.identity.aio import DefaultAzureCredential

        from headache_core.adapters.azure_content_safety import (
            AzureContentSafetyShield,
            api_key_auth,
            entra_id_auth,
        )

        config = settings.content_safety
        if not config.endpoint:
            raise ValueError("Falta CONTENT_SAFETY__ENDPOINT.")
        async with httpx.AsyncClient(timeout=5.0) as client, DefaultAzureCredential() as cred:
            key = config.api_key
            auth = api_key_auth(key.get_secret_value()) if key else entra_id_auth(cred)
            yield AzureContentSafetyShield(client, endpoint=config.endpoint, auth=auth)
    else:
        import boto3

        from headache_core.adapters.bedrock_guardrails import BedrockGuardrailShield

        guardrail = settings.bedrock_guardrail
        if not guardrail.guardrail_id:
            raise ValueError("Falta BEDROCK_GUARDRAIL__GUARDRAIL_ID.")
        session = boto3.Session(profile_name=guardrail.profile, region_name=guardrail.region)
        yield BedrockGuardrailShield(
            session.client("bedrock-runtime"),
            guardrail_id=guardrail.guardrail_id,
            version=guardrail.version,
        )
