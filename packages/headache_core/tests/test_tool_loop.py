from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from headache_core.adapters.memory import FakeLLM
from headache_core.application.tools import (
    FALLBACK_ANSWER,
    Tool,
    ToolContext,
    ToolError,
    ToolLoop,
    ToolRegistry,
)
from headache_core.ports import ChatMessage, Role, ToolCall

CONTEXT = ToolContext(customer_id="cus_1")
QUESTION = [ChatMessage(Role.SYSTEM, "Eres un asistente."), ChatMessage(Role.USER, "Hola")]


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(description="Texto a devolver.")
    delay: float = 0.0


class Recorder:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.active = 0
        self.max_active = 0

    async def __call__(self, args: EchoArgs, context: ToolContext) -> dict[str, Any]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.log.append(f"start:{args.text}")
        await asyncio.sleep(args.delay)
        self.log.append(f"end:{args.text}")
        self.active -= 1
        return {"echo": args.text, "customer": context.customer_id}


def _call(call_id: str, name: str, **arguments: Any) -> ToolCall:
    return ToolCall(call_id, name, json.dumps(arguments))


def _registry(recorder: Recorder, **write_options: Any) -> ToolRegistry:
    return ToolRegistry(
        [
            Tool("read", "Lectura.", EchoArgs, recorder, timeout_s=0.5),
            Tool("write", "Escritura.", EchoArgs, recorder, read_only=False, **write_options),
        ]
    )


async def test_runs_tools_and_returns_final_answer() -> None:
    recorder = Recorder()
    llm = FakeLLM([[_call("c1", "read", text="hola")], "Listo: hola"])

    result = await ToolLoop(llm, _registry(recorder)).run(QUESTION, CONTEXT)

    assert result.answer == "Listo: hola"
    assert result.stop_reason == "answer"
    assert len(result.responses) == 2
    # El segundo turno ve la petición del modelo y el resultado saneado de la tool.
    assistant, tool_result = llm.calls[1][-2:]
    assert assistant.tool_calls == (_call("c1", "read", text="hola"),)
    assert tool_result.role is Role.TOOL and tool_result.tool_call_id == "c1"
    assert json.loads(tool_result.content) == {"echo": "hola", "customer": "cus_1"}
    assert llm.tools[0] is not None
    assert [t.name for t in llm.tools[0].tools] == ["read", "write"]


async def test_reads_run_in_parallel_and_writes_in_order_after_them() -> None:
    recorder = Recorder()
    calls = [
        _call("w1", "write", text="w1"),
        _call("r1", "read", text="r1", delay=0.05),
        _call("r2", "read", text="r2", delay=0.05),
        _call("w2", "write", text="w2"),
    ]
    loop = ToolLoop(FakeLLM(), _registry(recorder))

    executions = await loop.execute_turn(calls, CONTEXT)

    assert [e.call.id for e in executions] == ["w1", "r1", "r2", "w2"]
    assert recorder.log[:2] == ["start:r1", "start:r2"]
    assert recorder.log[-4:] == ["start:w1", "end:w1", "start:w2", "end:w2"]
    assert not any(e.is_error for e in executions)


async def test_parallelism_is_bounded() -> None:
    recorder = Recorder()
    calls = [_call(f"r{i}", "read", text=str(i), delay=0.02) for i in range(6)]

    await ToolLoop(FakeLLM(), _registry(recorder), max_parallel=2).execute_turn(calls, CONTEXT)

    assert recorder.max_active == 2


@pytest.mark.parametrize(
    ("call", "kind"),
    [
        (_call("c1", "delete_everything"), "unknown_tool"),
        (ToolCall("c1", "read", "{not json"), "invalid_arguments"),
        (_call("c1", "read", text="x", extra="y"), "invalid_arguments"),
        (_call("c1", "read"), "invalid_arguments"),
        (_call("c1", "read", text="lento", delay=2), "timeout"),
    ],
)
async def test_tool_failures_are_returned_to_the_model(call: ToolCall, kind: str) -> None:
    loop = ToolLoop(FakeLLM(), _registry(Recorder()))

    [execution] = await loop.execute_turn([call], CONTEXT)

    assert execution.error == kind
    assert json.loads(execution.output)["error"]["type"] == kind


async def test_write_timeout_is_reported_as_uncertain_and_not_retryable() -> None:
    loop = ToolLoop(FakeLLM(), _registry(Recorder(), timeout_s=0.01))

    [execution] = await loop.execute_turn([_call("w", "write", text="x", delay=1)], CONTEXT)

    error = json.loads(execution.output)["error"]
    assert error["type"] == "timeout"
    assert error["retryable"] is False
    assert "incierto" in error["message"]


async def test_expected_and_unexpected_errors_are_separated() -> None:
    async def business(args: EchoArgs, _: ToolContext) -> None:
        raise ToolError("Fuera de plazo.", kind="rejected")

    async def crash(args: EchoArgs, _: ToolContext) -> None:
        raise RuntimeError("password=hunter2 en postgres://...")

    registry = ToolRegistry(
        [Tool("business", "b", EchoArgs, business), Tool("crash", "c", EchoArgs, crash)]
    )
    loop = ToolLoop(FakeLLM(), registry)

    rejected, internal = await loop.execute_turn(
        [_call("a", "business", text="x"), _call("b", "crash", text="x")], CONTEXT
    )

    assert json.loads(rejected.output)["error"]["message"] == "Fuera de plazo."
    assert internal.error == "internal_error"
    assert "hunter2" not in internal.output


async def test_too_many_calls_in_one_turn_are_rejected() -> None:
    calls = [_call(f"r{i}", "read", text=str(i)) for i in range(4)]
    loop = ToolLoop(FakeLLM(), _registry(Recorder()), max_calls_per_turn=3)

    executions = await loop.execute_turn(calls, CONTEXT)

    assert [e.error for e in executions] == [None, None, None, "too_many_calls"]


async def test_stops_after_max_iterations_with_fallback() -> None:
    llm = FakeLLM([[_call(f"c{i}", "read", text="otra vez")] for i in range(3)])

    result = await ToolLoop(llm, _registry(Recorder()), max_iterations=3).run(QUESTION, CONTEXT)

    assert result.stop_reason == "max_iterations"
    assert result.answer == FALLBACK_ANSWER
    assert len(result.executions) == 3


def test_tool_spec_is_strict_json_schema() -> None:
    spec = Tool("read", "Lectura.", EchoArgs, Recorder()).spec()

    assert spec.json_schema["additionalProperties"] is False
    assert spec.json_schema["required"] == ["text", "delay"]
    assert "default" not in spec.json_schema["properties"]["delay"]


def test_rejects_invalid_tool_names() -> None:
    with pytest.raises(ValueError):
        Tool("borrar pedido", "x", EchoArgs, Recorder())
