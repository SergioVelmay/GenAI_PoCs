"""Puntuaciones de la evaluación online en Langfuse (fase 11.6).

La traza llega a Langfuse por OTLP (collector); la puntuación, por su API, enganchada al mismo
`trace_id`. Así en Langfuse se filtran las respuestas con faithfulness baja y se abre su traza.
"""

from __future__ import annotations

import asyncio
from typing import Self

from langfuse import Langfuse

from headache_core.application.online_eval import EvalSample
from headache_core.config import LangfuseSettings


def trace_id_of(carrier: dict[str, str] | None) -> str | None:
    """`00-<trace_id>-<span_id>-<flags>` → `<trace_id>` (W3C traceparent)."""
    parts = (carrier or {}).get("traceparent", "").split("-")
    return parts[1] if len(parts) == 4 and len(parts[1]) == 32 else None


class LangfuseScoreSink:
    def __init__(self, client: Langfuse) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: LangfuseSettings) -> Self:
        return cls(
            Langfuse(
                public_key=settings.public_key,
                secret_key=settings.secret_key.get_secret_value(),
                host=settings.host,
            )
        )

    async def record(self, sample: EvalSample, *, name: str, score: float) -> None:
        trace_id = trace_id_of(dict(sample.trace))
        if trace_id is None:
            return
        # El SDK es síncrono (encola y envía en segundo plano): fuera del event loop.
        await asyncio.to_thread(
            self._client.create_score,
            name=name,
            value=score,
            trace_id=trace_id,
            data_type="NUMERIC",
            comment="LLM-juez online (fase 11.6)",
        )
