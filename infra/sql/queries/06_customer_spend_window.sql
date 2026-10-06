-- Comportamiento de compra de un cliente: gasto acumulado y tiempo entre compras.
-- Window functions: SUM() OVER (running total) y LAG() (fila anterior), por moneda.
SELECT o.id,
       o.created_at,
       o.total_amount,
       o.currency,
       SUM(o.total_amount) OVER w                AS running_total,
       o.created_at - LAG(o.created_at) OVER w   AS since_previous_order
FROM orders o
WHERE o.customer_id = 'cus_0000042'
  AND o.status = 'paid'
WINDOW w AS (PARTITION BY o.currency ORDER BY o.created_at)
ORDER BY o.currency, o.created_at;
