# Evals de retrieval (fase 5.2)

- Golden dataset v2: 144 preguntas ({'acl_adversarial': 14, 'cross_lingual': 19, 'factual': 78, 'filtered': 17, 'unanswerable': 16}); corpus `evals/corpus/knowledge.jsonl` (sha256 `3ac13cba8716`).
- Corpus indexado: 116 documentos / 131 chunks; embeddings `hashing-1024`; top_k=5, candidate_k=20.
- Coste: 0 (todo local). Latencia medida en este equipo, solo orientativa.
- Limitacion: con embeddings `hashing` las cifras de `vector` validan el arnes, no la calidad semantica; BM25 si es una baseline legitima.

## Resumen (preguntas con respuesta)

| Motor | Estrategia | Hit@1 | Hit@5 | Recall@5 | MRR | nDCG@5 | Evidencia@5 | Fugas ACL | Sin resultados (sin respuesta) | Planes fallidos | p50 ms | p95 ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| native | bm25 | 0.895 | 1.000 | 0.741 | 0.924 | 0.852 | 1.000 | 0 | 3/30 | 0 | 1.1 | 1.3 |
| native | vector | 0.947 | 1.000 | 0.759 | 0.974 | 0.897 | 1.000 | 0 | 2/30 | 0 | 0.2 | 0.3 |
| native | hybrid | 0.912 | 1.000 | 0.741 | 0.953 | 0.875 | 1.000 | 0 | 2/30 | 0 | 1.4 | 1.6 |
| native | rerank | 0.895 | 0.991 | 0.737 | 0.936 | 0.862 | 0.991 | 0 | 2/30 | 0 | 2.0 | 2.2 |
| native | parent-child | 0.912 | 1.000 | 0.741 | 0.953 | 0.875 | 1.000 | 0 | 2/30 | 0 | 1.4 | 1.7 |
| llamaindex | bm25 | 0.903 | 1.000 | 0.741 | 0.928 | 0.856 | 1.000 | 0 | 6/30 | 0 | 17.2 | 19.6 |
| llamaindex | vector | 0.947 | 1.000 | 0.759 | 0.974 | 0.897 | 1.000 | 0 | 2/30 | 0 | 18.0 | 19.9 |
| llamaindex | hybrid | 0.903 | 1.000 | 0.759 | 0.947 | 0.876 | 1.000 | 0 | 2/30 | 0 | 31.5 | 33.9 |
| llamaindex | rerank | 0.895 | 0.991 | 0.737 | 0.934 | 0.860 | 0.991 | 0 | 2/30 | 0 | 31.9 | 34.9 |
| llamaindex | parent-child | 0.903 | 1.000 | 0.759 | 0.947 | 0.876 | 1.000 | 0 | 2/30 | 0 | 32.3 | 37.3 |
| llamaindex | auto-merge | 0.947 | 1.000 | 0.759 | 0.974 | 0.897 | 1.000 | 0 | 2/30 | 0 | 24.1 | 26.8 |

## Hit@5 por segmento

| Motor | Estrategia | doc_type=event_sheet (n) | doc_type=faq (n) | doc_type=policy (n) | doc_type=venue_guide (n) | language=en (n) | language=es (n) | question_type=cross_lingual (n) | question_type=factual (n) | question_type=filtered (n) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| native | bm25 | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| native | vector | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| native | hybrid | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| native | rerank | 1.00 (18) | 1.00 (40) | 0.97 (36) | 1.00 (20) | 0.98 (57) | 1.00 (57) | 1.00 (19) | 0.99 (78) | 1.00 (17) |
| native | parent-child | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| llamaindex | bm25 | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| llamaindex | vector | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| llamaindex | hybrid | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| llamaindex | rerank | 1.00 (18) | 1.00 (40) | 0.97 (36) | 1.00 (20) | 0.98 (57) | 1.00 (57) | 1.00 (19) | 0.99 (78) | 1.00 (17) |
| llamaindex | parent-child | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| llamaindex | auto-merge | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |

## Preguntas que ninguna estrategia acierta en el top-5 (0)

