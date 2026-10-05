-- Se ejecuta una única vez, al crear el volumen, sobre la base de datos POSTGRES_DB (headache).
CREATE EXTENSION IF NOT EXISTS vector;

-- Base de datos separada para Langfuse (mismo servidor, distinto "inquilino").
CREATE DATABASE langfuse;
