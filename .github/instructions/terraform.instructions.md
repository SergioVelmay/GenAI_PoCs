---
applyTo: "infra/terraform/**"
description: "Terraform para AWS y Azure: validación sin coste y recursos desactivados por defecto."
---

# Terraform (`infra/terraform/{aws,azure}`)

- Estado local; `terraform.tfvars` sin versionar (`budget_email` y, en Azure, `subscription_id`).
- Todo recurso con coste nuevo va detrás de una variable `*_enabled = false` por defecto (como `agentcore_enabled`, `mcp_gateway_enabled`, `apim_enabled`, `content_safety_enabled`, `bedrock_guardrail_enabled`) y con su coste estimado en un comentario.
- Validación sin coste (lo mismo que la CI, en cada módulo):
  `terraform fmt -check -recursive` · `terraform init -backend=false -input=false` · `terraform validate`.
  Usar `-check -recursive`, no solo `terraform fmt`: la CI de la 7.4 falló por eso.
- `plan` y `apply` requieren credenciales y **confirmación explícita**; antes, revisar coste y presupuesto (alertas de presupuesto activas). EKS/AKS solo efímeros (`apply` → probar → `destroy`).
- Identidades sin secretos (Managed Identity / IAM Roles; OIDC en CI). Nada de claves en variables ni outputs no sensibles.
- Lo que azurerm no gestiona se aprovisiona con scripts idempotentes con dry-run por defecto (`scripts/provision_*.py [--apply]`).
- Recursos de vista previa en Azure con `azapi` y versión de API explícita.
