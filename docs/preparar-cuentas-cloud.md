# Preparar las cuentas de Azure, AWS y GCP

Checkpoint G→13 B.3 (27-11-2026). Antes de las fases 13 (AWS), 14 (Azure) y 15 (GCP), y de las
sesiones de [pendientes-cloud.md](pendientes-cloud.md): cada nube con **una cuenta personal limpia**,
la tarjeta puesta, presupuesto con alertas e identidad sin claves. Las condiciones de la tabla se
comprobaron en las páginas oficiales el 27-11-2026 ([fuentes](#fuentes)); cambian a menudo, así que
se repasan antes de cada alta.

Reglas que aplican (AGENTS.md): pedir confirmación antes de crear o borrar recursos, de cualquier
acción con coste y de aceptar licencias; **nunca** tocar suscripciones, cuentas o proyectos
corporativos; ninguna credencial en el repo.

## 1. Tarjeta y créditos de prueba

| | Azure | AWS | GCP |
|---|---|---|---|
| ¿Tarjeta? | **Obligatoria** (crédito o débito, no prepago): verificación de identidad, retención de 1 $ que se devuelve | Plan Free: "no se pide a la mayoría" de clientes nuevos. En la práctica, **sí**: el plan Paid y los modelos de terceros de Bedrock (vía Marketplace) exigen método de pago válido | **Obligatoria**: retención de 0–1 $ |
| Crédito | 200 $ durante **30 días** (más servicios gratis 12 meses) | Hasta 200 $ (100 al darse de alta + 100 por actividades), 6 meses | 300 $ durante **90 días** |
| ¿Quién lo recibe? | Solo clientes nuevos de Azure; uno por cliente | Solo quien **nunca ha tenido** una cuenta de AWS | Quien nunca ha pagado en GCP, Maps o Firebase y no ha usado la prueba |
| ¿Cobran solos al acabar? | No: la suscripción se **deshabilita** salvo que se pase a pago por uso | Plan Free: no; la cuenta se **suspende** (90 días para pasar a Paid y después se borra). Plan Paid: sí, lo que pase del crédito | No: se cierra la facturación (30 días de gracia y después se borra) salvo que se active la cuenta de pago |
| Límites que nos afectan | — | **AgentCore solo está en el plan Paid** | Desde el **16-11-2026**, el crédito **no cubre Gemini ni Agent Platform** ni los modelos de terceros; durante la prueba no se pueden pedir aumentos de cuota |

## 2. Qué significa para el plan

- **AWS (fase 13):** cuenta en **plan Paid** (AgentCore, Bedrock y Marketplace). Si ya hubo una
  cuenta con este correo, no hay créditos nuevos: se **limpia y se reutiliza**. Cerrarla no ayuda:
  el correo de una cuenta cerrada **no se puede reutilizar** en otra (salvo que se cambie antes de
  cerrarla) y el cierre definitivo tarda 90 días. Presupuesto: 100 $.
- **Azure (fase 14):** si la cuenta es elegible, darse de alta **justo antes de usarla** (los 30 días
  empiezan al registrarse) y pasar a pago por uso para no perder la suscripción. Si no es elegible,
  pago por uso directamente. Presupuesto: 200 $.
- **GCP (fase 15 y pendientes de la pasada GCP):** la prueba de 300 $ ya **no paga Gemini ni Agent
  Platform** (lo que más usamos), y limita las cuotas: conviene activar la cuenta de pago desde el
  principio y controlar el gasto con el presupuesto de 100 $ (`budget.tf` ya mide el gasto bruto, sin
  créditos).
- **La pasada de paridad (P) es local y sin coste:** ninguna cuenta hace falta hasta la fase 13. El
  orden de preparación es el de uso: AWS, después Azure y después GCP.

## 3. Lista por nube

### 3.1 Comprobar el estado (consola, solo Sergio)

| | Azure (portal, cuenta personal) | AWS (consola, usuario root) | GCP (consola, cuenta de Google personal) |
|---|---|---|---|
| Cuenta | **Suscripciones**: cuáles hay y su estado (activa, deshabilitada, expirada) | **Account**: activa o suspendida; plan Free o Paid | **Billing**: cuentas de facturación abiertas o cerradas y si hay prueba activa |
| Pago | **Cost Management + Billing**: métodos de pago y facturas pendientes | **Billing → Payment methods** y **Bills** (cargos de los últimos meses por servicio y región = recursos vivos) | Método de pago de la cuenta de facturación; historial de pagos (decide si hay derecho a prueba) |
| Recursos | Grupos de recursos de cada suscripción | **Resource Explorer** (índice en todas las regiones) | **IAM → Gestionar recursos**: proyectos |
| Identidad | **Entra ID**: registros de aplicaciones y usuarios invitados | MFA del root; claves de acceso del root y de usuarios IAM | Cuentas de servicio y claves JSON de proyectos antiguos |

### 3.2 Limpiar (con confirmación)

- **Azure:** borrar los grupos de recursos que sobren y cancelar las suscripciones viejas; purgar
  de la papelera lo que retenga nombre (AI Services, Content Safety, Key Vault).
- **AWS:** borrar lo que liste Resource Explorer y lo que aparezca en **Bills**; borrar las claves
  de acceso antiguas (y **nunca** crear claves del root); quitar el alias de la cuenta si se va a
  reutilizar.
- **GCP:** borrar los proyectos antiguos (quedan 30 días pendientes de borrado y en ese tiempo
  siguen ocupando su ID) y cerrar las cuentas de facturación que no se usen.

### 3.3 Dejar lista

| | Azure | AWS | GCP |
|---|---|---|---|
| Alta o reactivación | Cuenta gratuita si es elegible; si no, pago por uso | Plan **Paid** | Cuenta de facturación de pago (o prueba + activación) y proyecto `genaipocs-dev-<sufijo>` |
| Seguridad | MFA en la cuenta | **MFA en el root** y el root guardado; trabajo diario con **IAM Identity Center** (usuario con permisos de administrador) | MFA en la cuenta de Google |
| CLI en local | `AZURE_CONFIG_DIR=%USERPROFILE%\.azure-genaipocs` (ver [aviso](#4-aviso-perfil-aislado-de-azure)) | `aws configure sso --profile genaipocs` y `BEDROCK__PROFILE=genaipocs` en el `.env`; región `eu-west-1` | `CLOUDSDK_CONFIG=%USERPROFILE%\.gcloud-genaipocs`; `gcloud auth login` y `gcloud auth application-default login` (ADC, sin claves JSON) |
| Presupuesto | `azurerm_consumption_budget_resource_group` (`infra/terraform/azure/main.tf`) | `aws_budgets_budget` (`infra/terraform/aws/main.tf`) | `google_billing_budget` (`infra/terraform/gcp/budget.tf`) |
| Modelos | Cuota de `gpt-4.1-mini`, `gpt-4.1-nano` y `text-embedding-3-small` en `swedencentral` | Acceso automático en la primera invocación (con permisos de Marketplace y método de pago). Claude **anterior a Opus 4.7** (p. ej., Haiku 4.5) exige antes el **formulario de primer uso** de Anthropic. Invocar un modelo de terceros **acepta su EULA**: pedir confirmación | APIs del proyecto con `terraform apply` (fase 0 de [pendientes](pendientes-cloud.md#fase-0)) |
| Terraform | `subscription_id` nuevo en `terraform.tfvars` | Perfil `genaipocs` | `terraform.tfvars` desde la plantilla |

## 4. Aviso: perfil aislado de Azure

El 27-11-2026, el perfil aislado `%USERPROFILE%\.azure-genaipocs` tenía abierta la sesión de la
**cuenta corporativa** de Sergio, con suscripciones de su empresa y de otros tenants. Antes de usarlo:
`az logout` en ese perfil (solo cierra la sesión local; no cambia nada en la nube) y `az login` con
la cuenta personal. Después, `az account show` debe mostrar solo la suscripción personal. Ningún
inventario ni borrado se lanza sin comprobarlo antes.

## 5. Inventario y borrado desde la CLI

Con la sesión de cada nube abierta (solo lectura; el agente puede ejecutarlos):

```bash
# Azure
export AZURE_CONFIG_DIR="$USERPROFILE/.azure-genaipocs"
az account show -o table
az group list -o table
az resource list -o table
az cognitiveservices account list-deleted -o table      # retienen nombre en la papelera

# AWS (Resource Explorer necesita un índice agregador)
aws sts get-caller-identity --profile genaipocs
aws resource-explorer-2 search --query-string "*" --profile genaipocs
aws resourcegroupstaggingapi get-resources --region eu-west-1 --profile genaipocs

# GCP
export CLOUDSDK_CONFIG="$USERPROFILE/.gcloud-genaipocs"
gcloud config list
gcloud projects list
gcloud billing accounts list
```

El borrado (`az group delete`, `gcloud projects delete`, borrar recursos de AWS) solo se ejecuta
con la confirmación de Sergio, recurso a recurso o grupo a grupo, después de revisar el inventario.

<a id="fuentes"></a>

## Fuentes (consultadas el 27-11-2026)

- Azure: [cuenta gratuita](https://azure.microsoft.com/en-us/pricing/purchase-options/azure-account) y [evitar cargos con la cuenta gratuita](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/avoid-charges-free-account).
- AWS: [capa gratuita](https://aws.amazon.com/free/), [preguntas frecuentes](https://aws.amazon.com/free/free-tier-faqs/), [cerrar una cuenta](https://docs.aws.amazon.com/accounts/latest/reference/manage-acct-closing.html) y [acceso a modelos de Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html).
- GCP: [prueba gratuita](https://docs.cloud.google.com/free/docs/free-cloud-features).
