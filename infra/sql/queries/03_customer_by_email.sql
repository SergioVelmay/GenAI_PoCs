-- Buscar un cliente por email sin distinguir mayúsculas (tool típica de un agente de soporte).
-- Índice clave: índice de EXPRESIÓN sobre lower(email). Un índice sobre `email` no serviría.
SELECT id, full_name, country, language
FROM customers
WHERE lower(email) = lower('User42@Example.com');
