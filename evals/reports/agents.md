# Evals del Knowledge & Booking Agent (fases 7 y 12.1)

- Dataset `agent-scenarios-v1.jsonl`: 14 escenarios; modo `scripted`.
- `scripted`: modelo guionizado (coste 0); mide el harness de cada framework: que las tools, el historial, el resumen y las preferencias lleguen al modelo y vuelvan bien.
- Resueltos: el mundo queda como debe (pedidos, preferencias, aprobaciones, parámetros de las escrituras) sin tools prohibidas ni invariantes; no mira el texto de la respuesta.
- Trayectoria P/R: precisión y recall de las tools como subsecuencia ordenada; exacta = ambas 1. Parámetros: expectativas de `tool_args` cumplidas.
- Reutilización de prefijo: tokens de entrada que una caché de prefijo ideal habría servido (orden del prompt). Tokens estimados (~4 caracteres/token).
- Invariantes (siempre 0): `acl_leaks`, `cross_customer`, `identity_args`, `unexpected_writes`, `payload_to_model`, `unapproved_irreversible`.

## Calidad por sabor

| Sabor | Superados | Resueltos | Trayectoria P / R | Trayectoria exacta | Parámetros | Args inválidos | Invariantes |
|---|---:|---:|---:|---:|---:|---:|---:|
| native | 14/14 | 100.0% | 100.0% / 100.0% | 100.0% | 100.0% | 1 | 0 |
| langgraph | 14/14 | 100.0% | 100.0% / 100.0% | 100.0% | 100.0% | 1 | 0 |
| llamaindex | 14/14 | 100.0% | 100.0% / 100.0% | 100.0% | 100.0% | 1 | 0 |
| maf | 14/14 | 100.0% | 100.0% / 100.0% | 100.0% | 100.0% | 1 | 0 |
| strands | 14/14 | 100.0% | 100.0% / 100.0% | 100.0% | 100.0% | 1 | 0 |
| adk | 14/14 | 100.0% | 100.0% / 100.0% | 100.0% | 100.0% | 1 | 0 |
| foundry | 13/14 | 100.0% | 100.0% / 100.0% | 100.0% | 100.0% | 1 | 0 |
| agentcore | 14/14 | 100.0% | 100.0% / 100.0% | 100.0% | 100.0% | 1 | 0 |
| agentplatform | 14/14 | 100.0% | 100.0% / 100.0% | 100.0% | 100.0% | 1 | 0 |

## Coste y contexto por sabor

| Sabor | Llamadas LLM | Tokens entrada | Reutilización prefijo | Tokens salida | Tools | Errores tool | p50 ms | p95 ms | Coste USD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| native | 50 | 97142 | 68.0% | 1261 | 31 | 2 | 2.4 | 4.4 | 0 |
| langgraph | 50 | 97142 | 68.0% | 1261 | 31 | 2 | 8.0 | 14.1 | 0 |
| llamaindex | 51 | 99211 | 68.6% | 1272 | 31 | 2 | 18.0 | 38.7 | 0 |
| maf | 50 | 97142 | 68.0% | 1261 | 31 | 2 | 3.9 | 18.7 | 0 |
| strands | 50 | 97142 | 68.0% | 1261 | 31 | 2 | 5.3 | 8.2 | 0 |
| adk | 50 | 97142 | 68.0% | 1261 | 31 | 2 | 16.4 | 319.8 | 0 |
| foundry | 48 | 96748 | 68.8% | 1228 | 31 | 2 | 2.0 | 3.8 | 0 |
| agentcore | 50 | 97142 | 68.0% | 1261 | 31 | 2 | 6.5 | 41.6 | 0 |
| agentplatform | 50 | 97142 | 68.0% | 1261 | 31 | 2 | 18.6 | 36.5 | 0 |

## Tasa de éxito por categoría

| Sabor | booking | harness | knowledge | memory | refund | security |
|---|---:|---:|---:|---:|---:|---:|
| native | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| langgraph | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| llamaindex | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| maf | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| strands | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| adk | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| foundry | 1.00 | 1.00 | 1.00 | 0.67 | 1.00 | 1.00 |
| agentcore | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| agentplatform | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |

## Escenarios

### native

| Escenario | Categoría | OK | Resuelto | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | sí | 3 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | sí | sí | 8 | search_knowledge, search_knowledge | 2 | - |

### langgraph

| Escenario | Categoría | OK | Resuelto | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | sí | 3 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | sí | sí | 8 | search_knowledge, search_knowledge | 2 | - |

### llamaindex

| Escenario | Categoría | OK | Resuelto | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | sí | 4 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | sí | sí | 8 | search_knowledge, search_knowledge | 2 | - |

### maf

| Escenario | Categoría | OK | Resuelto | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | sí | 3 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | sí | sí | 8 | search_knowledge, search_knowledge | 2 | - |

### strands

| Escenario | Categoría | OK | Resuelto | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | sí | 3 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | sí | sí | 8 | search_knowledge, search_knowledge | 2 | - |

### adk

| Escenario | Categoría | OK | Resuelto | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | sí | 3 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | sí | sí | 8 | search_knowledge, search_knowledge | 2 | - |

### foundry

| Escenario | Categoría | OK | Resuelto | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | sí | 3 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | NO | sí | 6 | search_knowledge, search_knowledge | 0 | compactaciones 0 < 1 |

### agentcore

| Escenario | Categoría | OK | Resuelto | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | sí | 3 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | sí | sí | 8 | search_knowledge, search_knowledge | 2 | - |

### agentplatform

| Escenario | Categoría | OK | Resuelto | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | sí | 3 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | sí | sí | 8 | search_knowledge, search_knowledge | 2 | - |
