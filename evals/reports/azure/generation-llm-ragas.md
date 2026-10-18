# Evals de generacion (fase 5.3)

- Golden dataset v1: 144 preguntas ({'acl_adversarial': 14, 'cross_lingual': 19, 'factual': 78, 'filtered': 17, 'unanswerable': 16}); corpus sha256 `4bbd46f0ee6a`.
- top_k=5, candidate_k=20, embeddings `hashing-1024`.
- `extractive`: la respuesta son los chunks recuperados citados (coste 0). `llm`: el modelo selecciona citas literales; el servicio rechaza citas inventadas.
- Evidencia = algun literal esperado aparece en la respuesta. Precision de citas = citas validas cuyo documento es gold (solo preguntas con respuesta).

## Resumen

| Motor | Estrategia | Modo | Evidencia | Abstencion falsa | Abstencion correcta | Validez citas | Precision citas | Citas/resp. | Rechazadas | Fugas ACL | p50 ms | p95 ms | Coste acumulado USD |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| native | hybrid | llm | 0.833 | 0.000 | 1.000 | 1.000 | 0.929 | 1.400 | 2 | 0 | 684.9 | 2471.7 | 0.2135830 |

## Abstencion / evidencia por tipo de pregunta

| Motor | Estrategia | acl_adversarial (n) | cross_lingual (n) | factual (n) | filtered (n) | unanswerable (n) |
|---|---|---:|---:|---:|---:|---:|
| native | hybrid | 1.00 / 0.00 (4) | 0.00 / 1.00 (4) | 0.00 / 0.50 (4) | 0.00 / 1.00 (4) | 1.00 / 0.00 (4) |

## Jueces LLM

| Motor | Estrategia | Juez | Muestras | Errores juez | faithfulness | answer_relevancy | context_precision | context_recall |
|---|---|---|---:|---:|---:|---:|---:|---:|
| native | hybrid | ragas | 10 | 0 | 1.000 | 0.576 | 0.737 | 1.000 |

Llamadas y coste por modelo (acumulado):

- native / hybrid: `gpt-4.1-2025-04-14` 94 llamadas / 0.207936 USD, `gpt-4.1-mini-2025-04-14` 15 llamadas / 0.0054244 USD, `gpt-4.1-nano-2025-04-14` 5 llamadas / 0.0002226 USD
