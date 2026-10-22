# Experimento `booking-agent-prompt-v2` (fase 12.7)

booking-agent v1 (producción) frente al candidato v2 (idioma y memoria explícita).

- Dataset `agents` (14 casos, los mismos en las dos variantes); modo `scripted`; métrica de decisión `resolved`.
- Comparación pareada: McNemar exacto (solo cuentan los casos que cambian de resultado) e intervalo bootstrap del 95 % de la diferencia (tratamiento menos control).

## Variantes

| Variante | Sabor | Harness | Prompt | Modelo | Métrica | Superados | Resueltos | Invariantes | Llamadas LLM |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| control | native | no | producción | por defecto | 100.0% | 14/14 | 100.0% | 0 | 50 |
| tratamiento | native | no | booking-agent v2 | por defecto | 100.0% | 14/14 | 100.0% | 0 | 50 |

## Comparación

- Casos que solo supera el control: **0**; solo el tratamiento: **0**. p-valor (McNemar exacto): **1.0000** (alfa = 0.05).
- Diferencia: +0.0% (IC 95 %: +0.0% a +0.0%).
- **Decisión: `mantener_control`** — ningún caso cambia de resultado: no aporta nada.
- Para que McNemar pueda dar significativo con un 80 % de discordantes a favor hacen falta al menos 12 casos discordantes: un dataset de 14 casos casi nunca los tiene.

## Casos que cambian

| Caso | Control | Tratamiento |
|---|---|---|
| (ninguno) | | |
