"""Caché semántica delante del asistente (fase 10.3), con fusión de peticiones idénticas.

- Acierto: se devuelve la respuesta guardada sin llamar al RAG ni al modelo.
- Fallo: se responde y, si la respuesta es reutilizable (con citas y verificada), se guarda.
- *Single-flight*: en un pico, mil personas hacen la misma pregunta a la vez y la caché aún
  está vacía; sin fusión, las mil fallan y pagan mil llamadas (*cache stampede*). Aquí la
  primera calcula y las demás (misma pregunta normalizada y mismo espacio) esperan su
  resultado. Es por réplica: entre réplicas haría falta un candado en Redis.
- Lo personal ni se busca, ni se guarda, ni se fusiona: dos clientes con la misma pregunta
  sobre "mi pedido" esperan respuestas distintas.

En streaming (10.4) la que calcula ve los trozos según llegan; las que esperaban y los
aciertos reciben la respuesta completa contada en trozos (`replay`).
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import asdict, replace

from edge_api.assistant import Answer, Assistant, AssistantEvent, replay
from headache_core.application.semantic_cache import CacheLookup, SemanticCache
from headache_core.application.telemetry import genai, get_telemetry
from headache_core.domain.admission import Caller, access_scope
from headache_core.domain.semantic_cache import (
    CacheScope,
    detect_language,
    is_cacheable_answer,
    normalize,
)
from headache_core.ports import LLMTransientError


def to_payload(answer: Answer) -> str:
    return json.dumps({**asdict(answer), "cache": "miss"}, ensure_ascii=False)


def from_payload(payload: str) -> Answer:
    data = json.loads(payload)
    return Answer(
        text=data["text"],
        citations=tuple(data["citations"]),
        abstained=data["abstained"],
        mode=data["mode"],
        cache="hit",
    )


class CachedAssistant:
    def __init__(
        self, inner: Assistant, cache: SemanticCache, *, index_version: str, mode: str
    ) -> None:
        self._inner = inner
        self.cache = cache
        self._index_version = index_version
        self._mode = mode
        self._inflight: dict[str, asyncio.Future[Answer]] = {}
        self.outcomes: Counter[str] = Counter()

    @property
    def inner(self) -> Assistant:
        return self._inner

    def scope(self, question: str, caller: Caller) -> CacheScope:
        return CacheScope(
            access_scope(caller.plan).allowed_acls,
            detect_language(question),
            self._index_version,
            self._mode,
        )

    async def answer(self, question: str, caller: Caller) -> Answer:
        lookup = await self.cache.lookup(question, self.scope(question, caller))
        if lookup.hit and lookup.payload is not None:
            self._count("hit")
            return from_payload(lookup.payload)
        if lookup.reason == "personal":
            self._count("bypass")
            return replace(await self._inner.answer(question, caller), cache="bypass")
        key = self._key(lookup, question)
        if key in self._inflight:
            return await self._follow(key)
        future = self._lead(key)
        try:
            answer = await self._inner.answer(question, caller)
        except BaseException as exc:
            self._abort(future, exc)
            raise
        finally:
            self._inflight.pop(key, None)
        await self._finish(future, lookup, answer)
        return answer

    async def stream(self, question: str, caller: Caller) -> AsyncIterator[AssistantEvent]:
        lookup = await self.cache.lookup(question, self.scope(question, caller))
        if lookup.hit and lookup.payload is not None:
            self._count("hit")
            async for event in replay(from_payload(lookup.payload)):
                yield event
            return
        if lookup.reason == "personal":
            self._count("bypass")
            async for event in self._inner.stream(question, caller):
                yield replace(event, cache="bypass") if isinstance(event, Answer) else event
            return
        key = self._key(lookup, question)
        if key in self._inflight:
            async for event in replay(await self._follow(key)):
                yield event
            return
        future = self._lead(key)
        try:
            async for event in self._inner.stream(question, caller):
                if isinstance(event, Answer):
                    # Antes de entregarla: si el cliente corta justo después, ya está guardada.
                    await self._finish(future, lookup, event)
                yield event
        except BaseException as exc:
            self._abort(future, exc)
            raise
        finally:
            self._inflight.pop(key, None)
        if not future.done():
            self._abort(future, RuntimeError("El stream terminó sin respuesta final."))

    def _count(self, outcome: str) -> None:
        self.outcomes[outcome] += 1
        # En el span de la petición: la tasa de aciertos se puede cortar por plan o por ruta.
        get_telemetry().current_span().set_attribute(genai.HEADACHE_CACHE, outcome)

    @staticmethod
    def _key(lookup: CacheLookup, question: str) -> str:
        return f"{lookup.scope.key()}|{normalize(question)}"

    def _lead(self, key: str) -> asyncio.Future[Answer]:
        future: asyncio.Future[Answer] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        return future

    async def _follow(self, key: str) -> Answer:
        self._count("coalesced")
        # `shield`: si este seguidor se cancela, no cancela el cálculo de los demás.
        return replace(await asyncio.shield(self._inflight[key]), cache="coalesced")

    async def _finish(
        self, future: asyncio.Future[Answer], lookup: CacheLookup, answer: Answer
    ) -> None:
        if future.done():
            return
        future.set_result(answer)
        self._count("miss")
        cacheable = is_cacheable_answer(abstained=answer.abstained, citations=len(answer.citations))
        # Ni sin verificar ni degradada: la caché serviría el plan B cuando el modelo ya va bien.
        if cacheable and answer.verified and not answer.degraded:
            await self.cache.store_answer(lookup, to_payload(answer))

    @staticmethod
    def _abort(future: asyncio.Future[Answer], exc: BaseException) -> None:
        if future.done():
            return
        if isinstance(exc, Exception):
            future.set_exception(exc)
        else:
            # La primera petición se canceló (cliente cerrado): las que esperaban reintentan.
            future.set_exception(
                LLMTransientError(
                    "Se canceló la petición que calculaba la respuesta.", retry_after=1
                )
            )
        future.exception()  # Marcada como leída: sin aviso si nadie esperaba.
