---
name: explicar-concepto
description: Explica un concepto técnico de forma didáctica y anclada en este repo. Úsala cuando Sergio pregunte "explícame", "qué significa X en este contexto", "por qué pasa X" o pida ejemplos concretos en la PoC.
---

# Explicar un concepto

Sergio prepara entrevistas de AI Technical Lead (50 % técnico, 50 % negocio y stakeholders; producción en AWS): necesita entenderlo, saber defenderlo y saber explicarlo a quien no es técnico.

1. **Definición en una frase**, sin jerga innecesaria.
2. **Equivalencia** con algo que ya domina (Azure, Foundry, MAF, FastMCP, AI Search, Bicep, Azure DevOps) y su traducción a **AWS**.
3. **Dónde aparece en este repo**: rutas y símbolos concretos (enlaces a ficheros), no ejemplos inventados. Si hay varias variantes (p. ej. "gateway": LLM, MCP, API), una tabla con todas.
4. **Azure ↔ AWS ↔ framework** si aplica.
5. **Trampas habituales** o límites.
6. **Por qué le importa a Headache**: impacto en negocio, coste o riesgo, en 2–3 frases que entendería un stakeholder no técnico.
7. **Preguntas de entrevista**: una técnica y una de negocio, con respuesta modelo de 2–3 frases cada una.

Si la pregunta es "por qué pasa X" (un test saltado, un aviso de la CI), comprobarlo primero con evidencia (salida del comando, código) y decir si es correcto o si estamos ignorando algo. No implementar cambios salvo que Sergio lo pida.
