"""Convenciones semánticas de GenAI de OpenTelemetry: nombres de atributos y de spans.

Las convenciones viven en el repositorio `open-telemetry/semantic-conventions-genai` y siguen
en estado *Development*: los nombres cambian entre versiones (`gen_ai.system` pasó a
`gen_ai.provider.name`). Por eso están aquí, en un solo sitio, y nadie escribe la cadena a mano.

Además de las de OTel hay dos familias propias:

- `langfuse.*`: Langfuse las lee para su modelo (tipo de observación, sesión, usuario). El
  resto de backends las guardan como atributos sin más.
- `headache.*`: lo que es nuestro (nivel del modelo, plan del usuario, resultado de la caché).
"""

from __future__ import annotations

from enum import StrEnum

from headache_core.ports.llm import TokenUsage
from headache_core.ports.telemetry import AttributeValue


class GenAIOperation(StrEnum):
    CHAT = "chat"
    EMBEDDINGS = "embeddings"
    RETRIEVAL = "retrieval"
    EXECUTE_TOOL = "execute_tool"
    INVOKE_AGENT = "invoke_agent"
    INVOKE_WORKFLOW = "invoke_workflow"


class ObservationType(StrEnum):
    """Tipos de observación de Langfuse (`langfuse.observation.type`)."""

    SPAN = "span"
    GENERATION = "generation"
    EMBEDDING = "embedding"
    AGENT = "agent"
    TOOL = "tool"
    CHAIN = "chain"
    RETRIEVER = "retriever"
    GUARDRAIL = "guardrail"
    EVALUATOR = "evaluator"


OPERATION_NAME = "gen_ai.operation.name"
PROVIDER_NAME = "gen_ai.provider.name"
REQUEST_MODEL = "gen_ai.request.model"
REQUEST_TEMPERATURE = "gen_ai.request.temperature"
REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
RESPONSE_MODEL = "gen_ai.response.model"
RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
USAGE_CACHE_READ_INPUT_TOKENS = "gen_ai.usage.cache_read.input_tokens"
USAGE_CACHE_WRITE_INPUT_TOKENS = "gen_ai.usage.cache_write.input_tokens"
# No es de OTel: es la convención que leen Langfuse y otros backends de LLM para el coste.
USAGE_COST = "gen_ai.usage.cost"
INPUT_MESSAGES = "gen_ai.input.messages"
OUTPUT_MESSAGES = "gen_ai.output.messages"
TOOL_NAME = "gen_ai.tool.name"
TOOL_TYPE = "gen_ai.tool.type"
TOOL_CALL_ID = "gen_ai.tool.call.id"
TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
TOOL_CALL_RESULT = "gen_ai.tool.call.result"
AGENT_NAME = "gen_ai.agent.name"
AGENT_ID = "gen_ai.agent.id"
CONVERSATION_ID = "gen_ai.conversation.id"
WORKFLOW_NAME = "gen_ai.workflow.name"
DATA_SOURCE_ID = "gen_ai.data_source.id"
RETRIEVAL_TOP_K = "gen_ai.retrieval.top_k"
ERROR_TYPE = "error.type"

LANGFUSE_OBSERVATION_TYPE = "langfuse.observation.type"
LANGFUSE_SESSION_ID = "langfuse.session.id"
LANGFUSE_USER_ID = "langfuse.user.id"
# Langfuse aún no lee `gen_ai.input/output.messages`: con captura de contenido van en las dos.
LANGFUSE_INPUT = "langfuse.observation.input"
LANGFUSE_OUTPUT = "langfuse.observation.output"
# Inicio de la respuesta en streaming (ISO 8601): Langfuse calcula el TTFT con él.
LANGFUSE_COMPLETION_START_TIME = "langfuse.observation.completion_start_time"
EVENT_FIRST_TOKEN = "gen_ai.first_token"

HEADACHE_DEPLOYMENT = "headache.llm.deployment"
HEADACHE_ENGINE = "headache.llm.engine"
HEADACHE_TIER = "headache.llm.tier"
HEADACHE_STREAM = "headache.llm.stream"
HEADACHE_RESPONSE_SCHEMA = "headache.llm.response_schema"
HEADACHE_TOOLS_OFFERED = "headache.llm.tools_offered"
HEADACHE_TOOL_READ_ONLY = "headache.tool.read_only"
HEADACHE_TOOL_OUTCOME = "headache.tool.outcome"
HEADACHE_AGENT_FLAVOR = "headache.agent.flavor"
HEADACHE_STOP_REASON = "headache.agent.stop_reason"
HEADACHE_GUARDRAILS = "headache.agent.guardrails"
HEADACHE_APPROVALS = "headache.agent.approvals"
HEADACHE_COMPACTED = "headache.agent.compacted"
HEADACHE_RETRIEVAL_STRATEGY = "headache.retrieval.strategy"
HEADACHE_RETRIEVAL_QUERIES = "headache.retrieval.queries"
HEADACHE_RETRIEVAL_RESULTS = "headache.retrieval.results"
HEADACHE_SEARCH_MODE = "headache.retrieval.mode"
HEADACHE_PLAN = "headache.caller.plan"
HEADACHE_ADMISSION = "headache.admission.outcome"
HEADACHE_IN_FLIGHT = "headache.admission.in_flight"
HEADACHE_CACHE = "headache.cache.outcome"
HEADACHE_CACHE_REASON = "headache.cache.reason"
HEADACHE_CACHE_SIMILARITY = "headache.cache.similarity"
HEADACHE_ANSWER_MODE = "headache.answer.mode"
HEADACHE_DEGRADED = "headache.answer.degraded"
HEADACHE_ABSTAINED = "headache.answer.abstained"
EVENT_DEGRADED = "headache.degraded"
EVENT_FIRST_CHUNK = "headache.first_chunk"

# Nuestro identificador de proveedor -> valor bien conocido de `gen_ai.provider.name`.
_PROVIDERS = {
    "azure": "azure.ai.openai",
    "aws": "aws.bedrock",
    "litellm": "litellm",
}


def provider_name(provider: str) -> str:
    """Nombre estándar del proveedor; los nuestros (simulado, fake) llevan prefijo propio."""
    return _PROVIDERS.get(provider, f"headache.{provider}")


def span_name(operation: GenAIOperation, target: str | None) -> str:
    """`{operación} {objetivo}` (p. ej. `chat gpt-4.1-mini`, `execute_tool get_order`).

    El objetivo tiene que ser de cardinalidad baja (un modelo o una tool, nunca un ID de caso).
    """
    return f"{operation} {target}" if target else str(operation)


def usage_attributes(usage: TokenUsage) -> dict[str, AttributeValue]:
    """Tokens con la semántica de OTel: `input_tokens` incluye los leídos y escritos en caché."""
    return {
        USAGE_INPUT_TOKENS: usage.input_tokens,
        USAGE_OUTPUT_TOKENS: usage.output_tokens,
        USAGE_CACHE_READ_INPUT_TOKENS: usage.cached_input_tokens,
        USAGE_CACHE_WRITE_INPUT_TOKENS: usage.cache_write_tokens,
    }
