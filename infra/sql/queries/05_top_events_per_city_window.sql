-- Top 3 eventos por ciudad según ingresos: CTEs encadenadas + window function RANK().
-- Recorre TODOS los pedidos pagados: ningún índice B-tree lo evita. Es una consulta analítica;
-- en producción iría a una réplica de lectura, a una vista materializada o a un data warehouse.
WITH revenue AS (
    SELECT e.city,
           e.id        AS event_id,
           e.title,
           e.currency,
           SUM(o.total_amount) AS revenue,
           SUM(o.quantity)     AS tickets
    FROM orders o
    JOIN sessions s ON s.id = o.session_id
    JOIN events e ON e.id = s.event_id
    WHERE o.status = 'paid'
    GROUP BY e.city, e.id, e.title, e.currency
),
ranked AS (
    SELECT revenue.*,
           RANK() OVER (PARTITION BY city ORDER BY revenue DESC) AS position
    FROM revenue
)
SELECT city, position, title, revenue, currency, tickets
FROM ranked
WHERE position <= 3
ORDER BY city, position;
