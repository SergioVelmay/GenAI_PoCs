"""Proveedor de LLM simulado para pruebas de carga (fase 10.7). Coste 0, comportamiento realista.

Una prueba de carga contra el modelo real costaría dinero y, sobre todo, chocaría con la cuota
antes de medir nada más. Este adaptador reproduce lo que importa para la escala:
- **Latencia**: tiempo hasta el primer token + tokens a una velocidad dada (en streaming, trozo
  a trozo; sin streaming, todo al final).
- **Cuota**: TPM y RPM del "despliegue". Al agotarse devuelve `LLMRateLimitError` con
  `retry_after`, como el 429 de Azure OpenAI o el `ThrottlingException` de Bedrock. Con un
  `RateLimiterPort` en Redis la cuota es compartida por todas las réplicas, como la real.
- **Fallos**: 5xx aleatorios opcionales para ejercitar reintentos y circuit breaker.

Responde citando la primera fuente (`[1]`) para que la verificación de citas pase.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import Counter
from collections.abc import AsyncGenerator, Sequence
from typing import Self

from headache_core.adapters.memory import InMemoryRateLimiter
from headache_core.config import SimulatedLLMSettings
from headache_core.ports import (
    ChatMessage,
    LLMRateLimitError,
    LLMResponse,
    LLMStreamEvent,
    LLMTransientError,
    RateLimiterPort,
    RateLimiterUnavailableError,
    RateLimitRequest,
    RateLimitRule,
    ResponseSchema,
    TextDelta,
    TokenUsage,
    ToolConfig,
)

# Contadores del proveedor simulado en este proceso (el edge los enseña en /ready).
STATS: Counter[str] = Counter()

_PHRASE = (
    "Según la política de Headache, la entrada se gestiona desde la app y el equipo de "
    "atención al cliente puede ayudarte con cualquier duda sobre el evento y su horario"
)
_WORDS = _PHRASE.split()


def _estimate(messages: Sequence[ChatMessage]) -> int:
    return sum(len(m.content) for m in messages) // 4 + 1


class SimulatedLLM:
    def __init__(
        self,
        settings: SimulatedLLMSettings,
        *,
        quota: RateLimiterPort | None = None,
        key: str = "simulated",
        rng: random.Random | None = None,
    ) -> None:
        self._settings = settings
        self._quota = quota or InMemoryRateLimiter()
        self._key = key
        self._rng = rng or random.Random()
        self._tpm = RateLimitRule("provider_tpm", settings.tokens_per_minute, 60.0)
        self._rpm = RateLimitRule("provider_rpm", settings.requests_per_minute, 60.0)

    @classmethod
    def from_settings(
        cls, settings: SimulatedLLMSettings, *, quota: RateLimiterPort | None = None
    ) -> Self:
        return cls(settings, quota=quota)

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: ResponseSchema | None = None,
        tools: ToolConfig | None = None,
    ) -> LLMResponse:
        final: LLMResponse | None = None
        async for event in self.stream(messages, temperature=temperature, max_tokens=max_tokens):
            if isinstance(event, LLMResponse):
                final = event
        assert final is not None
        return final

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        started = time.perf_counter()
        input_tokens = _estimate(messages)
        output_tokens = min(
            max_tokens or self._settings.output_tokens, self._settings.output_tokens
        )
        await self._admit(input_tokens + output_tokens)
        STATS["calls"] += 1
        await asyncio.sleep(self._jitter(self._settings.ttft_ms / 1000))
        if self._rng.random() < self._settings.error_rate:
            STATS["errors"] += 1
            raise LLMTransientError("503 simulado: el proveedor no responde.")
        words = [self._rng.choice(_WORDS) for _ in range(max(1, output_tokens - 2))]
        parts = [f"{word} " for word in words] + ["[1]."]
        per_token = 1 / self._settings.tokens_per_second
        for part in parts:
            await asyncio.sleep(per_token)
            yield TextDelta(part)
        yield LLMResponse(
            content="".join(parts),
            model=self._settings.model,
            usage=TokenUsage(input_tokens, output_tokens),
            provider="simulated",
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason="stop",
        )

    async def _admit(self, tokens: int) -> None:
        try:
            decision = await self._quota.acquire(
                [
                    RateLimitRequest(f"provider:{self._key}:rpm", self._rpm),
                    RateLimitRequest(f"provider:{self._key}:tpm", self._tpm, tokens),
                ]
            )
        except RateLimiterUnavailableError:
            return  # Sin Redis, el simulador no limita (no es el sistema que se mide).
        if not decision.allowed:
            STATS["rate_limited"] += 1
            raise LLMRateLimitError(
                f"429 simulado ({decision.limited_by}).", retry_after=decision.retry_after
            )

    def _jitter(self, seconds: float) -> float:
        # Cola larga como la de un proveedor real: casi siempre cerca de la media, a veces 3x.
        return seconds * (3.0 if self._rng.random() < 0.05 else self._rng.uniform(0.7, 1.3))
