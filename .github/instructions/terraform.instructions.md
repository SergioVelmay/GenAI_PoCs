---
applyTo: "infra/terraform/**"
description: "Terraform para AWS y Azure: validación sin coste y recursos desactivados por defecto."
---

# Terraform (`infra/terraform/{aws,azure}`)

- Estado local; `terraform.tfvars` ignorado por Git (`*.tfvars`); se versiona la plantilla `terraform.tfvars.example`.
- Todo recurso con coste nuevo va detrás de una variable `*_enabled = false` por defecto (como `foundry_enabled`, `agentcore_enabled`, `mcp_gateway_enabled`, `apim_enabled`, `content_safety_enabled`, `bedrock_guardrail_enabled`) y con su coste estimado en un comentario.
- Nombres en Azure con la convención CAF `<tipo>-<carga>-<entorno>-<región>-<instancia>` (`aif-headache-dev-swc-001`; Storage sin guiones: `stheadachedevswc001`), calculados en `locals` de `main.tf` (`caf_suffix`, `region_abbr`). Nada de sufijos aleatorios. Antes de un apply con nombres nuevos, comprobar su disponibilidad global (`az storage account check-name`, `az servicebus namespace exists`, `checkNameAvailability` / `checkDomainAvailability`).
- Azure: el grupo de recursos `rg-<project>-<environment>` lo crea Terraform (con presupuesto asociado); no crearlo a mano. Foundry en `foundry.tf` (cuenta `AIServices` + proyecto + despliegues en serie).
- Antes de cualquier `plan`/`apply` en Azure: `$env:AZURE_CONFIG_DIR = "$env:USERPROFILE\.azure-headache"` (perfil de `az` aislado; el perfil por defecto es de otra organización) y comprobar `az account show`.
- Decisiones de Azure: región `swedencentral`; tiers gratuitos donde no recorten capacidades (Content Safety F0); AI Search **Basic** (~70 €/mes fijo: identidad gestionada, Semantic Ranker, Foundry IQ) y **efímero** (`search_enabled`: se crea al empezar una sesión de trabajo y se borra al terminar; al recrearlo, índice y reindexación según `docs/pendientes-cloud.md`). Recordar a Sergio apagarlo al cerrar cada sesión; presupuesto del grupo de recursos de 100 €/mes con alertas al 50/75/100 % (independiente de los presupuestos de la suscripción). La suscripción tiene recursos de otros proyectos en otros grupos: Terraform solo gestiona lo que está en su estado.
- Validación sin coste (lo mismo que la CI, en cada módulo):
  `terraform fmt -check -recursive` · `terraform init -backend=false -input=false` · `terraform validate`.
  Usar `-check -recursive`, no solo `terraform fmt`: la CI de la 7.4 falló por eso.
- `plan` y `apply` requieren credenciales y **confirmación explícita**; antes, revisar coste y presupuesto (alertas de presupuesto activas). EKS/AKS solo efímeros (`apply` → probar → `destroy`).
- Identidades sin secretos (Managed Identity / IAM Roles; OIDC en CI). Nada de claves en variables ni outputs no sensibles.
- Lo que azurerm no gestiona se aprovisiona con scripts idempotentes con dry-run por defecto (`scripts/provision_*.py [--apply]`).
- Recursos de vista previa en Azure con `azapi` y versión de API explícita.
