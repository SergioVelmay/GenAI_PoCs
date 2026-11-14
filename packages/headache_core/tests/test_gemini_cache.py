from __future__ import annotations

import httpx
import pytest
from google.genai import errors, types

from headache_core.adapters.gemini_cache import CachePrefix, ExplicitCache

MODEL = "gemini-3.5-flash-lite"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeCaches:
    """Doble de `Client.aio.caches`: crea recursos con nombre y guarda lo que se borra."""

    def __init__(self, *, tokens: int = 5000, errors: list[BaseException] | None = None) -> None:
        self.tokens = tokens
        self.errors = errors or []
        self.created: list[tuple[str, types.CreateCachedContentConfig]] = []
        self.deleted: list[str] = []

    async def create(
        self, *, model: str, config: types.CreateCachedContentConfig
    ) -> types.CachedContent:
        if self.errors:
            raise self.errors.pop(0)
        self.created.append((model, config))
        return types.CachedContent(
            name=f"cachedContents/{len(self.created)}",
            usage_metadata=types.CachedContentUsageMetadata(total_token_count=self.tokens),
        )

    async def delete(self, *, name: str) -> object:
        self.deleted.append(name)
        return None


def _prefix(system: str = "Manual de políticas", model: str = MODEL) -> CachePrefix:
    return CachePrefix(
        model, system, (types.Content(role="user", parts=[types.Part(text="Contexto fijo")]),)
    )


def _api_error(code: int, message: str) -> errors.APIError:
    return errors.APIError(code, {"error": {"code": code, "message": message}})


async def test_creates_once_and_reuses_the_same_prefix() -> None:
    caches, clock = FakeCaches(), FakeClock()
    registry = ExplicitCache(caches, ttl_seconds=600, clock=clock)

    first = await registry.lookup_or_create(_prefix())
    second = await registry.lookup_or_create(_prefix())

    assert first is not None and second is not None
    assert (first.name, first.write_tokens) == ("cachedContents/1", 5000)
    # La segunda solo lee: la escritura ya se pagó.
    assert (second.name, second.write_tokens) == ("cachedContents/1", 0)
    model, config = caches.created[0]
    assert (model, config.system_instruction, config.ttl) == (MODEL, "Manual de políticas", "600s")
    assert len(caches.created) == 1


async def test_another_prefix_gets_its_own_cache() -> None:
    caches = FakeCaches()
    registry = ExplicitCache(caches, ttl_seconds=600, clock=FakeClock())

    await registry.lookup_or_create(_prefix("Manual A"))
    await registry.lookup_or_create(_prefix("Manual B"))

    assert len(caches.created) == 2


async def test_an_expiring_cache_is_recreated() -> None:
    caches, clock = FakeCaches(), FakeClock()
    registry = ExplicitCache(caches, ttl_seconds=600, clock=clock)
    await registry.lookup_or_create(_prefix())

    clock.now += 600 - 10  # Dentro del margen: podría caducar en mitad de la petición.
    hit = await registry.lookup_or_create(_prefix())

    assert hit is not None and hit.write_tokens == 5000
    assert len(caches.created) == 2


async def test_a_model_without_explicit_cache_is_not_retried() -> None:
    """Gemini 4 no la admite: la primera vez da 400 y desde entonces se usa la implícita."""
    caches = FakeCaches(errors=[_api_error(400, "Explicit caching is not supported")])
    registry = ExplicitCache(caches, ttl_seconds=600, clock=FakeClock())

    assert await registry.lookup_or_create(_prefix(model="gemini-4-hydrogen")) is None
    assert await registry.lookup_or_create(_prefix("Otro", model="gemini-4-hydrogen")) is None
    assert caches.created == []
    # Otro modelo sí lo intenta.
    assert await registry.lookup_or_create(_prefix()) is not None


async def test_a_prefix_below_the_minimum_is_not_retried_but_others_are() -> None:
    caches = FakeCaches(
        errors=[_api_error(400, "Cached content is too small. min_total_token_count=4096")]
    )
    registry = ExplicitCache(caches, ttl_seconds=600, clock=FakeClock())

    assert await registry.lookup_or_create(_prefix("corto")) is None
    assert await registry.lookup_or_create(_prefix("corto")) is None
    assert await registry.lookup_or_create(_prefix("largo")) is not None
    assert len(caches.created) == 1


async def test_transient_failures_degrade_this_call_and_retry_the_next() -> None:
    caches = FakeCaches(errors=[_api_error(429, "RESOURCE_EXHAUSTED")])
    registry = ExplicitCache(caches, ttl_seconds=600, clock=FakeClock())

    assert await registry.lookup_or_create(_prefix()) is None
    assert await registry.lookup_or_create(_prefix()) is not None


async def test_transport_errors_are_left_to_the_adapter() -> None:
    """Solo traduce errores de la API; los de red los degrada `GeminiLLM`."""
    registry = ExplicitCache(
        FakeCaches(errors=[httpx.ConnectError("sin red")]), ttl_seconds=600, clock=FakeClock()
    )

    with pytest.raises(httpx.ConnectError):
        await registry.lookup_or_create(_prefix())


async def test_aclose_deletes_live_caches_and_records_how_long_they_were_stored() -> None:
    caches, clock = FakeCaches(), FakeClock()
    registry = ExplicitCache(caches, ttl_seconds=600, clock=clock)
    await registry.lookup_or_create(_prefix())

    clock.now += 120
    await registry.aclose()

    assert caches.deleted == ["cachedContents/1"]
    (created,) = registry.created
    assert (created.tokens, created.stored_seconds(clock.now + 999)) == (5000, 120.0)
    # Una caché no borrada se paga hasta su TTL, no más.
    clock.now += 10_000
    leftover = ExplicitCache(FakeCaches(), ttl_seconds=600, clock=clock)
    await leftover.lookup_or_create(_prefix())
    assert leftover.created[0].stored_seconds(clock.now + 5000) == 600.0
