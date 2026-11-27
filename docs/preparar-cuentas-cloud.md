# Preparar las cuentas de Azure, AWS y GCP

Checkpoint G→13 B.3 y B.4 (27-11-2026). Antes de las fases 13 (AWS), 14 (Azure) y 15 (GCP), y de
las sesiones de [pendientes-cloud.md](pendientes-cloud.md): cada nube con **una cuenta personal
limpia**, la tarjeta puesta, presupuesto con alertas e identidad sin claves. Las condiciones de la
tabla se comprobaron en las páginas oficiales el 27-11-2026 ([fuentes](#fuentes)); cambian a menudo,
así que se repasan antes de cada alta. El estado real de las cuentas de Sergio está en el
[inventario](#6-inventario-del-27-11-2026).

Reglas que aplican (AGENTS.md): pedir confirmación antes de crear o borrar recursos, de cualquier
acción con coste y de aceptar licencias; **nunca** tocar suscripciones, cuentas o proyectos
corporativos; ninguna credencial en el repo; comprobar la cuenta de cada perfil **en un paso
aparte** antes de cualquier inventario, `plan` o borrado ([lección](lecciones-aprendidas.md)).

## 1. Tarjeta y créditos de prueba

| | Azure | AWS | GCP |
|---|---|---|---|
| ¿Tarjeta? | **Obligatoria** (crédito o débito, no prepago): verificación de identidad, retención de 1 $ que se devuelve | Puede pedirla al darse de alta (retención de 1 $ de 3 a 5 días). El plan Paid y los modelos de terceros de Bedrock (vía Marketplace) exigen método de pago válido | **Obligatoria**: retención de 0–1 $ |
| Crédito | 200 $ durante **30 días** (más servicios gratis 12 meses) | Hasta 200 $ (100 al darse de alta + 100 por actividades), 6 meses | 300 $ durante **90 días** |
| ¿Quién lo recibe? | Solo clientes nuevos de Azure; uno por cliente | Solo quien **nunca ha tenido** una cuenta de AWS | Quien nunca ha pagado en GCP, Maps o Firebase y no ha usado la prueba |
| ¿Cobran solos al acabar? | No: la suscripción se **deshabilita** salvo que se pase a pago por uso | Plan Free: no; la cuenta se **suspende** (90 días para pasar a Paid y después se borra). Plan Paid: sí, lo que pase del crédito | No: se cierra la facturación (30 días de gracia y después se borra) salvo que se active la cuenta de pago |
| Límites que nos afectan | — | Ver [dos tipos de cuenta](#11-aws-dos-tipos-de-cuenta) | Desde el **16-11-2026**, el crédito **no cubre Gemini ni Agent Platform** ni los modelos de terceros; durante la prueba no se pueden pedir aumentos de cuota |

### 1.1 AWS: dos tipos de cuenta

Desde 2026, AWS tiene dos formas de darse de alta, y los pasos cambian por completo:

| | Nueva experiencia (*Sign up for AWS (new)*) | Clásica (*Sign up for AWS (advanced)*) |
|---|---|---|
| Identidad | AWS Builder ID (Google, GitHub, Apple, Amazon o correo); **sin usuario root** | Usuario root + IAM Identity Center |
| CLI | `aws login --region <región del proyecto> --profile <perfil>` (AWS CLI 2.32+) | `aws configure sso --profile <perfil>` |
| Organización | **Proyectos** (cada uno es una cuenta) en AWS Settings | AWS Organizations y cuentas miembro |
| Regiones | **Una por proyecto**, asignada por el país de la dirección (`us-east-2`, `eu-north-1` o `ap-southeast-2`; España → `eu-north-1`). Sin funciones multirregión. Bedrock sí admite llamadas sin recursos en otras regiones | Todas |
| Servicios | Lista cerrada: Bedrock, AgentCore, S3, SQS, SNS, EventBridge, Step Functions, Lambda, ECS, EKS, ECR, Aurora, RDS, ElastiCache, API Gateway, WAF, Cognito, Secrets Manager, CloudWatch, X-Ray, Budgets... Textract y Neptune, solo en Paid. **No están** IAM Identity Center, Organizations, Comprehend ni Control Tower | Todos |
| Control de gasto | **Límite de gasto** por proyecto (plan Paid) | Presupuestos y alertas |
| Paso a clásica | **Activar las funciones avanzadas**: exige plan Paid, **no tiene vuelta atrás**, da todas las regiones y servicios, Organizations e Identity Center, y quita el límite de gasto | — |

## 2. Qué significa para el plan

- **AWS (fase 13):** la cuenta de Sergio es de la nueva experiencia, plan **Free** con crédito y en
  `eu-north-1` ([inventario](#6-inventario-del-27-11-2026)). **Decisión (27-11-2026):** no se activa
  nada ahora; al iniciar la fase 13 se comprueba qué hay en `eu-north-1` (Claude Haiku 4.5,
  Knowledge Bases, S3 Vectors, AgentCore) y si Terraform acepta las credenciales de `aws login`, y
  con eso se decide entre quedarse en el proyecto (crédito y límite de gasto, una región) o activar
  las funciones avanzadas (todo AWS, irreversible). El repo usa hoy `eu-west-1`.
- **Azure (fase 14):** la prueba gratuita ya se usó: la suscripción se pasa a **pago por uso** (o se
  crea una nueva de pago por uso). Presupuesto: 200 $ de dinero real, con el presupuesto y las
  alertas de Terraform.
- **GCP (fase 15 y pendientes de la pasada GCP):** la prueba de 300 $ ya no paga Gemini ni Agent
  Platform, y la de Sergio probablemente ya se usó: se reabre la cuenta de facturación (o se crea
  otra) con tarjeta y se controla el gasto con el presupuesto de 100 $ (`budget.tf` ya mide el gasto
  bruto, sin créditos).
- **La pasada de paridad (P) es local y sin coste:** ninguna cuenta hace falta hasta la fase 13. El
  orden de preparación es el de uso: AWS, después Azure y después GCP.

## 3. Lista por nube

### 3.1 Comprobar el estado

| | Azure (cuenta personal) | AWS (nueva experiencia) | GCP (cuenta de Google personal) |
|---|---|---|---|
| Cuenta | Suscripciones y su estado (activa, deshabilitada, expirada) y su oferta (`quotaId`) | Plan (Free o Paid), crédito restante y fecha de fin (`aws freetier get-account-plan-state`); región del proyecto en AWS Settings | Cuentas de facturación (abiertas o cerradas) y si hay prueba activa |
| Pago | **Cost Management + Billing**: métodos de pago y facturas pendientes | AWS Settings: método de pago y límite de gasto | Método de pago de la cuenta de facturación |
| Recursos | Grupos de recursos de cada suscripción; papelera (AI Services, Key Vault) | Recursos de la región del proyecto (S3, EC2, Lambda, RDS, ECS, ECR, SQS, SNS, CloudFormation, Bedrock) y roles de IAM | Proyectos, su facturación y sus APIs activas |
| Identidad | **Entra ID**: registros de aplicaciones y usuarios invitados | Miembros del proyecto en AWS Settings | Cuentas de servicio y claves JSON de proyectos antiguos |

### 3.2 Limpiar (con confirmación)

- **Azure:** borrar los grupos de recursos que sobren y cancelar las suscripciones viejas; purgar de
  la papelera lo que retenga nombre (AI Services, Content Safety, Key Vault). Una suscripción
  **deshabilitada no admite borrados**: hay que reactivarla antes (o esperar a que Microsoft la
  limpie).
- **AWS:** borrar lo que aparezca en el inventario o en la factura. En la nueva experiencia no hay
  root ni claves de root; en una cuenta clásica, activar MFA en el root y borrar sus claves.
- **GCP:** borrar los proyectos antiguos (quedan 30 días pendientes de borrado y en ese tiempo
  siguen ocupando su ID) y cerrar las cuentas de facturación que no se usen.

### 3.3 Dejar lista

| | Azure | AWS | GCP |
|---|---|---|---|
| Alta o reactivación | Pago por uso (la prueba ya se usó) | Según la decisión de la fase 13: plan Paid en el proyecto, o funciones avanzadas | Reabrir la facturación (o crear otra) y proyecto `genaipocs-dev-<sufijo>` |
| Seguridad | MFA en la cuenta | MFA en el Builder ID | MFA en la cuenta de Google |
| CLI en local | `AZURE_CONFIG_DIR=%USERPROFILE%\.azure-genaipocs` y `az login --allow-no-subscriptions` con la cuenta personal | `aws login --region eu-north-1 --profile genaipocs` y `BEDROCK__PROFILE=genaipocs` en el `.env` | `CLOUDSDK_CONFIG=%USERPROFILE%\.gcloud-genaipocs`; `gcloud auth login` y `gcloud auth application-default login` (ADC, sin claves JSON). En Git Bash, `gcloud.cmd` |
| Presupuesto | `azurerm_consumption_budget_resource_group` (`infra/terraform/azure/main.tf`) | Límite de gasto del proyecto (plan Paid) o `aws_budgets_budget` (`infra/terraform/aws/main.tf`) | `google_billing_budget` (`infra/terraform/gcp/budget.tf`) |
| Modelos | Cuota de `gpt-4.1-mini`, `gpt-4.1-nano` y `text-embedding-3-small` en `swedencentral` | Acceso automático en la primera invocación (con permisos de Marketplace y método de pago). Claude **anterior a Opus 4.7** (p. ej., Haiku 4.5) exige antes el **formulario de primer uso** de Anthropic. Invocar un modelo de terceros **acepta su EULA**: pedir confirmación | APIs del proyecto con `terraform apply` (fase 0 de [pendientes](pendientes-cloud.md#fase-0)) |
| Terraform | `subscription_id` nuevo en `terraform.tfvars` | Perfil `genaipocs` y región según la decisión de la fase 13 | `terraform.tfvars` desde la plantilla |

## 4. Iniciar sesión en los perfiles aislados

Desde el prompt de Claude Code, con `!` delante (fuera del chat, sin `!` y con la sintaxis de la
shell: `set VAR=...` en `cmd`, `$env:VAR = "..."` en PowerShell). Si el terminal se abrió antes de
instalar las CLI, añadir antes su carpeta al `PATH`.

```bash
export AZURE_CONFIG_DIR="$USERPROFILE/.azure-genaipocs"; az logout; az login --allow-no-subscriptions
export PATH="$PATH:$LOCALAPPDATA/Google/Cloud SDK/google-cloud-sdk/bin"; export CLOUDSDK_CONFIG="$USERPROFILE/.gcloud-genaipocs"; gcloud.cmd auth login
export PATH="$PATH:$LOCALAPPDATA/Programs/Amazon/AWSCLIV2"; aws login --region eu-north-1 --profile genaipocs
```

El 27-11-2026, el perfil aislado de Azure tenía abierta la sesión de la **cuenta corporativa** de
Sergio: `az logout` solo cierra la sesión local, no cambia nada en la nube.

## 5. Inventario desde la CLI

Solo lectura. **Primero** se comprueba la cuenta y se para si no es la personal; después, el resto:

```bash
# Azure
export AZURE_CONFIG_DIR="$USERPROFILE/.azure-genaipocs"
u=$(az account show --query user.name -o tsv); echo "$u"   # parar si no es la personal
az account list --all -o table
az group list --subscription <id> -o table
az resource list --subscription <id> -o table
az cognitiveservices account list-deleted --subscription <id> -o table
az keyvault list-deleted --subscription <id> -o table

# GCP
export CLOUDSDK_CONFIG="$USERPROFILE/.gcloud-genaipocs"
gcloud.cmd config get-value account                         # parar si no es la personal
gcloud.cmd projects list
gcloud.cmd billing accounts list
gcloud.cmd billing projects describe <proyecto>
gcloud.cmd services list --enabled --project <proyecto>

# AWS (la CLI usa --output, no -o)
aws sts get-caller-identity --profile genaipocs               # parar si no es la cuenta esperada
aws freetier get-account-plan-state --profile genaipocs --region us-east-1
aws s3api list-buckets --profile genaipocs
aws <servicio> <list-...> --profile genaipocs --region eu-north-1 --output text
```

El borrado (`az group delete`, `gcloud projects delete`, borrar recursos de AWS) solo se ejecuta
con la confirmación de Sergio, después de revisar el inventario.

## 6. Inventario del 27-11-2026

Sin identificadores de cuenta ni correos (el repo es público).

| | Azure | AWS | GCP |
|---|---|---|---|
| Cuenta | Prueba gratuita **caducada**: suscripción `Disabled`, oferta `FreeTrial`, con límite de gasto | Nueva experiencia, un proyecto en `eu-north-1`, creado en octubre de 2026. Plan **Free activo** con **120 $** de crédito hasta el **04-04-2027** | Cuenta de facturación **cerrada** (EUR), de 2018 |
| Recursos | Un grupo de recursos en `swedencentral` con lo que crea el inicio rápido de Foundry (recurso y proyecto, Log Analytics, Application Insights, grupo de acciones). Papelera vacía | **Ninguno**: solo los roles que gestiona AWS | Dos proyectos antiguos sin facturación (APIs de Maps, de 2018; Firebase, de 2019) |
| Coste | Ninguno (deshabilitada) | Ninguno | Ninguno |
| Acción | Ninguna hasta la fase 14: deshabilitada no admite borrados | Ninguna; decisión de región y tipo de cuenta al iniciar la fase 13 | Los dos proyectos, **borrados** con confirmación de Sergio (`DELETE_REQUESTED`; desaparecen a los 30 días) |

<a id="fuentes"></a>

## Fuentes (consultadas el 27-11-2026)

- Azure: [cuenta gratuita](https://azure.microsoft.com/en-us/pricing/purchase-options/azure-account) y [evitar cargos con la cuenta gratuita](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/avoid-charges-free-account).
- AWS: [capa gratuita](https://aws.amazon.com/free/), [preguntas frecuentes](https://aws.amazon.com/free/free-tier-faqs/), [cerrar una cuenta](https://docs.aws.amazon.com/accounts/latest/reference/manage-acct-closing.html), [acceso a modelos de Bedrock](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html), [nueva experiencia](https://docs.aws.amazon.com/accounts/latest/reference/sign-in-new.html), [regiones de los proyectos](https://docs.aws.amazon.com/accounts/latest/reference/project-regions.html), [servicios admitidos](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html) y [funciones avanzadas](https://docs.aws.amazon.com/accounts/latest/reference/activate-advanced-features.html).
- GCP: [prueba gratuita](https://docs.cloud.google.com/free/docs/free-cloud-features).
