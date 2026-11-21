"""Instantánea JSON de un hilo: la comparten las memorias gestionadas (AgentCore Memory, fase 7;
Sessions de Agent Runtime, 7.G3).

El resumen y la compactación son nuestros (`ShortTermMemory`), así que el servicio no puede
reconstruir el hilo a partir de eventos conversacionales sueltos: cada guardado es el hilo
entero. Las *thought signatures* de Gemini viajan dentro de cada `ToolCall`.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from genaipocs_core.ports.llm import ChatMessage, Role, ToolCall
from genaipocs_core.ports.memory import ThreadState


def thread_document(state: ThreadState) -> dict[str, Any]:
    return {
        "thread_id": state.thread_id,
        "customer_id": state.customer_id,
        "summary": state.summary,
        "messages": [
            {
                "role": m.role.value,
                "content": m.content,
                "tool_calls": [asdict(c) for c in m.tool_calls],
                "tool_call_id": m.tool_call_id,
                "is_error": m.is_error,
            }
            for m in state.messages
        ],
    }


def thread_from_document(document: dict[str, Any], version: int) -> ThreadState:
    messages = tuple(
        ChatMessage(
            Role(m["role"]),
            m["content"],
            tool_calls=tuple(ToolCall(**c) for c in m["tool_calls"]),
            tool_call_id=m["tool_call_id"],
            is_error=m["is_error"],
        )
        for m in document["messages"]
    )
    return ThreadState(
        thread_id=document["thread_id"],
        customer_id=document["customer_id"],
        messages=messages,
        summary=document["summary"],
        version=version,
    )
