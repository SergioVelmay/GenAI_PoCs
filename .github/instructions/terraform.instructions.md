---
applyTo: "infra/terraform/**"
description: "Terraform para AWS, Azure y GCP: validación sin coste y recursos desactivados por defecto."
---

# Terraform (`infra/terraform/{aws,azure,gcp}`)

- Estado local; `terraform.tfvars` ignorado por Git (`*.tfvars`); se versiona la plantilla `terraform.tfvars.example`.
- Todo recurso con coste nuevo va detrás de una variable `*_enabled = false` por defecto (como `foundry_enabled`, `agentcore_enabled`, `mcp_gateway_enabled`, `apim_enabled`, `content_safety_enabled`, `bedrock_guardrail_enabled`, `queue_workers_enabled`, `observability_enabled`) y con su coste estimado en un comentario.
- Nombres en Azure con la convención CAF `<tipo>-<carga>-<entorno>-<región>-<instancia>` (`aif-headache-dev-swc-001`; Storage sin guiones: `stheadachedevswc001`), calculados en `locals` de `main.tf` (`caf_suffix`, `region_abbr`). Nada de sufijos aleatorios. Antes de un apply con nombres nuevos, comprobar su disponibilidad global (`az storage account check-name`, `az servicebus namespace exists`, `checkNameAvailability` / `checkDomainAvailability`).
  - Si un nombre está ocupado: primero mirar si es nuestro en la papelera (Cognitive Services, Key Vault y APIM retienen el nombre tras borrarlos; `az cognitiveservices account list-deleted` → purgar). Si es de otro, subir `instance` (`002`) para todos los recursos, no solo el que choca, y volver a comprobar.
  - Si alguna vez hace falta un sufijo aleatorio: solo consonantes y dígitos, sin vocales (como las matrículas), para que no forme palabras que parezcan tener significado.
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
- **GCP** (desde la 0.G2): proveedores `google` y `google-beta` (`~> 8.10`; los recursos en preview, con `provider = google-beta`), región `europe-west4`. El proyecto se crea a mano (cuenta de facturación de prueba) y Terraform gestiona lo de dentro.
  - Antes de cualquier `plan`/`apply`: `$env:CLOUDSDK_CONFIG = "$env:USERPROFILE\.gcloud-headache"`, `gcloud config list` y ADC de usuario (`gcloud auth application-default login`). Nunca claves JSON de cuentas de servicio.
  - Cada API se habilita en `var.services` (`google_project_service`, `disable_on_destroy = false`); una API nueva de la pasada se añade ahí, no en el recurso que la usa.
  - El provider lleva `billing_project` + `user_project_override = true`: con ADC de usuario, Billing Budgets devuelve 403 sin ellos.
  - El presupuesto mide el gasto **bruto** (`EXCLUDE_ALL_CREDITS`): con créditos de prueba, el neto es 0 y las alertas no saltarían.
  - Nombres `<project>-<environment>-<pieza>` (`headache-dev-app`); IDs cortos (cuentas de servicio 6–30, pools 4–32). Un pool de federación borrado retiene su ID 30 días.
  - Federación de la CI con condición por IDs numéricos de GitHub (repo y owner) y por `ref` (solo `main`): una PR no obtiene credenciales.
  - Roles de la cuenta de servicio `headache-dev-app`: cada fase añade solo los suyos.
