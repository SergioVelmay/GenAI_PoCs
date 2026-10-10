from __future__ import annotations

import io
import json
from typing import Any

import boto3
import httpx
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber
from openai import AsyncOpenAI

from booking_agent.flavors import create_agent
from booking_agent.flavors.foundry import FoundryAgent
from booking_agent.managed.agentcore import (
    SESSION_HEADER,
    USER_HEADER,
    BotoRuntimeInvoker,
    HttpRuntimeInvoker,
    create_runtime_app,
    runtime_session_id,
)
from booking_agent.managed.foundry import (
    FoundryAgentService,
    LocalAgentService,
    agent_definition,
)
from booking_agent.runtime import AgentDeps, JournaledExecutor
from booking_agent.spec import build_booking_spec
from headache_core.adapters.local_retrieval import LocalRetrieval
from headache_core.adapters.memory import (
    FakeLLM,
    HashingEmbedding,
    InMemoryCheckpointStore,
    InMemoryPreferenceStore,
    InMemoryUnitOfWork,
)
from headache_core.adapters.ticketing_local import UnitOfWorkTicketing
from headache_core.application.retrieval import RetrievalService
from headache_core.application.tools import ToolContext, build_ticketing_tools
from headache_core.ports import Role, ToolCall
from headache_core.ports.memory import ThreadAccessError

ANA = ToolContext(customer_id="cus_1")
BOB = ToolContext(customer_id="cus_2")


def _deps(llm: FakeLLM, preferences: InMemoryPreferenceStore | None = None) -> AgentDeps:
    preferences = preferences or InMemoryPreferenceStore()
    uow = InMemoryUnitOfWork()
    spec = build_booking_spec(
        ticketing_tools=build_ticketing_tools(UnitOfWorkTicketing(lambda: uow)),
        retrieval=RetrievalService(LocalRetrieval([], embeddings=HashingEmbedding(64))),
        preferences=preferences,
    )
    return AgentDeps(
        spec, llm, JournaledExecutor(spec.tools), InMemoryCheckpointStore(), preferences
    )


def _foundry(deps: AgentDeps) -> tuple[FoundryAgent, LocalAgentService]:
    definition = agent_definition(deps.spec.system_prompt, deps.spec.tools, model="m")
    service = LocalAgentService(deps.llm, definition)
    return FoundryAgent(deps, service), service


def test_agent_definition_publishes_the_same_tool_schemas() -> None:
    deps = _deps(FakeLLM())

    definition = agent_definition(deps.spec.system_prompt, deps.spec.tools, model="gpt-4.1-mini")

    assert definition["kind"] == "prompt" and definition["model"] == "gpt-4.1-mini"
    assert [t["name"] for t in definition["tools"]] == deps.spec.tools.names
    get_order = next(t for t in definition["tools"] if t["name"] == "get_order")
    assert get_order["parameters"] == dict(deps.spec.tools.get("get_order").spec().json_schema)  # type: ignore[union-attr]


async def test_foundry_keeps_history_in_the_service_and_owns_threads() -> None:
    llm = FakeLLM([[ToolCall("c1", "list_my_orders", '{"limit": 1}')], "Sin pedidos.", "Hola."])
    preferences = InMemoryPreferenceStore({"cus_1": {"language": "es"}})
    deps = _deps(llm, preferences)
    agent, service = _foundry(deps)

    first = await agent.respond("t1", "mis pedidos", ANA)
    second = await agent.respond("t1", "gracias", ANA)

    assert (first.answer, second.answer) == ("Sin pedidos.", "Hola.")
    assert deps.executor.journal[0].call.name == "list_my_orders"
    # Turno 2: el servicio reenvía TODO el historial (no hay compactación del cliente).
    prompt = llm.calls[2]
    assert prompt[0].content == deps.spec.system_prompt
    assert [m.content for m in prompt if m.role is Role.USER] == ["mis pedidos", "gracias"]
    assert sum("language: es" in m.content for m in prompt if m.role is Role.SYSTEM) == 2
    [conversation] = service.conversations.values()
    assert [i["type"] for i in conversation].count("function_call_output") == 1
    with pytest.raises(ThreadAccessError):
        await agent.respond("t1", "me cuelo", BOB)


