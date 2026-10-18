"""Medidor de coste de una eval con LLM: cuenta llamadas, tokens y dolares, y corta al presupuesto.

Se enchufa como `LLMTracer` en `ObservedLLM` / `build_gateway`, asi que mide exactamente las
mismas llamadas que se facturan (incluidas las de correccion de structured outputs y las del
clasificador del router). Puede reenviar cada *generation* a otro tracer (Langfuse).
"""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from headache_core.ports.llm import ChatMessage, GenerationObservation, LLMResponse, LLMTracer


class BudgetExceededError(RuntimeError):
    """Se ha alcanzado el presupuesto (o el coste ya no es medible): no se lanzan mas llamadas."""


@dataclass(frozen=True, slots=True)
class MeterSnapshot:
    calls: int
    failed_calls: int
    unpriced_calls: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: str
    budget_usd: str | None
    calls_by_model: dict[str, int] = field(default_factory=dict)
    cost_by_model: dict[str, str] = field(default_factory=dict)


class CostMeter:
    """Corta *antes* de cada llamada: el exceso maximo es una llamada por peticion concurrente."""

    def __init__(
        self, *, budget_usd: Decimal | None = None, forward_to: LLMTracer | None = None
    ) -> None:
        if budget_usd is not None and budget_usd <= 0:
            raise ValueError("El presupuesto debe ser positivo")
        self._budget = budget_usd
        self._forward_to = forward_to
        self.calls = 0
        self.failed_calls = 0
        self.unpriced_calls = 0
        self.input_tokens = 0
        self.cached_input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = Decimal(0)
        # Por modelo: demuestra qué modelo generó y cuál juzgó (sesgo de autopreferencia).
        self.calls_by_model: Counter[str] = Counter()
        self.cost_by_model: defaultdict[str, Decimal] = defaultdict(Decimal)

    def start_generation(
        self,
        *,
        name: str,
        messages: Sequence[ChatMessage],
        metadata: Mapping[str, str],
    ) -> GenerationObservation:
        if self._budget is not None:
            if self.unpriced_calls:
                raise BudgetExceededError("Modelo sin precio en PriceTable: coste no acotable")
            if self.cost_usd >= self._budget:
                raise BudgetExceededError(
                    f"Presupuesto agotado: {self.cost_usd} >= {self._budget} USD"
                )
        forwarded = (
            self._forward_to.start_generation(name=name, messages=messages, metadata=metadata)
            if self._forward_to is not None
            else None
        )
        return _MeteredGeneration(self, forwarded)

    def snapshot(self) -> MeterSnapshot:
        return MeterSnapshot(
            calls=self.calls,
            failed_calls=self.failed_calls,
            unpriced_calls=self.unpriced_calls,
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=str(self.cost_usd),
            budget_usd=None if self._budget is None else str(self._budget),
            calls_by_model=dict(sorted(self.calls_by_model.items())),
            cost_by_model={model: str(cost) for model, cost in sorted(self.cost_by_model.items())},
        )


class _MeteredGeneration:
    def __init__(self, meter: CostMeter, forwarded: GenerationObservation | None) -> None:
        self._meter = meter
        self._forwarded = forwarded

    def finish(self, response: LLMResponse, *, cost_usd: Decimal | None) -> None:
        meter = self._meter
        meter.calls += 1
        meter.input_tokens += response.usage.input_tokens
        meter.cached_input_tokens += response.usage.cached_input_tokens
        meter.output_tokens += response.usage.output_tokens
        meter.calls_by_model[response.model] += 1
        if cost_usd is None:
            meter.unpriced_calls += 1
        else:
            meter.cost_usd += cost_usd
            meter.cost_by_model[response.model] += cost_usd
        if self._forwarded is not None:
            self._forwarded.finish(response, cost_usd=cost_usd)

    def fail(self, error: BaseException) -> None:
        self._meter.calls += 1
        self._meter.failed_calls += 1
        if self._forwarded is not None:
            self._forwarded.fail(error)
