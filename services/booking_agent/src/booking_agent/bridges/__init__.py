"""Puentes framework ↔ `LLMPort`: el framework orquesta, pero cada llamada al modelo pasa por
nuestro gateway (routing, fallback, caché de prompt, coste y trazas) y los tests no tocan red.
"""