async def test_foundry_max_iterations_closes_the_conversation_without_another_call() -> None:
    loop = [[ToolCall(f"c{i}", "list_my_orders", '{"limit": 1}')] for i in range(10)]
    llm = FakeLLM(loop)
    deps = _deps(llm)
    agent, service = _foundry(deps)

    turn = await agent.respond("t1", "pedidos", ANA)

    assert turn.stop_reason == "max_iterations" and turn.answer == deps.spec.fallback_answer
    assert len(llm.calls) == deps.spec.max_iterations == len(deps.executor.journal)
    [conversation] = service.conversations.values()
    assert conversation[-2]["type"] == "function_call_output"
    assert conversation[-1] == {
        "type": "message",
        "role": "assistant",
        "content": deps.spec.fallback_answer,
    }


async def test_foundry_service_speaks_the_responses_api() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/conversations"):
            return httpx.Response(200, json={"id": "conv_1", "object": "conversation"})
        call = {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "c1",
            "name": "get_order",
            "arguments": '{"order_id": "o1"}',
            "status": "completed",
        }
        return httpx.Response(
            200,
            json={"id": "resp_1", "object": "response", "status": "completed", "output": [call]},
        )

    client = AsyncOpenAI(
        api_key="test",
        base_url="https://proyecto.local/openai/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    service = FoundryAgentService(client, "headache-booking-agent")

    conversation = await service.create_conversation()
    reply = await service.respond(
        conversation, [{"type": "message", "role": "user", "content": "x"}]
    )

    assert conversation == "conv_1"
    assert reply.calls == (ToolCall("c1", "get_order", '{"order_id": "o1"}'),)
    body = json.loads(requests[1].content)
    assert body["conversation"] == "conv_1"
    assert body["agent_reference"] == {"name": "headache-booking-agent", "type": "agent_reference"}


async def test_agentcore_runtime_app_follows_the_http_contract() -> None:
    deps = _deps(FakeLLM(["hola", "otra"]))
    app = create_runtime_app(create_agent("native", deps))
    session = runtime_session_id("cus_1", "t1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime"
    ) as http:
        assert (await http.get("/ping")).json() == {"status": "Healthy"}
        # Sin el usuario que pone AgentCore no hay identidad: se rechaza.
        missing = await http.post(
            "/invocations", json={"prompt": "x"}, headers={SESSION_HEADER: session}
        )
        assert missing.status_code == 422
        invoker = HttpRuntimeInvoker(http)
        result = await invoker.invoke(session_id=session, user_id="cus_1", prompt="hola")
        assert (result.response, result.stop_reason) == ("hola", "answer")
        # La misma sesión con otro usuario: el sabor del contenedor detecta el dueño ajeno.
        with pytest.raises(ThreadAccessError):
            await invoker.invoke(session_id=session, user_id="cus_2", prompt="me cuelo")
        bad = await http.post(
            "/invocations",
            json={"prompt": "x"},
            headers={SESSION_HEADER: "corta", USER_HEADER: "cus_1"},
        )
        assert bad.status_code == 422


async def test_agentcore_sessions_are_isolated_per_customer() -> None:
    deps = _deps(FakeLLM(["para Ana", "para Bob"]))
    agent = create_agent("agentcore", deps)

    await agent.respond("t1", "hola", ANA)
    await agent.respond("t1", "hola", BOB)

    assert runtime_session_id("cus_1", "t1") != runtime_session_id("cus_2", "t1")
    assert len(runtime_session_id("cus_1", "t1")) >= 33
    # Bob no ve el turno de Ana aunque use el mismo identificador de hilo.
    assert [m.content for m in deps.llm.calls[1] if m.role is Role.USER] == ["hola"]  # type: ignore[attr-defined]


async def test_boto_invoker_calls_invoke_agent_runtime() -> None:
    client = boto3.client(
        "bedrock-agentcore",
        region_name="eu-west-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    arn = "arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/headache_agent-a1b2c3d4e5"
    session = runtime_session_id("cus_1", "t1")
    payload = json.dumps({"response": "hola", "stop_reason": "answer", "compacted": False}).encode()
    response: dict[str, Any] = {
        "runtimeSessionId": session,
        "contentType": "application/json",
        "statusCode": 200,
        "response": StreamingBody(io.BytesIO(payload), len(payload)),
    }
    with Stubber(client) as stubber:
        stubber.add_response(
            "invoke_agent_runtime",
            response,
            {
                "agentRuntimeArn": arn,
                "runtimeSessionId": session,
                "runtimeUserId": "cus_1",
                "contentType": "application/json",
                "accept": "application/json",
                "payload": b'{"prompt": "hola"}',
            },
        )
        result = await BotoRuntimeInvoker(client, arn).invoke(
            session_id=session, user_id="cus_1", prompt="hola"
        )

    assert result.response == "hola" and result.status == "success"
