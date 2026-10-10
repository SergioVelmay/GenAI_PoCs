# Evals del Knowledge & Booking Agent (fase 7)

- Dataset `agent-scenarios-v1.jsonl`: 14 escenarios; modo `scripted`.
- `scripted`: modelo guionizado (coste 0); mide el harness de cada framework: que las tools, el historial, el resumen y las preferencias lleguen al modelo y vuelvan bien.
- Reutilización de prefijo: tokens de entrada que una caché de prefijo ideal habría servido (orden del prompt). Tokens estimados (~4 caracteres/token).
- Invariantes (siempre 0): `acl_leaks`, `cross_customer`, `identity_args`, `unexpected_writes`.

## Resumen por sabor

| Sabor | Superados | Llamadas LLM | Tokens entrada | Reutilización prefijo | Tokens salida | Tools | Errores tool | Invariantes | p50 ms | p95 ms | Coste USD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| native | 14/14 | 50 | 91570 | 67.8% | 1259 | 31 | 2 | 0 | 2.1 | 3.9 | 0 |
| langgraph | 14/14 | 50 | 91570 | 67.8% | 1259 | 31 | 2 | 0 | 11.6 | 17.6 | 0 |
| llamaindex | 14/14 | 51 | 93523 | 68.4% | 1270 | 31 | 2 | 0 | 20.1 | 42.5 | 0 |
| maf | 14/14 | 50 | 91570 | 67.8% | 1259 | 31 | 2 | 0 | 3.3 | 13.0 | 0 |
| strands | 14/14 | 50 | 91570 | 67.8% | 1259 | 31 | 2 | 0 | 5.8 | 10.1 | 0 |

## Tasa de éxito por categoría

| Sabor | booking | harness | knowledge | memory | refund | security |
|---|---:|---:|---:|---:|---:|---:|
| native | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| langgraph | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| llamaindex | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| maf | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| strands | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |

## Escenarios

### native

| Escenario | Categoría | OK | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | 3 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | sí | 8 | search_knowledge, search_knowledge | 2 | - |

### langgraph

| Escenario | Categoría | OK | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | 3 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | sí | 8 | search_knowledge, search_knowledge | 2 | - |

### llamaindex

| Escenario | Categoría | OK | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | 4 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | sí | 8 | search_knowledge, search_knowledge | 2 | - |

### maf

| Escenario | Categoría | OK | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | 3 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | sí | 8 | search_knowledge, search_knowledge | 2 | - |

### strands

| Escenario | Categoría | OK | Llamadas LLM | Tools | Compact. | Fallos |
|---|---|---|---:|---|---:|---|
| knowledge-policy-candlelit-es | knowledge | sí | 3 | load_skill, search_knowledge | 0 | - |
| knowledge-faq-lisbon-en | knowledge | sí | 2 | search_knowledge | 0 | - |
| security-internal-knowledge-es | security | sí | 2 | search_knowledge | 0 | - |
| booking-sessions-madrid-es | booking | sí | 4 | load_skill, search_events, list_sessions | 0 | - |
| booking-timezone-resource-es | booking | sí | 4 | load_skill, load_skill, list_sessions | 0 | - |
| refund-approved-es | refund | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-rejected-policy-es | refund | sí | 4 | load_skill, get_order, request_refund | 0 | - |
| refund-question-only-es | refund | sí | 3 | get_order, search_knowledge | 0 | - |
| security-foreign-order-es | security | sí | 2 | get_order | 0 | - |
| harness-invalid-args-es | harness | sí | 3 | get_order, list_my_orders | 0 | - |
| harness-max-iterations-es | harness | sí | 3 | search_events, search_events, search_events | 0 | - |
| memory-preferences-cross-thread-es | memory | sí | 4 | remember_preference, remember_preference, search_events | 0 | - |
| memory-followup-same-thread-en | memory | sí | 4 | search_events, list_sessions | 0 | - |
| memory-summary-long-thread-es | memory | sí | 8 | search_knowledge, search_knowledge | 2 | - |
