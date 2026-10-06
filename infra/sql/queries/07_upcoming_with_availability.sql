-- Próximas 20 sesiones con plazas en todo el catálogo ("¿qué hay esta noche?").
-- Índice clave: PARCIAL sobre starts_at WHERE available > 0.
SELECT s.id, s.event_id, s.starts_at, s.available
FROM sessions s
WHERE s.available > 0
  AND s.starts_at > TIMESTAMPTZ '2026-10-01 00:00:00+00'
ORDER BY s.starts_at
LIMIT 20;
