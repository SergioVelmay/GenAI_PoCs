# Calibracion de jueces: acuerdo humano-juez

- Conjunto `calibration-v1` (20 muestras), etiquetado por un humano a ciegas; la adjudicacion corrige despistes y unifica el criterio, documentado en `labels-v1-adjudicated.csv`.
- Fiel segun el juez = faithfulness 1 (todas las afirmaciones respaldadas). Relevante = relevancia >= 2/3. Kappa de Cohen: 1 acuerdo total, 0 como el azar.
- Deteccion: muestras de cada tipo en las que el juez da la fidelidad esperada (no depende de las etiquetas humanas).

## Etiquetas a ciegas

| Juez | Muestras | Errores | Fiel: acierto | Fiel: kappa | Relevancia: MAE | Relevante: acierto | Relevante: kappa | good | fabricated | unsupported | off_topic | non_answer |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| deepeval | 20 | 0 | 0.700 | 0.429 | 0.267 | 0.650 | 0.205 | 8/8 | 3/4 | 0/4 | 1/2 | 0/2 |
| foundry | 20 | 0 | 0.800 | 0.640 | 0.254 | 0.800 | 0.529 | 8/8 | 4/4 | 4/4 | 2/2 | 0/2 |
| native | 20 | 0 | 0.800 | 0.643 | 0.117 | 0.850 | 0.634 | 8/8 | 4/4 | 4/4 | 2/2 | 0/2 |
| ragas | 20 | 0 | 0.800 | 0.643 | 0.333 | 0.400 | 0.055 | 8/8 | 4/4 | 4/4 | 2/2 | 0/2 |

## Etiquetas adjudicadas

| Juez | Muestras | Errores | Fiel: acierto | Fiel: kappa | Relevancia: MAE | Relevante: acierto | Relevante: kappa | good | fabricated | unsupported | off_topic | non_answer |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| deepeval | 20 | 0 | 0.600 | 0.304 | 0.100 | 0.800 | 0.474 | 8/8 | 3/4 | 0/4 | 1/2 | 0/2 |
| foundry | 20 | 0 | 0.900 | 0.817 | 0.196 | 0.950 | 0.857 | 8/8 | 4/4 | 4/4 | 2/2 | 0/2 |
| native | 20 | 0 | 0.900 | 0.815 | 0.050 | 1.000 | 1.000 | 8/8 | 4/4 | 4/4 | 2/2 | 0/2 |
| ragas | 20 | 0 | 0.900 | 0.815 | 0.368 | 0.250 | 0.026 | 8/8 | 4/4 | 4/4 | 2/2 | 0/2 |
