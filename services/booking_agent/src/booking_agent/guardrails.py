"""Harness de producción del Knowledge & Booking Agent: el mismo para los siete sabores."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from booking_agent.runtime import AgentRuntime, JournaledExecutor
from booking_agent.spec import AgentSpec
from genaipocs_core.config import Settings
from genaipocs_core.ports.approvals import ApprovalStore
from genaipocs_core.ports.guardrails import ContentShieldPort
from harness import GuardedExecutor, GuardedRuntime
from harness.pii import PiiDetector, RegexPiiDetector, pii_filter
from harness.rules.refunds import (
    refund_approval_gate,
    refund_claims_grounded,
    refund_tool_guards,
)
from harness.security import (
    DocumentShield,
    HeuristicShield,
    output_guard,
    prompt_guard,
    system_prompt_leak_guard,
)

# Tools que devuelven texto de terceros (documentos del RAG, descripciones de partners).
UNTRUSTED_TOOLS = frozenset({"search_knowledge", "get_event", "search_events"})


def guarded_executor(
    spec: AgentSpec,
    approvals: ApprovalStore,
    shield: ContentShieldPort | None = None,
    *,
    fail_open: bool = False,
) -> JournaledExecutor:
    """Las aprobaciones son obligatorias: sin almacén no hay ejecutor de producción."""
    documents = DocumentShield(shield or HeuristicShield(), UNTRUSTED_TOOLS, fail_open=fail_open)
    guards = [*refund_tool_guards(), refund_approval_gate(approvals)]
    inner = GuardedExecutor(spec.tools, guards, output_guards=[documents])
    return JournaledExecutor(spec.tools, inner=inner)


def guarded_agent(
    agent: AgentRuntime,
    *,
    system_prompt: str,
    shield: ContentShieldPort | None = None,
    pii: PiiDetector | None = None,
    fail_open: bool = False,
) -> GuardedRuntime:
    """`system_prompt` es obligatorio: sin él no hay guarda de fuga de instrucciones (12.4)."""
    shield = shield or HeuristicShield()
    return GuardedRuntime(
        agent,
        input_guards=[prompt_guard(shield, fail_open=fail_open)],
        output_guards=[
            refund_claims_grounded,
            system_prompt_leak_guard(system_prompt),
            output_guard(shield, fail_open=fail_open),
        ],
        # Entrada: el detector configurado (Presidio detecta nombres). Salida: solo patrones,
        # para no borrar por error títulos de eventos que un NER confunda con personas.
        input_filters=[pii_filter(pii or RegexPiiDetector())],
        output_filters=[pii_filter(RegexPiiDetector())],
    )


def build_pii_detector(settings: Settings) -> PiiDetector:
    if settings.guardrails.pii == "presidio":
        from harness.pii_presidio import PresidioPiiDetector

        return PresidioPiiDetector()
    if settings.guardrails.pii == "gcp":
        from google.cloud.dlp_v2 import DlpServiceClient

        from genaipocs_core.adapters.gcp_auth import gcp_credentials
        from harness.pii_gcp import SensitiveDataProtectionPiiDetector, location_parent

        gcp, sdp = settings.gcp, settings.gcp_sensitive_data
        if not gcp.project:
            raise ValueError("Falta GCP__PROJECT.")
        parent = location_parent(gcp.project, sdp.location or gcp.region)
        template = (
            f"{parent}/inspectTemplates/{sdp.inspect_template}" if sdp.inspect_template else ""
        )
        return SensitiveDataProtectionPiiDetector(
            DlpServiceClient(credentials=gcp_credentials(gcp)),
            parent=parent,
            min_likelihood=sdp.min_likelihood,
            inspect_template=template,
            timeout_seconds=sdp.timeout_seconds,
        )
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

        from genaipocs_core.adapters.azure_content_safety import (
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
    elif kind == "gcp":
        from google.cloud.modelarmor_v1 import ModelArmorAsyncClient

        from genaipocs_core.adapters.gcp_auth import gcp_credentials
        from genaipocs_core.adapters.model_armor import (
            ModelArmorShield,
            model_armor_options,
            template_name,
        )

        template = template_name(settings)
        armor = settings.gcp_model_armor
        location = armor.location or settings.gcp.region
        async with ModelArmorAsyncClient(
            credentials=gcp_credentials(settings.gcp),
            client_options=model_armor_options(location),
        ) as client:
            yield ModelArmorShield(client, template=template, timeout_seconds=armor.timeout_seconds)
    else:
        import boto3

        from genaipocs_core.adapters.bedrock_guardrails import BedrockGuardrailShield

        guardrail = settings.bedrock_guardrail
        if not guardrail.guardrail_id:
            raise ValueError("Falta BEDROCK_GUARDRAIL__GUARDRAIL_ID.")
        session = boto3.Session(profile_name=guardrail.profile, region_name=guardrail.region)
        yield BedrockGuardrailShield(
            session.client("bedrock-runtime"),
            guardrail_id=guardrail.guardrail_id,
            version=guardrail.version,
        )
