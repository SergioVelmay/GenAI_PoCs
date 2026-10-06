"""Contabilidad de coste por petición a partir de los tokens que devuelve cada proveedor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from headache_core.ports import TokenUsage

_PER_MILLION = Decimal(1_000_000)
_REGION_PREFIXES = frozenset({"eu", "us", "apac", "global", "jp", "au", "ca", "us-gov"})


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD por millón de tokens. `cache_write=None` => la escritura en caché cuesta como `input`."""

    input: Decimal
    output: Decimal
    cached_input: Decimal
    cache_write: Decimal | None = None


def _p(inp: str, out: str, cached: str, write: str | None = None) -> ModelPrice:
    return ModelPrice(
        Decimal(inp), Decimal(out), Decimal(cached), None if write is None else Decimal(write)
    )


# Precios orientativos on-demand (Global Standard en Azure, US en Bedrock). Revísalos en la
# página oficial antes de sacar conclusiones: cambian y varían por región.
DEFAULT_PRICES: Mapping[str, ModelPrice] = {
    "gpt-4.1-nano": _p("0.10", "0.40", "0.025"),
    "gpt-4.1-mini": _p("0.40", "1.60", "0.10"),
    "gpt-4.1": _p("2.00", "8.00", "0.50"),
    "gpt-4o-mini": _p("0.15", "0.60", "0.075"),
    "amazon.nova-micro": _p("0.035", "0.14", "0.00875"),
    "amazon.nova-lite": _p("0.06", "0.24", "0.015"),
    "amazon.nova-pro": _p("0.80", "3.20", "0.20"),
    "anthropic.claude-haiku-4-5": _p("1.00", "5.00", "0.10", "1.25"),
    "anthropic.claude-sonnet-4-5": _p("3.00", "15.00", "0.30", "3.75"),
}


def normalize_model_name(model: str) -> str:
    """`bedrock/converse/eu.amazon.nova-lite-v1:0` -> `amazon.nova-lite-v1:0`."""
    name = model.lower().rsplit("/", 1)[-1]
    region, _, rest = name.partition(".")
    return rest if rest and region in _REGION_PREFIXES else name


class PriceTable:
    def __init__(self, prices: Mapping[str, ModelPrice] = DEFAULT_PRICES) -> None:
        self._prices = dict(prices)

    def lookup(self, model: str) -> ModelPrice | None:
        """Coincidencia por prefijo más largo: `gpt-4.1-mini-2025-04-14` -> `gpt-4.1-mini`."""
        name = normalize_model_name(model)
        matches = [key for key in self._prices if name.startswith(key)]
        return self._prices[max(matches, key=len)] if matches else None

    def cost(self, model: str, usage: TokenUsage) -> Decimal | None:
        price = self.lookup(model)
        if price is None:
            return None
        uncached = max(usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens, 0)
        write_price = price.input if price.cache_write is None else price.cache_write
        total = (
            uncached * price.input
            + usage.cached_input_tokens * price.cached_input
            + usage.cache_write_tokens * write_price
            + usage.output_tokens * price.output
        )
        return total / _PER_MILLION
