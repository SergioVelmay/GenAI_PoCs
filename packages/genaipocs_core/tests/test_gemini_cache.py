from __future__ import annotations

import httpx
import pytest
from google.genai import errors, types

from genaipocs_core.adapters.gemini_cache import (
    CachePrefix,
    CacheRegistryUnavailableError,
    ExplicitCache,
    SharedCache,
)

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


# --- Registro compartido entre réplicas (10.G3) ---------------------------------------------


class SharedRegistry:
    """Doble del registro en Redis: `put_if_absent` como `SET NX`."""

    def __init__(self, *, down: bool = False) -> None:
        self.entries: dict[str, SharedCache] = {}
        self.down = down

    async def get(self, key: str) -> SharedCache | None:
        if self.down:
            raise CacheRegistryUnavailableError("sin conexión")
        return self.entries.get(key)

    async def put_if_absent(self, key: str, cache: SharedCache) -> SharedCache:
        if self.down:
            raise CacheRegistryUnavailableError("sin conexión")
        return self.entries.setdefault(key, cache)


def _replica(
    registry: SharedRegistry, clock: FakeClock, caches: FakeCaches | None = None
) -> tuple[ExplicitCache, FakeCaches]:
    caches = caches or FakeCaches()
    cache = ExplicitCache(caches, ttl_seconds=600, clock=clock, registry=registry, wall_clock=clock)
    return cache, caches


async def test_a_second_replica_reuses_the_cache_of_the_first() -> None:
    """Heredado de la 2.G3: sin registro compartido, cada réplica pagaba su propia caché."""
    registry, clock = SharedRegistry(), FakeClock()
    first, first_caches = _replica(registry, clock)
    second, second_caches = _replica(registry, clock)

    created = await first.lookup_or_create(_prefix())
    reused = await second.lookup_or_create(_prefix())

    assert created is not None and reused is not None
    assert reused.name == created.name and reused.write_tokens == 0
    assert len(first_caches.created) == 1 and second_caches.created == []


async def test_the_loser_of_a_race_deletes_its_copy_and_uses_the_winner() -> None:
    registry, clock = SharedRegistry(), FakeClock()
    loser, loser_caches = _replica(registry, clock)
    key = _prefix().key()
    # Otra réplica registró la suya entre el `get` y el `put_if_absent` de esta.
    original_get = registry.get

    async def get_then_lose(k: str) -> SharedCache | None:
        found = await original_get(k)
        registry.entries[key] = SharedCache("cachedContents/winner", clock.now + 600)
        return found

    registry.get = get_then_lose  # type: ignore[method-assign,assignment]
    hit = await loser.lookup_or_create(_prefix())

    assert hit is not None and hit.name == "cachedContents/winner"
    assert hit.write_tokens == 5000  # La escritura de la copia sí se pagó.
    assert loser_caches.deleted == ["cachedContents/1"]  # El almacenamiento, no.
    assert loser.created[0].deleted_at == clock.now


async def test_shared_caches_are_left_to_expire_on_close() -> None:
    registry, clock = SharedRegistry(), FakeClock()
    replica, caches = _replica(registry, clock)
    await replica.lookup_or_create(_prefix())

    await replica.aclose()

    assert caches.deleted == []  # Otra réplica puede estar usándola.


async def test_an_expiring_shared_entry_is_not_borrowed() -> None:
    registry, clock = SharedRegistry(), FakeClock()
    registry.entries[_prefix().key()] = SharedCache("cachedContents/old", clock.now + 10)
    replica, caches = _replica(registry, clock)

    hit = await replica.lookup_or_create(_prefix())

    assert hit is not None and hit.name == "cachedContents/1"
    assert len(caches.created) == 1


async def test_registry_down_falls_back_to_a_cache_per_replica() -> None:
    registry, clock = SharedRegistry(down=True), FakeClock()
    replica, caches = _replica(registry, clock)

    first = await replica.lookup_or_create(_prefix())
    second = await replica.lookup_or_create(_prefix())

    assert first is not None and second is not None and first.name == second.name
    assert len(caches.created) == 1
