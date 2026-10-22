# Experimento `flavor-native-vs-foundry` (fase 12.7)

Sabor nativo (control) frente a Foundry Agent Service emulado, suite de agentes.

- Dataset `agents` (14 casos, los mismos en las dos variantes); modo `scripted`; métrica de decisión `passed`.
- Comparación pareada: McNemar exacto (solo cuentan los casos que cambian de resultado) e intervalo bootstrap del 95 % de la diferencia (tratamiento menos control).

## Variantes

| Variante | Sabor | Harness | Prompt | Modelo | Métrica | Superados | Resueltos | Invariantes | Llamadas LLM |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| control | native | no | producción | por defecto | 100.0% | 14/14 | 100.0% | 0 | 50 |
| tratamiento | foundry | no | producción | por defecto | 92.9% | 13/14 | 100.0% | 0 | 48 |

## Comparación

- Casos que solo supera el control: **1**; solo el tratamiento: **0**. p-valor (McNemar exacto): **1.0000** (alfa = 0.05).
- Diferencia: -7.1% (IC 95 %: -21.4% a +0.0%).
- **Decisión: `sin_evidencia`** — solo 1 caso discrepa (menos de 10): falta muestra.
- Para que McNemar pueda dar significativo con un 80 % de discordantes a favor hacen falta al menos 12 casos discordantes: un dataset de 14 casos casi nunca los tiene.

## Casos que cambian

| Caso | Control | Tratamiento |
|---|---|---|
| memory-summary-long-thread-es | sí | no |
