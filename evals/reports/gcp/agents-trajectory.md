# Trayectoria: la medida propia (12.1) frente al Evaluation Service de GCP (12.G2)

- Trayectorias de `evals/reports/azure/agents-llm.json`: 84 casos (sabor por escenario).
- **Propia**: subsecuencia común más larga. **Documentada**: definición de Google (nombres en la otra lista; una repetición cuenta cada vez), calculada en local.
- **Servicio**: `EvaluateInstances` de `aiplatform_v1beta1` (sin ejecutar: `--service`, con coste).

| Métrica | Propia | Documentada | Servicio | Casos propia ≠ documentada |
|---|---:|---:|---:|---:|
| `trajectory_exact_match` | 31% | 31% | — | 0 |
| `trajectory_in_order_match` | — | 39% | — | — |
| `trajectory_any_order_match` | — | 39% | — | — |
| `trajectory_precision` | 74% | 75% | — | 4 |
| `trajectory_recall` | 62% | 62% | — | 2 |

## Casos en los que las dos definiciones no coinciden (6)

| Sabor | Escenario | Esperada | Hecha | Propia P / R | Documentada P / R |
|---|---|---|---|---:|---:|
| native | refund-question-only-es | get_order → search_knowledge | load_skill → get_order → get_event → list_my_orders → get_order → search_knowledge | 33% / 100% | 50% / 100% |
| langgraph | memory-preferences-cross-thread-es | remember_preference → remember_preference → search_events | remember_preference | 100% / 33% | 100% / 67% |
| llamaindex | refund-question-only-es | get_order → search_knowledge | load_skill → get_order → get_event → list_my_orders → get_event → get_order | 17% / 50% | 33% / 50% |
| maf | refund-question-only-es | get_order → search_knowledge | load_skill → get_order → get_event → list_my_orders → get_event → get_order | 17% / 50% | 33% / 50% |
| strands | refund-question-only-es | get_order → search_knowledge | load_skill → get_order → get_event → list_my_orders → get_event → get_order | 17% / 50% | 33% / 50% |
| foundry | memory-preferences-cross-thread-es | remember_preference → remember_preference → search_events | remember_preference | 100% / 33% | 100% / 67% |
