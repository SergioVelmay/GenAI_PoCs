# Caché semántica: calibración del umbral

Embeddings: `hashing` · dataset `semantic-cache-v1.jsonl` (36 casos). Bolsa de palabras: valida el arnés, no la calidad semántica.

**Umbral recomendado (con guarda de facetas): 0.55** → 12/17 paráfrasis reutilizadas (71%), 0 falsos aciertos.
Sin guarda, para no servir respuestas equivocadas hay que subir el umbral a 0.9, y entonces se reutilizan 0/17 (0%).

| Umbral | Guarda | Paráfrasis reutilizadas | Falsos aciertos | Límite conocido | Personales guardadas |
|---:|:---:|---:|---:|---:|---:|
| 0.5 | sí | 12/17 (71%) | 0 | 1 | 0 |
| 0.55 | sí | 12/17 (71%) | 0 | 1 | 0 |
| 0.6 | sí | 11/17 (65%) | 0 | 1 | 0 |
| 0.65 | sí | 5/17 (29%) | 0 | 1 | 0 |
| 0.7 | sí | 5/17 (29%) | 0 | 1 | 0 |
| 0.75 | sí | 2/17 (12%) | 0 | 1 | 0 |
| 0.8 | sí | 1/17 (6%) | 0 | 1 | 0 |
| 0.85 | sí | 0/17 (0%) | 0 | 1 | 0 |
| 0.9 | sí | 0/17 (0%) | 0 | 1 | 0 |
| 0.95 | sí | 0/17 (0%) | 0 | 0 | 0 |
| 0.99 | sí | 0/17 (0%) | 0 | 0 | 0 |
| 0.5 | no | 12/17 (71%) | 11 | 1 | 0 |
| 0.55 | no | 12/17 (71%) | 11 | 1 | 0 |
| 0.6 | no | 11/17 (65%) | 10 | 1 | 0 |
| 0.65 | no | 5/17 (29%) | 10 | 1 | 0 |
| 0.7 | no | 5/17 (29%) | 9 | 1 | 0 |
| 0.75 | no | 2/17 (12%) | 7 | 1 | 0 |
| 0.8 | no | 1/17 (6%) | 7 | 1 | 0 |
| 0.85 | no | 0/17 (0%) | 5 | 1 | 0 |
| 0.9 | no | 0/17 (0%) | 0 | 1 | 0 |
| 0.95 | no | 0/17 (0%) | 0 | 0 | 0 |
| 0.99 | no | 0/17 (0%) | 0 | 0 | 0 |

## Casos en el umbral recomendado

| Caso | Tipo | Similitud | ¿Debe acertar? | Decisión |
|---|---|---:|:---:|---|
| para-doors-es | paraphrase | 0.722 | sí | hit |
| para-print-es | paraphrase | 0.224 | sí | below_threshold |
| para-app-es | paraphrase | 0.630 | sí | hit |
| para-refund-candlelit-es | paraphrase | 0.471 | sí | below_threshold |
| para-age-nightlife-es | paraphrase | 0.630 | sí | hit |
| para-date-theatre-es | paraphrase | 0.806 | sí | hit |
| para-doors-en | paraphrase | 0.714 | sí | hit |
| para-print-en | paraphrase | 0.571 | sí | hit |
| para-refund-food-en | paraphrase | 0.629 | sí | hit |
| para-support-hours-es | paraphrase | 0.445 | sí | below_threshold |
| para-cancelled-es | paraphrase | 0.784 | sí | hit |
| para-kids-exhibition-es | paraphrase | 0.252 | sí | below_threshold |
| para-refund-theatre-en | paraphrase | 0.614 | sí | hit |
| para-venue-access-es | paraphrase | 0.617 | sí | hit |
| para-age-nightlife-en | paraphrase | 0.750 | sí | hit |
| para-refund-tours-es | paraphrase | 0.143 | sí | below_threshold |
| para-city-alias-es | paraphrase | 0.630 | sí | hit |
| trap-city-es | trap | 0.875 | no | facet_mismatch |
| trap-city-en | trap | 0.857 | no | facet_mismatch |
| trap-city-alias-es | trap | 0.833 | no | facet_mismatch |
| trap-city-missing-es | trap | 0.866 | no | facet_mismatch |
| trap-category-es | trap | 0.658 | no | facet_mismatch |
| trap-category-en | trap | 0.750 | no | facet_mismatch |
| trap-operation-es | trap | 0.727 | no | facet_mismatch |
| trap-operation-en | trap | 0.556 | no | facet_mismatch |
| trap-operation-transfer-es | trap | 0.429 | no | below_threshold |
| trap-number-tickets-es | trap | 0.889 | no | facet_mismatch |
| trap-number-minutes-es | trap | 0.875 | no | facet_mismatch |
| trap-event-id | trap | 0.800 | no | facet_mismatch |
| trap-language | trap | 0.189 | no | miss |
| trap-acl | trap | 1.000 | no | miss |
| personal-order-identical | personal | 1.000 | no | personal |
| personal-order-en | personal | 0.857 | no | personal |
| personal-email | personal | 0.875 | no | personal |
| personal-refund-status | personal | 0.671 | no | personal |
| gap-negation | trap | 0.926 | no | falso acierto (límite conocido) |
