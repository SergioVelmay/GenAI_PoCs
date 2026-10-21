"""Caché semántica delante del asistente (fase 10.3), con fusión de peticiones idénticas.

- Acierto: se devuelve la respuesta guardada sin llamar al RAG ni al modelo.
- Fallo: se responde y, si la respuesta es reutilizable, se guarda.
- *Single-flight*: en un pico, mil personas hacen la misma pregunta a la vez y la caché aún
  está vacía; sin fusión, las mil fallan y pagan mil llamadas (*cache stampede*). Aquí la
  primera calcula y las demás (misma pregunta normalizada y mismo espacio) esperan su
  resultado. Es por réplica: entre réplicas haría falta un candado en Redis.
- Lo personal ni se busca, ni se guarda, ni se fusiona: dos clientes con la misma pregunta
  sobre "mi pedido" esperan respuestas distintas.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import asdict, replace

from edge_api.assistant import Answer, Assistant
from headache_core.application.semantic_cache import SemanticCache
from headache_core.domain.admission import Caller, access_scope
from headache_core.domain.semantic_cache import (
    CacheScope,
    detect_language,
    is_cacheable_answer,
    normalize,
)


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
            self.outcomes["hit"] += 1
            return from_payload(lookup.payload)
        if lookup.reason == "personal":
            self.outcomes["bypass"] += 1
            return replace(await self._inner.answer(question, caller), cache="bypass")

        key = f"{lookup.scope.key()}|{normalize(question)}"
        leader = self._inflight.get(key)
        if leader is not None:
            self.outcomes["coalesced"] += 1
            # `shield`: si este seguidor se cancela, no cancela el cálculo de los demás.
            return replace(await asyncio.shield(leader), cache="coalesced")

        future: asyncio.Future[Answer] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            answer = await self._inner.answer(question, caller)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                future.cancel()
            else:
                future.set_exception(exc)
                future.exception()  # Marcada como leída: sin aviso si nadie esperaba.
            raise
        finally:
            self._inflight.pop(key, None)
        future.set_result(answer)
        self.outcomes["miss"] += 1
        if is_cacheable_answer(abstained=answer.abstained, citations=len(answer.citations)):
            await self.cache.store_answer(lookup, to_payload(answer))
        return answer
