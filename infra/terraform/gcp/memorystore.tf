# Fase 10.G2: Memorystore for Valkey para el estado compartido del edge (GCRA del rate limiting y
# caché semántica con búsqueda vectorial). Equivale a Azure Managed Redis y a ElastiCache con
# Valkey (sin Terraform aún en esas dos nubes: pendientes de la fase 10).
#
#   Azure                          AWS                              GCP
#   Azure Managed Redis + Entra    ElastiCache (Valkey) + IAM auth  Memorystore for Valkey + IAM auth
#   Private Endpoint               subred de la VPC                 Private Service Connect (PSC)
#
# Lo que usa el código (comprobado en la 10.G2 contra valkey-bundle 9.0.8, el mismo motor):
# - búsqueda vectorial (FT.CREATE/FT.SEARCH con VECTOR y TAG; sin TEXT ni SORTBY), GA desde el
#   22-04-2025 y sin activar nada en la instancia;
# - EVAL con `TIME` para el GCRA; en modo clúster, multiclave solo en un slot (hash tag
#   `{genaipocs-rl}`). Aquí CLUSTER_DISABLED: un shard, cualquier cliente, y la caché no
#   necesita hash tag (Valkey no lo admite en el PREFIX del índice).
#
# Coste: SHARED_CORE_NANO (1,4 GB, sin SLA) se cobra por nodo y hora mientras exista (del orden
# de 0,03 $/h según terceros; el precio oficial en europe-west4 está por confirmar). Efímero, como
# AI Search: memorystore_enabled = true solo durante la sesión de validación.
#
# Red: Memorystore no tiene IP pública. Se conecta por PSC: una política de conexión de servicio
# (`gcp-memorystore`) en la subred deja que la instancia cree su endpoint. El edge en Cloud Run
# llegará con Direct VPC egress a esta subred (fase 15); en la validación, desde una VM o Cloud
# Shell en la VPC.
#
# Autenticación: IAM_AUTH (`SCALE_REDIS__AUTH=gcp_iam`): el token de acceso de la identidad es la
# contraseña; la cuenta de la aplicación recibe roles/memorystore.dbConnectionUser. TLS con
# SERVER_AUTHENTICATION (`SCALE_REDIS__TLS=true` y la CA de la salida memorystore_server_ca).

locals {
  memorystore = var.memorystore_enabled ? 1 : 0
}

resource "google_compute_network" "scale" {
  count = local.memorystore

  name                    = "${local.name}-scale"
  auto_create_subnetworks = false
  description             = "VPC del estado compartido del edge (Memorystore por PSC)."

  depends_on = [google_project_service.enabled]
}

resource "google_compute_subnetwork" "scale" {
  count = local.memorystore

  name          = "${local.name}-scale-${var.region}"
  network       = google_compute_network.scale[0].id
  region        = var.region
  ip_cidr_range = var.memorystore_subnet_cidr
  # Las APIs de Google desde la subred sin IP pública (p. ej. el token de IAM desde una VM).
  private_ip_google_access = true
}

resource "google_network_connectivity_service_connection_policy" "memorystore" {
  count = local.memorystore

  name          = "${local.name}-memorystore"
  location      = var.region
  service_class = "gcp-memorystore"
  network       = google_compute_network.scale[0].id
  description   = "Deja que Memorystore cree su endpoint PSC en la subred del edge."

  psc_config {
    subnetworks = [google_compute_subnetwork.scale[0].id]
  }
}

resource "google_memorystore_instance" "scale" {
  count = local.memorystore

  instance_id    = "${local.name}-scale"
  location       = var.region
  engine_version = var.memorystore_engine_version
  node_type      = var.memorystore_node_type
  mode           = "CLUSTER_DISABLED"
  shard_count    = 1
  replica_count  = 0

  authorization_mode      = "IAM_AUTH"
  transit_encryption_mode = "SERVER_AUTHENTICATION"

  # Como el redis-scale local: sin persistencia y, si se llena, fuera lo que tiene TTL (todo).
  engine_configs = {
    "maxmemory-policy" = "volatile-lru"
  }

  zone_distribution_config {
    mode = "SINGLE_ZONE"
    zone = "${var.region}-${var.memorystore_zone_suffix}"
  }

  desired_auto_created_endpoints {
    network    = google_compute_network.scale[0].id
    project_id = var.project_id
  }

  # Efímera: se destruye al terminar la sesión.
  deletion_protection_enabled = false
  labels                      = local.labels

  depends_on = [google_network_connectivity_service_connection_policy.memorystore]
}

# La identidad de la aplicación (edge y gateway) se conecta con su token de acceso.
resource "google_project_iam_member" "app_memorystore" {
  count = local.memorystore

  project = var.project_id
  role    = "roles/memorystore.dbConnectionUser"
  member  = google_service_account.app.member
}
