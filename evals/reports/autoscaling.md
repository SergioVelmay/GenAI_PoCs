# Autoescalado por cola (demo local)

Política: objetivo 20 mensajes por réplica, 0-8 réplicas, activación 0, ventana de bajada 30 s, enfriamiento 45 s.

| t (s) | Mensajes | Pide la fórmula | Réplicas antes | Réplicas después | Motivo |
|---:|---:|---:|---:|---:|---|
| 1 | 0 | 0 | 0 | 0 | steady |
| 19 | 856 | 8 | 0 | 4 | scale_up_limited |
| 25 | 1200 | 8 | 4 | 8 | scale_up |
| 31 | 1098 | 8 | 8 | 8 | steady |
| 38 | 941 | 8 | 8 | 8 | steady |
| 42 | 786 | 8 | 8 | 8 | steady |
| 45 | 621 | 8 | 8 | 8 | steady |
| 53 | 460 | 8 | 8 | 8 | steady |
| 56 | 304 | 8 | 8 | 8 | steady |
| 64 | 142 | 8 | 8 | 8 | steady |
| 67 | 0 | 0 | 8 | 8 | stabilizing |
| 97 | 175 | 8 | 8 | 8 | steady |
| 100 | 216 | 8 | 8 | 8 | steady |
| 108 | 64 | 4 | 8 | 8 | stabilizing |
| 112 | 0 | 0 | 8 | 8 | stabilizing |
| 138 | 0 | 0 | 8 | 4 | scale_down |
| 147 | 0 | 0 | 4 | 1 | cooldown |
| 155 | 0 | 0 | 1 | 0 | scale_down |
| 163 | 0 | 0 | 0 | 0 | steady |

Pico de réplicas: 8.
