from __future__ import annotations

from decimal import Decimal

import pytest

from headache_core.application.llm import PriceTable, normalize_model_name
from headache_core.ports import TokenUsage


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("bedrock/converse/eu.amazon.nova-lite-v1:0", "amazon.nova-lite-v1:0"),
        ("eu.anthropic.claude-haiku-4-5-20251001-v1:0", "anthropic.claude-haiku-4-5-20251001-v1:0"),
        ("azure/gpt-4.1-mini", "gpt-4.1-mini"),
        ("gpt-4.1-mini-2025-04-14", "gpt-4.1-mini-2025-04-14"),
    ],
)
def test_normalize_model_name(raw: str, expected: str) -> None:
    assert normalize_model_name(raw) == expected


def test_lookup_uses_longest_prefix() -> None:
    prices = PriceTable()

    assert prices.lookup("gpt-4.1-mini-2025-04-14") == prices.lookup("gpt-4.1-mini")
    assert prices.lookup("gpt-4.1-mini") != prices.lookup("gpt-4.1")
    assert prices.lookup("modelo-desconocido") is None


def test_cached_tokens_are_billed_at_discount() -> None:
    usage = TokenUsage(input_tokens=2000, output_tokens=10, cached_input_tokens=1536)

    # (464 x 0.40 + 1536 x 0.10 + 10 x 1.60) / 1M
    assert PriceTable().cost("gpt-4.1-mini-2025-04-14", usage) == Decimal("0.0003552")


def test_cache_writes_have_their_own_price() -> None:
    usage = TokenUsage(input_tokens=5000, output_tokens=100, cache_write_tokens=4800)

    # (200 x 1.00 + 4800 x 1.25 + 100 x 5.00) / 1M
    cost = PriceTable().cost("eu.anthropic.claude-haiku-4-5-20251001-v1:0", usage)

    assert cost == Decimal("0.0067")


def test_gemini_implicit_cache_and_thinking_tokens() -> None:
    # 4096 cacheados (dentro de la entrada) y 38 de salida, de ellos 30 de razonamiento.
    usage = TokenUsage(input_tokens=5000, output_tokens=38, cached_input_tokens=4096)

    # (904 x 1.50 + 4096 x 0.075 + 38 x 7.50) / 1M
    assert PriceTable().cost("gemini-4-hydrogen", usage) == Decimal("0.0019482")
    # LiteLLM (2.G2) antepone la ruta; el prefijo más largo separa Flash-Lite de Flash.
    lite = PriceTable().lookup("vertex_ai/gemini-3.5-flash-lite")
    assert lite is not None
    assert lite.input == Decimal("0.30")


def test_gemini_explicit_cache_bills_write_read_and_storage() -> None:
    # Petición que crea la caché: 4500 escritos (precio de entrada) que además se leen (90 % de
    # descuento) y 20 sin cachear; el adaptador suma la escritura a la entrada (9020).
    usage = TokenUsage(9020, 5, cached_input_tokens=4500, cache_write_tokens=4500)
    prices = PriceTable()

    # (20 x 0.30 + 4500 x 0.03 + 4500 x 0.30 + 5 x 2.50) / 1M
    assert prices.cost("gemini-3.5-flash-lite", usage) == Decimal("0.0015035")
    # El almacenamiento va aparte: 4500 tokens durante 30 minutos a 1 $ por millón y hora.
    assert prices.storage_cost("gemini-3.5-flash-lite", 4500, 1800) == Decimal("0.00225")
    # Gemini 4 no tiene caché explícita.
    assert prices.storage_cost("gemini-4-hydrogen", 4500, 1800) is None


def test_unknown_model_has_no_cost() -> None:
    assert PriceTable().cost("modelo-desconocido", TokenUsage(10, 10)) is None
