"""Carril C de evals: RAGAS y DeepEval conectados a nuestro `LLMPort`.

Requieren el extra opcional `evals` (`uv sync --all-packages --extra evals`). Los dos
frameworks aceptan un LLM propio mediante un adaptador, asi que todas sus llamadas pasan por
el gateway (routing, fallback, rate limit, trazas y medidor de coste) igual que el resto.
"""

import os

# Opt-out de telemetria ANTES de importar los frameworks (lo leen al importarse).
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
