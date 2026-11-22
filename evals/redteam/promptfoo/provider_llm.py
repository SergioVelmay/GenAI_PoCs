"""Provider de promptfoo con atacante LLM (12.G3): un ataque generado contra el agente real.

A diferencia de `provider.py` (variantes congeladas de nuestros escenarios con el modelo
comprometido), aquí cada prompt lo genera el LLM de `redteam.provider`. El agente responde con el
LLM de `LLM_PROVIDER` y el harness, sobre el mundo determinista. Con coste: `REDTEAM_BUDGET_USD`.
"""

from __future__ import annotations

import json
from typing import Any

from rag_evals.redteam.promptfoo import run_open_prompt


async def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    flavor = options.get("config", {}).get("flavor", "native")
    try:
        result = await run_open_prompt(prompt, flavor=str(flavor))
    except Exception as exc:  # promptfoo muestra el error en el caso, no aborta la ejecución.
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"output": json.dumps(result, ensure_ascii=False)}
