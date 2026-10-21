# Pruebas de carga del edge (fase 10.7)

Generado con `scripts/load_report.py` a partir de los CSV de Locust (`loadtests/locustfile.py`, forma `FestivalOnSale`). Análisis y conclusiones: [fase-10-escala.md](../../../docs/fase-10-escala.md#pruebas-de-carga).

## ingenuo

| Petición | Total | Fallos | p50 | p95 | p99 | Máx | req/s |
|---|---:|---:|---:|---:|---:|---:|---:|
| /ask | 85 | 32 (38%) | 12.00 s | 60.00 s | 60.00 s | 60.03 s | 0.6 |
| /ask (abuso) | 83 | 4 (5%) | 710 ms | 5.80 s | 59.00 s | 58.92 s | 0.6 |
| /ask (interno) | 54 | 18 (33%) | 3.30 s | 60.00 s | 60.00 s | 60.03 s | 0.4 |
| /ask/stream | 293 | 50 (17%) | 13.00 s | 60.00 s | 60.00 s | 60.13 s | 1.9 |
| /cases | 260 | 0 (0%) | 28 ms | 540 ms | 830 ms | 898 ms | 1.7 |
| TTFT (miss) | 243 | 0 (0%) | 1.40 s | 57.00 s | 59.00 s | 59.97 s | 1.6 |
| **Total** | 1018 | 104 (10%) | 660 ms | 60.00 s | 60.00 s | 60.13 s | 6.8 |

| Petición | Motivo | Veces |
|---|---|---:|
| /ask/stream | 504 | 49 |
| /ask | 503 model_unavailable | 26 |
| /ask (interno) | 503 model_unavailable | 14 |
| /ask | 504 | 6 |
| /ask (abuso) | 503 model_unavailable | 4 |
| /ask (interno) | 504 | 4 |
| /ask/stream | stream error | 1 |

- Admisión (preguntas): `{"admitted": 679}`
- Admisión (/cases): `{"admitted": 260}`
- Respuestas por origen: `{}`
- Respuestas degradadas: `{}`
- Decisiones de la caché: `{}`
- Proveedor (simulado): `{"calls": 505}`

## sin_cache

| Petición | Total | Fallos | p50 | p95 | p99 | Máx | req/s |
|---|---:|---:|---:|---:|---:|---:|---:|
| /ask | 279 | 235 (84%) | 5 ms | 2.80 s | 4.80 s | 5.09 s | 1.9 |
| /ask (abuso) | 609 | 471 (77%) | 5 ms | 3.50 s | 3.90 s | 4.59 s | 4.0 |
| /ask (interno) | 199 | 0 (0%) | 2.70 s | 4.00 s | 4.50 s | 4.52 s | 1.3 |
| /ask/stream | 1907 | 438 (23%) | 2.00 s | 3.90 s | 4.40 s | 5.39 s | 12.6 |
| /cases | 254 | 0 (0%) | 44 ms | 820 ms | 1.60 s | 1.68 s | 1.7 |
| TTFT (degraded) | 1110 | 0 (0%) | 2.00 s | 3.90 s | 4.00 s | 4.27 s | 7.4 |
| TTFT (miss) | 359 | 0 (0%) | 2.50 s | 4.40 s | 4.60 s | 5.39 s | 2.4 |
| **Total** | 4717 | 1144 (24%) | 2.00 s | 3.90 s | 4.40 s | 5.39 s | 31.3 |

| Petición | Motivo | Veces |
|---|---|---:|
| /ask (abuso) | 429 rate_limited | 442 |
| /ask/stream | 503 shed | 438 |
| /ask | 503 shed | 235 |
| /ask (abuso) | 503 shed | 29 |

- Admisión (preguntas): `{"admitted": 1904, "rate_limited": 442, "shed": 702}`
- Admisión (/cases): `{"admitted": 254}`
- Respuestas por origen: `{}`
- Respuestas degradadas: `{"degraded": 1366}`
- Decisiones de la caché: `{}`
- Proveedor (simulado): `{"calls": 532}`

## protegido

| Petición | Total | Fallos | p50 | p95 | p99 | Máx | req/s |
|---|---:|---:|---:|---:|---:|---:|---:|
| /ask | 274 | 0 (0%) | 9 ms | 890 ms | 1.70 s | 1.74 s | 1.8 |
| /ask (abuso) | 4780 | 4642 (97%) | 4 ms | 8 ms | 20 ms | 1.85 s | 31.7 |
| /ask (interno) | 271 | 0 (0%) | 9 ms | 770 ms | 1.80 s | 2.76 s | 1.8 |
| /ask/stream | 2921 | 1 (0%) | 8 ms | 78 ms | 670 ms | 1.43 s | 19.4 |
| /cases | 235 | 0 (0%) | 39 ms | 840 ms | 1.60 s | 1.91 s | 1.6 |
| TTFT (coalesced) | 10 | 0 (0%) | 1.10 s | 1.40 s | 1.40 s | 1.43 s | 0.1 |
| TTFT (hit) | 2892 | 0 (0%) | 8 ms | 42 ms | 540 ms | 1.22 s | 19.2 |
| TTFT (miss) | 18 | 0 (0%) | 770 ms | 1.40 s | 1.40 s | 1.38 s | 0.1 |
| **Total** | 11401 | 4643 (41%) | 7 ms | 40 ms | 710 ms | 2.76 s | 75.6 |

| Petición | Motivo | Veces |
|---|---|---:|
| /ask (abuso) | 429 rate_limited | 4642 |
| /ask/stream | 0 (conexión cortada en el cliente) | 1 |

- Admisión (preguntas): `{"admitted": 3603, "rate_limited": 4644}`
- Admisión (/cases): `{"admitted": 236}`
- Respuestas por origen: `{"coalesced": 14, "hit": 3521, "miss": 68}`
- Respuestas degradadas: `{}`
- Decisiones de la caché: `{"below_threshold": 18, "facet_mismatch": 37, "hit": 3521, "miss": 27, "stored": 68}`
- Proveedor (simulado): `{"calls": 68}`
