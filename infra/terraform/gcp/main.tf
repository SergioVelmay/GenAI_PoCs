# Fase 0.G2, cimientos de GCP: APIs del proyecto, presupuesto e identidades sin claves.
# Coste en reposo 0 $: habilitar APIs, presupuestos, cuentas de servicio y pools de
# federación no se factura. Lo que tenga coste por hora llegará detrás de `*_enabled = false`.
#
# El proyecto no lo crea Terraform: se crea a mano con la cuenta de facturación de prueba (ver
# docs/pendientes-cloud.md). Es el equivalente del grupo de recursos de Azure, pero más fuerte:
# facturación, cuotas, APIs e IAM cuelgan de él.

locals {
  name = "${var.project}-${var.environment}"

  labels = {
    project     = var.project
    environment = var.environment
    managed_by  = "terraform"
  }
}

data "google_project" "current" {}

# ---------- APIs ----------
# En AWS y Azure los servicios están "encendidos" por cuenta o suscripción; en GCP, cada API se
# habilita por proyecto. Es también un control de seguridad: lo que no está habilitado no se usa.

resource "google_project_service" "enabled" {
  for_each = var.services

  service = each.value
  # Al destruir el módulo no se apagan: otras piezas (o la consola) pueden depender de ellas, y
  # deshabilitar una API puede borrar recursos que no gestiona Terraform.
  disable_on_destroy         = false
  disable_dependent_services = false
}
