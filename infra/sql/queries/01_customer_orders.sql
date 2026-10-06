-- Historial de pedidos de un cliente (pantalla "Mis entradas" / tool de soporte).
-- Índice clave: orders(customer_id, created_at) -> Index Scan Backward + LIMIT sin ordenar.
SELECT o.id, o.created_at, o.status, o.quantity, o.total_amount, o.currency,
       e.title, e.city, s.starts_at
FROM orders o
JOIN sessions s ON s.id = o.session_id
JOIN events e ON e.id = s.event_id
WHERE o.customer_id = 'cus_0000042'
ORDER BY o.created_at DESC
LIMIT 20;
