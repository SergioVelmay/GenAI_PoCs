# Evals de retrieval (fase 5.2)

- Golden dataset v1: 144 preguntas ({'acl_adversarial': 14, 'cross_lingual': 19, 'factual': 78, 'filtered': 17, 'unanswerable': 16}); corpus `evals/corpus/knowledge.jsonl` (sha256 `4bbd46f0ee6a`).
- Corpus indexado: 116 documentos / 131 chunks; embeddings `azure:text-embedding-3-small-1024`; top_k=5, candidate_k=20.
- Coste: 0 (todo local). Latencia medida en este equipo, solo orientativa.
- Limitacion: con embeddings `hashing` las cifras de `vector` validan el arnes, no la calidad semantica; BM25 si es una baseline legitima.

## Resumen (preguntas con respuesta)

| Motor | Estrategia | Hit@1 | Hit@5 | Recall@5 | MRR | nDCG@5 | Evidencia@5 | Fugas ACL | Sin resultados (sin respuesta) | Planes fallidos | p50 ms | p95 ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| azure | bm25 | 0.983 | 1.000 | 0.746 | 0.990 | 0.904 | 1.000 | 0 | 3/30 | 0 | 38.6 | 58.7 |
| azure | vector | 1.000 | 1.000 | 0.952 | 1.000 | 0.979 | 1.000 | 0 | 2/30 | 0 | 161.3 | 207.0 |
| azure | hybrid | 1.000 | 1.000 | 0.789 | 1.000 | 0.922 | 1.000 | 0 | 2/30 | 0 | 173.7 | 237.4 |
| azure | rewrite | 0.974 | 1.000 | 0.767 | 0.983 | 0.904 | 1.000 | 0 | 4/30 | 2 | 837.3 | 1033.5 |
| azure | hyde | 0.991 | 1.000 | 0.947 | 0.994 | 0.974 | 1.000 | 0 | 4/30 | 2 | 685.0 | 902.1 |
| azure | subquestions | 0.886 | 0.983 | 0.789 | 0.923 | 0.875 | 1.000 | 0 | 4/30 | 2 | 1092.2 | 1557.9 |
| azure | router | 0.965 | 0.965 | 0.776 | 0.965 | 0.894 | 0.965 | 0 | 4/30 | 3 | 706.3 | 960.9 |
| azure | auto | 0.833 | 0.833 | 0.763 | 0.833 | 0.795 | 0.833 | 0 | 4/30 | 3 | 802.5 | 1013.8 |
| azure-semantic | hybrid | 0.991 | 1.000 | 0.974 | 0.993 | 0.980 | 1.000 | 0 | 2/30 | 0 | 223.4 | 282.8 |
| azure-semantic | rewrite | 0.991 | 1.000 | 0.969 | 0.993 | 0.981 | 1.000 | 0 | 4/30 | 2 | 897.4 | 1071.7 |
| azure-semantic | hyde | 0.983 | 1.000 | 0.974 | 0.988 | 0.975 | 1.000 | 0 | 4/30 | 2 | 729.3 | 922.8 |
| azure-semantic | subquestions | 0.912 | 0.974 | 0.925 | 0.934 | 0.902 | 0.991 | 0 | 4/30 | 2 | 1192.1 | 1793.8 |
| azure-semantic | router | 0.947 | 0.956 | 0.934 | 0.950 | 0.938 | 0.956 | 0 | 4/30 | 4 | 734.3 | 958.2 |
| azure-semantic | auto | 0.781 | 0.789 | 0.750 | 0.783 | 0.757 | 0.789 | 0 | 4/30 | 2 | 828.1 | 989.5 |

## Hit@5 por segmento

| Motor | Estrategia | doc_type=event_sheet (n) | doc_type=faq (n) | doc_type=policy (n) | doc_type=venue_guide (n) | language=en (n) | language=es (n) | question_type=cross_lingual (n) | question_type=factual (n) | question_type=filtered (n) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| azure | bm25 | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| azure | vector | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| azure | hybrid | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| azure | rewrite | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| azure | hyde | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| azure | subquestions | 1.00 (18) | 0.97 (40) | 0.97 (36) | 1.00 (20) | 0.98 (57) | 0.98 (57) | 1.00 (19) | 0.97 (78) | 1.00 (17) |
| azure | router | 0.94 (18) | 0.93 (40) | 1.00 (36) | 1.00 (20) | 0.98 (57) | 0.95 (57) | 0.95 (19) | 0.96 (78) | 1.00 (17) |
| azure | auto | 0.33 (18) | 0.82 (40) | 1.00 (36) | 1.00 (20) | 0.77 (57) | 0.89 (57) | 0.79 (19) | 0.81 (78) | 1.00 (17) |
| azure-semantic | hybrid | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| azure-semantic | rewrite | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| azure-semantic | hyde | 1.00 (18) | 1.00 (40) | 1.00 (36) | 1.00 (20) | 1.00 (57) | 1.00 (57) | 1.00 (19) | 1.00 (78) | 1.00 (17) |
| azure-semantic | subquestions | 1.00 (18) | 0.97 (40) | 0.94 (36) | 1.00 (20) | 0.98 (57) | 0.96 (57) | 1.00 (19) | 0.96 (78) | 1.00 (17) |
| azure-semantic | router | 0.89 (18) | 0.93 (40) | 1.00 (36) | 1.00 (20) | 0.98 (57) | 0.93 (57) | 0.89 (19) | 0.96 (78) | 1.00 (17) |
| azure-semantic | auto | 0.17 (18) | 0.80 (40) | 1.00 (36) | 0.95 (20) | 0.79 (57) | 0.79 (57) | 0.53 (19) | 0.81 (78) | 1.00 (17) |

## Preguntas que ninguna estrategia acierta en el top-5 (0)

