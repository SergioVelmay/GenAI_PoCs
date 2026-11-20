"""Fase 6.G1: el bucle de tools de la fase 6 con Gemini de punta a punta (sin red ni coste).

Lo que cubren los tests del adaptador por separado, aquí junto y en varios turnos: lecturas en
paralelo con la *thought signature* solo en la primera llamada, resultados agrupados en un turno,
escritura en serie con su propia firma, errores como datos y routing de escrituras al modelo
potente con los dos tiers de Gemini.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from google.genai import types
from pydantic import BaseModel, Field

from genaipocs_core.adapters.gemini import GeminiLLM
from genaipocs_core.application.llm import HeuristicClassifier, ModelTier, RoutingLLM
from genaipocs_core.application.tools import Tool, ToolContext, ToolLoop, ToolRegistry
from genaipocs_core.ports import ChatMessage, Role

STRONG, CHEAP = "gemini-4-hydrogen", "gemini-3.5-flash-lite"
READ_SIGNATURE = base64.b64encode(b"\x01razonamiento-lecturas\xff").decode()
WRITE_SIGNATURE = base64.b64encode(b"\x02razonamiento-escritura\xfe").decode()
ANA = ToolContext(customer_id="cus_1")


class ScriptedModels:
    """Doble de `Client.aio.models` que contesta por turnos y guarda cada petición."""

    def __init__(self, *turns: list[dict[str, Any]]) -> None:
        self._turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(
        self, *, model: str, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        # Copia profunda: el bucle reutiliza la conversación y el adaptador la reconstruye.
        self.calls.append({"model": model, "contents": [c.model_copy(deep=True) for c in contents]})
        parts = self._turns.pop(0)
        return types.GenerateContentResponse.model_validate(
            {
                "candidates": [{"content": {"role": "model", "parts": parts}}],
                "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10},
                "modelVersion": model,
            }
        )

    async def generate_content_stream(self, **_: Any) -> Any:
        raise AssertionError("El bucle de tools no usa streaming")


def _call(call_id: str, name: str, signature: str | None = None, **args: Any) -> dict[str, Any]:
    part: dict[str, Any] = {"functionCall": {"id": call_id, "name": name, "args": args}}
    if signature is not None:
        part["thoughtSignature"] = signature
    return part


class OrdersArgs(BaseModel):
    limit: int = Field(5, ge=1, le=20)


class EventsArgs(BaseModel):
    city: str


class RefundArgs(BaseModel):
    order_id: str = Field(pattern=r"^ord_")


def _registry(log: list[str]) -> ToolRegistry:
    async def orders(args: OrdersArgs, context: ToolContext) -> dict[str, object]:
        log.append(f"orders:{context.customer_id}")
        return {"orders": [{"order_id": "ord_1", "status": "paid"}]}

    async def events(args: EventsArgs, context: ToolContext) -> dict[str, object]:
        log.append(f"events:{args.city}")
        return {"events": [{"event_id": "evt_1", "city": args.city}]}

    async def refund(args: RefundArgs, context: ToolContext) -> dict[str, object]:
        log.append(f"refund:{args.order_id}")
        return {"approved": True, "order_id": args.order_id}

    return ToolRegistry(
        [
            Tool("list_my_orders", "Pedidos del cliente.", OrdersArgs, orders),
            Tool("search_events", "Busca eventos.", EventsArgs, events),
            Tool("request_refund", "Pide un reembolso.", RefundArgs, refund, read_only=False),
        ]
    )


def _routed(cheap: ScriptedModels, strong: ScriptedModels) -> RoutingLLM:
    return RoutingLLM(
        {
            ModelTier.CHEAP: GeminiLLM(cheap, CHEAP, thinking_level="low"),
            ModelTier.STRONG: GeminiLLM(strong, STRONG, thinking_level="high"),
        },
        HeuristicClassifier(),
    )


def _prompt(question: str) -> list[ChatMessage]:
    return [ChatMessage(Role.SYSTEM, "Agente de Genaipocs."), ChatMessage(Role.USER, question)]


async def test_refund_conversation_round_trips_every_signature() -> None:
    cheap = ScriptedModels()
    strong = ScriptedModels(
        [
            {"text": "Miro tus pedidos y el evento."},
            _call("c1", "list_my_orders", READ_SIGNATURE, limit=5),
            _call("c2", "search_events", city="Madrid"),
        ],
        [_call("c3", "request_refund", WRITE_SIGNATURE, order_id="ord_1")],
        [{"text": "Reembolso aprobado para ord_1."}],
    )
    log: list[str] = []

    result = await ToolLoop(_routed(cheap, strong), _registry(log)).run(
        _prompt("Quiero el reembolso de mi último pedido"), ANA
    )

    assert (result.stop_reason, result.answer) == ("answer", "Reembolso aprobado para ord_1.")
    # La identidad sale del contexto (nunca del modelo) y la escritura va después de las lecturas.
    assert sorted(log[:2]) == ["events:Madrid", "orders:cus_1"]
    assert log[2] == "refund:ord_1"
    # Ofrecer una tool con efectos manda cada turno al modelo potente (regla de la fase 6).
    assert cheap.calls == []
    assert [call["model"] for call in strong.calls] == [STRONG] * 3

    # Segunda petición: el turno del modelo vuelve con su firma solo en la primera llamada y
    # los dos resultados en paralelo van juntos, en orden, en un único turno `user`.
    _, model_turn, results = strong.calls[1]["contents"]
    text, first, second = model_turn.parts
    assert text.text == "Miro tus pedidos y el evento."
    assert first.thought_signature == base64.b64decode(READ_SIGNATURE)
    assert second.thought_signature is None
    assert [p.function_response.id for p in results.parts] == ["c1", "c2"]
    assert results.parts[0].function_response.response == {
        "output": {"orders": [{"order_id": "ord_1", "status": "paid"}]}
    }

    # Tercera petición: el historial completo conserva las dos firmas, cada una en su paso.
    contents = strong.calls[2]["contents"]
    assert [c.role for c in contents] == ["user", "model", "user", "model", "user"]
    assert contents[1].parts[1].thought_signature == base64.b64decode(READ_SIGNATURE)
    assert contents[3].parts[0].thought_signature == base64.b64decode(WRITE_SIGNATURE)
    refund = contents[4].parts[0].function_response
    assert (refund.name, refund.response) == (
        "request_refund",
        {"output": {"approved": True, "order_id": "ord_1"}},
    )


async def test_validation_errors_reach_gemini_as_error_results() -> None:
    """Un id deducido (`ses_` en lugar de `ord_`, el fallo real de la fase 6) no llega a la
    tool: vuelve a Gemini como `error` y el modelo se corrige en el turno siguiente."""
    strong = ScriptedModels(
        [_call("c1", "request_refund", WRITE_SIGNATURE, order_id="ses_0018361")],
        [_call("c2", "request_refund", READ_SIGNATURE, order_id="ord_1")],
        [{"text": "Hecho."}],
    )
    log: list[str] = []

    result = await ToolLoop(_routed(ScriptedModels(), strong), _registry(log)).run(
        _prompt("Reembolso de mi pedido"), ANA
    )

    assert [e.error for e in result.executions] == ["invalid_arguments", None]
    assert log == ["refund:ord_1"]
    error = strong.calls[1]["contents"][-1].parts[0].function_response.response
    assert set(error) == {"error"}
    assert error["error"]["error"]["type"] == "invalid_arguments"
    assert "order_id" in error["error"]["error"]["message"]


async def test_read_only_turns_stay_on_the_cheap_model() -> None:
    cheap = ScriptedModels(
        [_call("c1", "list_my_orders", READ_SIGNATURE, limit=3)],
        [{"text": "Tienes un pedido pagado."}],
    )
    strong = ScriptedModels()
    log: list[str] = []
    registry = ToolRegistry([t for t in _registry(log) if t.read_only])

    result = await ToolLoop(_routed(cheap, strong), registry).run(
        _prompt("¿Qué pedidos tengo?"), ANA
    )

    assert result.answer == "Tienes un pedido pagado."
    assert strong.calls == []
    assert [call["model"] for call in cheap.calls] == [CHEAP] * 2
    assert json.loads(result.executions[0].output) == {
        "orders": [{"order_id": "ord_1", "status": "paid"}]
    }
