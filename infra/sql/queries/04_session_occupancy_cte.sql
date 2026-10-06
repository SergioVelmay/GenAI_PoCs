-- Ocupación por sesión de un evento: CTE de ventas + LEFT JOIN para incluir sesiones sin ventas.
-- Índices clave: sessions(event_id, starts_at) y orders(session_id) (¡la FK sin índice!).
WITH sold AS (
    SELECT o.session_id,
           SUM(o.quantity) AS tickets_sold,
           COUNT(*)        AS orders
    FROM orders o
    JOIN sessions s ON s.id = o.session_id
    WHERE s.event_id = 'evt_000007'
      AND o.status = 'paid'
    GROUP BY o.session_id
)
SELECT s.id,
       s.starts_at,
       s.capacity,
       s.available,
       COALESCE(sold.tickets_sold, 0) AS tickets_sold,
       COALESCE(sold.orders, 0)       AS orders,
       ROUND(100.0 * (s.capacity - s.available) / s.capacity, 1) AS occupancy_pct
FROM sessions s
LEFT JOIN sold ON sold.session_id = s.id
WHERE s.event_id = 'evt_000007'
ORDER BY s.starts_at;
