"""Provider de promptfoo (fase 12.5): cada variante contra el agente de Headache con harness.

promptfoo lo ejecuta con `PROMPTFOO_PYTHON` (el Python del entorno de uv), en un proceso que
reutiliza entre llamadas: la base de conocimiento se carga una vez. Coste 0: modelo guionizado
comprometido, sin red.
"""

from __future__ import annotations

import json
from typing import Any

from rag_evals.redteam.promptfoo import BASE, run_case


async def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    variables = context.get("vars", {})
    flavor = options.get("config", {}).get("flavor", "native")
    try:
        result = await run_case(
            prompt,
            str(variables["scenario_id"]),
            strategy=str(variables.get("strategy", BASE)),
            flavor=str(flavor),
        )
    except Exception as exc:  # promptfoo muestra el error en el caso, no aborta la ejecución.
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"output": json.dumps(result, ensure_ascii=False)}
