-- Próximas sesiones con plazas de un evento (endpoint de disponibilidad).
-- Índice clave: sessions(event_id, starts_at).
SELECT s.id, s.starts_at, s.available, s.capacity
FROM sessions s
WHERE s.event_id = 'evt_000007'
  AND s.starts_at > TIMESTAMPTZ '2026-10-01 00:00:00+00'
  AND s.available > 0
ORDER BY s.starts_at;
