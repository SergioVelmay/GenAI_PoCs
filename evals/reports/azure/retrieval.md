# Evals de retrieval (fase 5.2)

- Golden dataset v1: 144 preguntas ({'acl_adversarial': 14, 'cross_lingual': 19, 'factual': 78, 'filtered': 17, 'unanswerable': 16}); corpus `evals/corpus/knowledge.jsonl` (sha256 `4bbd46f0ee6a`).
- Corpus indexado: 116 documentos / 131 chunks; embeddings `azure:text-embedding-3-small-1024`; top_k=5, candidate_k=20.
- Coste: 0 (todo local). Latencia medida en este equipo, solo orientativa.
- Limitacion: con embeddings `hashing` las cifras de `vector` validan el arnes, no la calidad semantica; BM25 si es una baseline legitima.

## Resumen (preguntas con respuesta)

| Motor | Estrategia | Hit@1 | Hit@5 | Recall@5 | MRR | nDCG@5 | Evidencia@5 | Fugas ACL | Sin resultados (sin respuesta) | p50 ms | p95 ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| azure | bm25 | 0.983 | 1.000 | 0.746 | 0.990 | 0.904 | 1.000 | 0 | 2/30 | 54.5 | 91.7 |
| azure | vector | 1.000 | 1.000 | 0.952 | 1.000 | 0.979 | 1.000 | 0 | 2/30 | 173.4 | 257.4 |
| azure | hybrid | 1.000 | 1.000 | 0.789 | 1.000 | 0.922 | 1.000 | 0 | 2/30 | 190.9 | 262.3 |
| azure-semantic | hybrid | 0.991 | 1.000 | 0.974 | 0.993 | 0.980 | 1.000 | 0 | 2/30 | 228.2 | 417.7 |

## Hit@5 por segmento

| Motor | Estrategia | doc_type=event_sheet (n) | doc_type=faq (n) | doc_type=policy (n) | doc_type=venue_guide (n) | language=en (n) | language=es (n) | question_type=cross_lingual (n) | question_type=factual (n) | question_type=filtered (n) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| azure | bm25 | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| azure | vector | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| azure | hybrid | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| azure-semantic | hybrid | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |

## Preguntas que ninguna estrategia acierta en el top-5 (0)

