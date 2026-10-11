"""Agentic harness (fase 8): ninguna salida ni acción de un agente llega a producción sin validar.

Tres puntos de intercepción que no dependen del framework:
- `generate_validated`: artefactos estructurados con autocorrección (propuestas, emails, SQL).
- `GuardedExecutor`: cada tool que pide el modelo pasa por guardas antes de ejecutarse.
- `GuardedRuntime`: guardas de entrada y de salida alrededor de cualquier `AgentRuntime`.
"""

from harness.correction import Attempt, CorrectionResult, correction_feedback, generate_validated
from harness.executor import (
    POLICY_DENIED,
    ArgumentRules,
    Denial,
    GuardedExecutor,
    MaxCallsPerTurn,
    ToolGuard,
    TurnTrace,
    current_trace,
    turn_trace,
)
from harness.runtime import BLOCKED_INPUT_ANSWER, GuardedRuntime, TurnRecord
from harness.validation import (
    Validator,
    Violation,
    extract_json,
    forbidden_patterns,
    from_rules,
    parse_output,
    render_feedback,
    run_validators,
)

__all__ = [
    "BLOCKED_INPUT_ANSWER",
    "POLICY_DENIED",
    "ArgumentRules",
    "Attempt",
    "CorrectionResult",
    "Denial",
    "GuardedExecutor",
    "GuardedRuntime",
    "MaxCallsPerTurn",
    "ToolGuard",
    "TurnRecord",
    "TurnTrace",
    "Validator",
    "Violation",
    "correction_feedback",
    "current_trace",
    "extract_json",
    "forbidden_patterns",
    "from_rules",
    "generate_validated",
    "parse_output",
    "render_feedback",
    "run_validators",
    "turn_trace",
]
