-- Catálogo filtrado por ciudad y categoría.
-- Lección: con ~1.000 eventos la tabla cabe en pocas páginas y el planificador puede preferir
-- un Seq Scan aunque exista ix_events_city_category. Un índice no siempre se usa (ni hace falta).
SELECT id, title, price_amount, currency
FROM events
WHERE city = 'Madrid'
  AND category = 'candlelit'
ORDER BY id
LIMIT 20;
