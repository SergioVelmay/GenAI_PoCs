# Detección de drift (fase 12.8)

> Generado por `uv run python -m rag_evals.llmops.drift`. Ventanas sintéticas y deterministas: valida el detector, no describe tráfico real.

PSI del último día frente a los 7 anteriores. Niveles: < 0.1 estable, 0.1-0.25 moderado, ≥ 0.25 significativo. Alerta (`DistributionDrift`) solo si es significativo y hay muestra mínima.

| Señal | Métrica | Muestra mínima |
|---|---|---|
| `case-intents` | `headache.cases.triaged` | 200 |
| `agent-tools` | `gen_ai.execute_tool.duration` | 200 |
| `agent-resolution` | `headache.eval.agent_verdicts` | 50 |

## ✅ semana-normal

Un día cualquiera: misma mezcla, solo ruido de muestreo.

| Señal | Muestra hoy | PSI | Nivel | Alerta |
|---|---:|---:|---|---|
| `case-intents` | 2000 | 0.002 | estable | no |
| `agent-tools` | 5000 | 0.002 | estable | no |
| `agent-resolution` | 120 | 0.016 | estable | no |

**Diagnóstico**: estable: nada que hacer.

## ✅ ola-de-cancelaciones

Se cancela un festival: se disparan los reembolsos y los agentes llaman más a get_order y request_refund, pero resuelven igual.

| Señal | Muestra hoy | PSI | Nivel | Alerta |
|---|---:|---:|---|---|
| `case-intents` | 2000 | 0.395 | significativo | sí |
| `agent-tools` | 5000 | 0.340 | significativo | sí |
| `agent-resolution` | 120 | 0.011 | estable | no |

**Diagnóstico**: externo: cambia lo que piden los clientes y los agentes lo absorben.

## ✅ regresion-silenciosa

El proveedor actualiza el modelo por debajo: los clientes piden lo mismo, pero los agentes buscan menos en la base de conocimiento y resuelven peor.

| Señal | Muestra hoy | PSI | Nivel | Alerta |
|---|---:|---:|---|---|
| `case-intents` | 2000 | 0.003 | estable | no |
| `agent-tools` | 5000 | 0.143 | moderado | no |
| `agent-resolution` | 120 | 0.570 | significativo | sí |

**Diagnóstico**: interno: cambia cómo se resuelve sin cambiar lo que se pide (prompt, modelo, índice).

## ✅ poca-muestra

La misma ola de cancelaciones un festivo de madrugada: 1 % del volumen. El PSI es alto, pero con tan pocos casos es ruido: se ve en el panel y no despierta a nadie.

| Señal | Muestra hoy | PSI | Nivel | Alerta |
|---|---:|---:|---|---|
| `case-intents` | 20 | 0.430 | significativo | no |
| `agent-tools` | 50 | 0.036 | estable | no |
| `agent-resolution` | 1 | 8.954 | significativo | no |

**Diagnóstico**: ruido: hay cambio, pero sin muestra suficiente; mirar el panel, no actuar.
