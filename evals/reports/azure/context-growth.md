# Conversación larga: compactación propia frente a Foundry Agent Service

- 14 turnos: dato personal, 12 preguntas de conocimiento y la pregunta final sobre el dato. Modelo `gpt-4.1-mini` en ambos.

| Sabor | Llamadas | Tokens entrada | Cacheados | Tokens salida | Compactaciones | Coste USD | Recuerda el dato |
|---|---:|---:|---:|---:|---:|---:|---|
| native (compactación propia) | 26 | 77701 | 71424 | 939 | 0 | 0.0112 | sí |
| foundry (conversation del servicio) | 28 | 103697 | 96128 | 1326 | 0 | 0.0148 | sí |

## Tokens de entrada por turno

| Turno | native (compactación propia) | foundry (conversation del servicio) |
|---:|---:|---:|
| 1 | 2830 | 2828 |
| 2 | 3272 | 3264 |
| 3 | 3782 | 3858 |
| 4 | 4284 | 4462 |
| 5 | 4794 | 5045 |
| 6 | 5307 | 5633 |
| 7 | 5821 | 6242 |
| 8 | 9498 | 10726 |
| 9 | 7112 | 8610 |
| 10 | 3864 | 9745 |
| 11 | 8274 | 10916 |
| 12 | 4407 | 12077 |
| 13 | 9433 | 13293 |
| 14 | 5023 | 6998 |

## Respuesta final

- **native (compactación propia)**: Te llamas Ana y me dijiste que viajas a Lisboa en noviembre. ¿Quieres que te ayude con algo relacionado con tu viaje?
- **foundry (conversation del servicio)**: Te llamas Ana y me dijiste que en noviembre viajas a Lisboa. ¿Quieres que te ayude con algo más?
