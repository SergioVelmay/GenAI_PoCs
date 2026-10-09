# Evals de generacion (fase 5.3)

- Golden dataset v1: 144 preguntas ({'acl_adversarial': 14, 'cross_lingual': 19, 'factual': 78, 'filtered': 17, 'unanswerable': 16}); corpus sha256 `4bbd46f0ee6a`.
- top_k=5, candidate_k=20, embeddings `hashing-1024`.
- `extractive`: la respuesta son los chunks recuperados citados (coste 0). `llm`: el modelo selecciona citas literales; el servicio rechaza citas inventadas.
- Evidencia = algun literal esperado aparece en la respuesta. Precision de citas = citas validas cuyo documento es gold (solo preguntas con respuesta).

## Resumen

| Motor | Estrategia | Modo | Evidencia | Abstencion falsa | Abstencion correcta | Validez citas | Precision citas | Citas/resp. | Rechazadas | Fugas ACL | p50 ms | p95 ms | Coste acumulado USD |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| native | hybrid | extractive | 1.000 | 0.000 | 0.067 | 1.000 | 0.218 | 4.579 | 0 | 0 | 1.7 | 2.5 | 0 |
| native | bm25 | extractive | 1.000 | 0.000 | 0.100 | 1.000 | 0.218 | 4.579 | 0 | 0 | 1.3 | 1.9 | 0 |
| native | parent-child | extractive | 1.000 | 0.000 | 0.067 | 1.000 | 0.218 | 4.579 | 0 | 0 | 1.7 | 2.3 | 0 |
| llamaindex | hybrid | extractive | 1.000 | 0.000 | 0.067 | 1.000 | 0.226 | 4.579 | 0 | 0 | 42.2 | 71.1 | 0 |
| llamaindex | bm25 | extractive | 1.000 | 0.000 | 0.200 | 1.000 | 0.218 | 4.579 | 0 | 0 | 23.7 | 39.6 | 0 |
| llamaindex | parent-child | extractive | 1.000 | 0.000 | 0.067 | 1.000 | 0.226 | 4.579 | 0 | 0 | 46.4 | 82.1 | 0 |

## Abstencion / evidencia por tipo de pregunta

| Motor | Estrategia | acl_adversarial (n) | cross_lingual (n) | factual (n) | filtered (n) | unanswerable (n) |
|---|---|---:|---:|---:|---:|---:|
| native | hybrid | 0.00 / 0.00 (14) | 0.00 / 1.00 (19) | 0.00 / 1.00 (78) | 0.00 / 1.00 (17) | 0.12 / 0.00 (16) |
| native | bm25 | 0.07 / 0.00 (14) | 0.00 / 1.00 (19) | 0.00 / 1.00 (78) | 0.00 / 1.00 (17) | 0.12 / 0.00 (16) |
| native | parent-child | 0.00 / 0.00 (14) | 0.00 / 1.00 (19) | 0.00 / 1.00 (78) | 0.00 / 1.00 (17) | 0.12 / 0.00 (16) |
| llamaindex | hybrid | 0.00 / 0.00 (14) | 0.00 / 1.00 (19) | 0.00 / 1.00 (78) | 0.00 / 1.00 (17) | 0.12 / 0.00 (16) |
| llamaindex | bm25 | 0.14 / 0.00 (14) | 0.00 / 1.00 (19) | 0.00 / 1.00 (78) | 0.00 / 1.00 (17) | 0.25 / 0.00 (16) |
| llamaindex | parent-child | 0.00 / 0.00 (14) | 0.00 / 1.00 (19) | 0.00 / 1.00 (78) | 0.00 / 1.00 (17) | 0.12 / 0.00 (16) |
