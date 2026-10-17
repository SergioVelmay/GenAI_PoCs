"""Escenarios de la suite de agentes (dataset en evals/datasets/agent-scenarios-v1.jsonl)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from headache_core.domain import OrderStatus
from rag_evals import EVALS_DIR

SCENARIOS_PATH = EVALS_DIR / "datasets" / "agent-scenarios-v1.jsonl"
# Fase 8.6: el modelo guionizado hace de modelo COMPROMETIDO (obedece al atacante).
ATTACKS_PATH = EVALS_DIR / "datasets" / "attack-scenarios-v1.jsonl"

type Category = Literal[
    "knowledge", "booking", "refund", "security", "memory", "harness", "attack", "benign"
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlannedCall(_Model):
    name: str
    # Los valores de texto admiten marcadores: {tool:NOMBRE.ruta}, {memory:clave}, {seen:literal}.
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Solo se pide si este literal llegó al modelo: simula obedecer una instrucción inyectada.
    when_seen: str | None = None


class ScenarioTurn(_Model):
    user: str
    # Texto que debe recibir el modelo si el harness lo transforma (p. ej. PII redactada).
    model_sees: str | None = None
    thread: str = "t1"
    # Guion del modo `scripted`: un paso = las tools que pide el modelo en una iteración.
    plan: list[list[PlannedCall]] = Field(default_factory=list)
    final: str


class ContextOverride(_Model):
    trigger_tokens: int
    target_tokens: int
    summarize: bool = True


class Expectations(_Model):
    # Subsecuencia ordenada de tools que deben ejecutarse (sin error o con el error esperado).
    tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    # Tipos de error de tool que deben aparecer (el agente debe recuperarse de ellos).
    errors: list[str] = Field(default_factory=list)
    # Sobre la respuesta del último turno (sin distinguir mayúsculas).
    answer_contains: list[str] = Field(default_factory=list)
    answer_excludes: list[str] = Field(default_factory=list)
    order_status: dict[str, OrderStatus] = Field(default_factory=dict)
    preferences: dict[str, str] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    min_compactions: int = 0
    stop_reason: Literal["answer", "max_iterations", "guardrail"] = "answer"
    # Códigos del harness en todos los turnos (conjunto exacto: [] = no se disparó ninguno).
    guardrails: list[str] = Field(default_factory=list)
    pending_approvals: int = 0
    # Literales que nunca deben llegar al modelo (cargas de inyección, PII).
    never_seen: list[str] = Field(default_factory=list)


class Scenario(_Model):
    id: str
    category: Category
    description: str
    customer_id: str
    turns: list[ScenarioTurn] = Field(min_length=1)
    expect: Expectations
    preferences: dict[str, str] = Field(default_factory=dict)
    context: ContextOverride | None = None
    max_iterations: int = 6


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[Scenario]:
    with path.open(encoding="utf-8") as source:
        scenarios = [Scenario.model_validate_json(line) for line in source if line.strip()]
    ids = [s.id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("IDs de escenario duplicados")
    return scenarios
