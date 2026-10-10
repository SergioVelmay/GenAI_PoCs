from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from llama_index.core.base.llms.types import ChatMessage as LIChatMessage
from llama_index.core.base.llms.types import MessageRole
from llama_index.core.tools import FunctionTool

from booking_agent.bridges.langchain import (
    GatewayChatModel,
    spec_to_openai,
    to_chat_messages,
    with_cache_checkpoint,
)
from booking_agent.bridges.llamaindex import (
    RAW_ARGUMENTS,
    GatewayFunctionLLM,
    to_llama_messages,
)
from booking_agent.bridges.llamaindex import to_chat_messages as from_llama
from booking_agent.bridges.maf import GatewayChatClient, to_maf_messages
from booking_agent.bridges.maf import to_chat_messages as from_maf
from booking_agent.bridges.strands import (
    GatewayModel,
    system_blocks,
    to_strands_messages,
    tool_spec,
)
from booking_agent.bridges.strands import to_chat_messages as from_strands
from headache_core.adapters.memory import FakeLLM
from headache_core.ports import ChatMessage, Role, ToolCall
from headache_core.ports.llm import ToolSpec

SPEC = ToolSpec(
    "get_order",
    "Detalle de un pedido.",
    {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
        "additionalProperties": False,
    },
)


async def test_langchain_bridge_sends_the_exact_tool_specs_and_messages() -> None:
    llm = FakeLLM(
        [[ToolCall("c1", "get_order", '{"order_id": "ord_1"}'), ToolCall("c2", "x", "{")]]
    )
    model = GatewayChatModel(gateway=llm).bind_tools([spec_to_openai(SPEC)])
    history = [
        SystemMessage("sistema"),
        with_cache_checkpoint(HumanMessage("hola")),
        AIMessage("", tool_calls=[{"name": "get_order", "args": {}, "id": "c0"}]),
        ToolMessage('{"error": "x"}', tool_call_id="c0", status="error"),
    ]

    reply = await model.ainvoke(history)

    assert llm.tools[0] is not None and llm.tools[0].tools == (SPEC,)
    sent = llm.calls[0]
    assert [m.role for m in sent] == [Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.TOOL]
    assert sent[1].cache_checkpoint and not sent[0].cache_checkpoint
    assert sent[3].tool_call_id == "c0" and sent[3].is_error
    assert isinstance(reply, AIMessage)
    assert reply.tool_calls[0]["args"] == {"order_id": "ord_1"}
    # El JSON roto no se pierde: vuelve como llamada inválida con los argumentos crudos.
    assert reply.invalid_tool_calls[0]["args"] == "{"
    assert [c.arguments for c in to_chat_messages([reply])[0].tool_calls][1] == "{"
    assert reply.usage_metadata is not None


def test_llamaindex_messages_round_trip() -> None:
    messages = [
        ChatMessage(Role.SYSTEM, "sistema", cache_checkpoint=True),
        ChatMessage(Role.ASSISTANT, "", tool_calls=(ToolCall("c1", "get_order", "{}"),)),
        ChatMessage(Role.TOOL, "{}", tool_call_id="c1", is_error=True),
    ]

    assert from_llama(to_llama_messages(messages)) == messages


async def test_llamaindex_bridge_uses_spec_schemas_and_keeps_raw_arguments() -> None:
    llm = FakeLLM([[ToolCall("c1", "get_order", "[1]")]])
    bridge = GatewayFunctionLLM(llm, [SPEC])
    # El esquema que infiere LlamaIndex se ignora: viaja el ToolSpec del agente.
    tool = FunctionTool.from_defaults(fn=lambda: "", name="get_order", description="otra")
    user_msg = LIChatMessage(role=MessageRole.USER, content="hola")

    response = await bridge.achat(
        **bridge._prepare_chat_with_tools([tool], chat_history=[user_msg])
    )
    [selection] = bridge.get_tool_calls_from_response(response)

    assert llm.tools[0] is not None and llm.tools[0].tools == (SPEC,)
    assert selection.tool_id == "c1"
    assert selection.tool_kwargs == {RAW_ARGUMENTS: "[1]"}
    assert bridge.metadata.is_function_calling_model


HISTORY = [
    ChatMessage(Role.USER, "hola"),
    ChatMessage(
        Role.ASSISTANT,
        "",
        tool_calls=(ToolCall("c1", "get_order", '{"order_id": "o1"}'), ToolCall("c2", "x", "{")),
    ),
    ChatMessage(Role.TOOL, '{"id": "o1"}', tool_call_id="c1"),
    ChatMessage(Role.TOOL, '{"error": "x"}', tool_call_id="c2", is_error=True),
    ChatMessage(Role.ASSISTANT, "listo", cache_checkpoint=True),
]


def test_maf_messages_round_trip_and_keep_raw_arguments() -> None:
    assert from_maf(to_maf_messages(HISTORY)) == HISTORY


async def test_maf_client_sends_instructions_first_and_the_exact_tool_specs() -> None:
    llm = FakeLLM(["hola"])
    client = GatewayChatClient(llm, [SPEC])
    tool = SimpleNamespace(name="get_order")

    response = await client._inner_get_response(
        messages=to_maf_messages(HISTORY[:1]),
        stream=False,
        options={"instructions": "sistema", "tools": [tool], "tool_choice": "auto"},
    )

    assert response.text == "hola"
    sent = llm.calls[0]
    assert (sent[0].role, sent[0].content, sent[0].cache_checkpoint) == (
        Role.SYSTEM,
        "sistema",
        True,
    )
    assert llm.tools[0] is not None and llm.tools[0].tools == (SPEC,)


def test_strands_messages_group_tool_results_and_mark_cache_points() -> None:
    converted = to_strands_messages(HISTORY)

    assert [m["role"] for m in converted] == ["user", "assistant", "user", "assistant"]
    assert len(converted[2]["content"]) == 2
    assert converted[-1]["content"][-1] == {"cachePoint": {"type": "default"}}
    assert from_strands(converted) == HISTORY


async def test_strands_model_streams_the_gateway_response() -> None:
    llm = FakeLLM([[ToolCall("c1", "get_order", "{roto")]])
    model = GatewayModel(llm, [SPEC])
    prefix = [ChatMessage(Role.SYSTEM, "sistema", cache_checkpoint=True)]

    events = [
        e
        async for e in model.stream(
            to_strands_messages(HISTORY[:1]),
            [tool_spec(SPEC)],
            system_prompt_content=system_blocks(prefix),
        )
    ]

    assert llm.calls[0][0] == prefix[0]
    assert llm.tools[0] is not None and llm.tools[0].tools == (SPEC,)
    deltas = [e["contentBlockDelta"]["delta"] for e in events if "contentBlockDelta" in e]
    assert deltas == [{"toolUse": {"input": '{"__raw_arguments__": "{roto"}'}}]
    assert events[-2] == {"messageStop": {"stopReason": "tool_use"}}
