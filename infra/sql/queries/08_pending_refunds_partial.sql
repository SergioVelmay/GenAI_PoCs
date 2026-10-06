-- Cola de reembolsos pendientes para el back-office (los más antiguos primero).
-- Índice clave: PARCIAL sobre requested_at WHERE status = 'requested' (diminuto).
SELECT r.id, r.order_id, r.amount, r.currency, r.requested_at
FROM refunds r
WHERE r.status = 'requested'
ORDER BY r.requested_at
LIMIT 50;
