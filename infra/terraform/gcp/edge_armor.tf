# Fase 10.G4: el edge (services/edge_api) en Cloud Run detrás de un Application Load Balancer
# externo con Cloud Armor. Desactivado por defecto (edge_armor_enabled); la imagen, de la fase 15.
#
#   Azure                               AWS                                GCP
#   Front Door / App Gateway + WAF      CloudFront/ALB + AWS WAF           ALB externo + Cloud Armor
#   APIM `rate-limit-by-key`            WAF rate-based rules               `throttle` / `rate_based_ban`
#
# Dos capas de rate limiting, cada una con su papel:
# - **Cloud Armor (perímetro, grueso)**: frena inundaciones antes de que lleguen a Cloud Run (y a
#   la factura): por IP sin token, por token con él y un baneo temporal para quien pasa de 20
#   peticiones/s. Umbrales holgados: detrás de un NAT hay muchos usuarios legítimos.
# - **El edge (aplicación, fino)**: GCRA por usuario y plan en Valkey (10.2, 10.G2), con el
#   `Retry-After` exacto. Cloud Armor no conoce el plan (está dentro del JWT firmado).
#
# Cloud Armor solo funciona en un balanceador (no hay Cloud Armor directo en Cloud Run). Para que
# no se pueda saltar por la URL `run.app`, el servicio solo admite tráfico del balanceador
# (`INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`). La autenticación es el JWT del edge, así que el
# servicio es invocable sin IAM.
#
# Coste: regla de reenvío del balanceador ~0,025 $/h (~18 $/mes), Cloud Armor Standard ~5 $ por
# política y mes + 1 $ por regla y mes + 0,75 $ por millón de peticiones, y Cloud Run por
# petición. Efímero.

locals {
  edge_armor = var.edge_armor_enabled && var.edge_image != "" ? 1 : 0
  edge_https = local.edge_armor == 1 && var.edge_domain != "" ? 1 : 0
  edge_http  = local.edge_armor == 1 && var.edge_domain == "" ? 1 : 0
}

resource "google_cloud_run_v2_service" "edge" {
  count = local.edge_armor

  name                 = "${local.name}-edge"
  location             = var.region
  labels               = local.labels
  deletion_protection  = false
  ingress              = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  invoker_iam_disabled = true # Público tras el balanceador: la identidad es el JWT del edge.

  template {
    service_account                  = google_service_account.app.email
    max_instance_request_concurrency = 64 # EDGE__CAPACITY: lo demás, 503 inmediato.
    timeout                          = "120s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.edge_max_instances
    }

    # Con Memorystore, salida directa a su VPC (Direct VPC egress) para el estado compartido.
    dynamic "vpc_access" {
      for_each = var.memorystore_enabled ? [1] : []
      content {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.scale[0].id
          subnetwork = google_compute_subnetwork.scale[0].id
        }
      }
    }

    containers {
      image   = var.edge_image
      command = ["uvicorn", "edge_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle = true
      }

      env {
        name  = "LLM_PROVIDER"
        value = "gcp"
      }
      env {
        name  = "GCP__PROJECT"
        value = var.project_id
      }
      # Rate limiting y caché compartidos entre instancias: solo con Memorystore; sin él, en
      # memoria por instancia (como una réplica local). Falta montar la CA del servidor
      # (SCALE_REDIS__TLS_CA_FILE) desde Secret Manager: fase 15, con la imagen.
      dynamic "env" {
        for_each = var.memorystore_enabled ? {
          RATE_LIMIT__BACKEND     = "redis"
          SEMANTIC_CACHE__BACKEND = "redis"
          SCALE_REDIS__AUTH       = "gcp_iam"
          SCALE_REDIS__TLS        = "true"
          SCALE_REDIS__HOST       = try(google_memorystore_instance.scale[0].endpoints[0].connections[0].psc_auto_connection[0].ip_address, "")
          SCALE_REDIS__PORT       = tostring(try(google_memorystore_instance.scale[0].endpoints[0].connections[0].psc_auto_connection[0].port, 6379))
        } : {}
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
}

# ---------- Cloud Armor ----------

resource "google_compute_security_policy" "edge" {
  count = local.edge_armor

  name        = "${local.name}-edge"
  description = "Perímetro del edge: inundaciones por IP o por token antes de Cloud Run."
  type        = "CLOUD_ARMOR"

  # 1. Baneo temporal: quien pase de N peticiones/min desde una IP queda fuera unos minutos
  #    (los dos "abusadores" a 20 peticiones/s de la prueba de carga de la 10.7).
  rule {
    priority    = 100
    action      = "rate_based_ban"
    description = "Baneo temporal por IP ante una inundación."
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action   = "allow"
      exceed_action    = "deny(429)"
      enforce_on_key   = "IP"
      ban_duration_sec = var.edge_ban_seconds
      rate_limit_threshold {
        count        = var.edge_ban_per_minute
        interval_sec = 60
      }
    }
  }

  # 2. Sin token (anónimos): por IP. Holgado: el límite fino por plan (5/min) lo pone el edge.
  rule {
    priority    = 200
    action      = "throttle"
    description = "Anónimos: tope por IP."
    match {
      expr {
        expression = "!has(request.headers['authorization'])"
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = var.edge_anonymous_per_minute
        interval_sec = 60
      }
    }
  }

  # 3. Con token: por token (la clave es la cabecera; Cloud Armor no valida el JWT, el edge sí).
  rule {
    priority    = 300
    action      = "throttle"
    description = "Con token: tope por cabecera Authorization."
    match {
      expr {
        expression = "has(request.headers['authorization'])"
      }
    }
    rate_limit_options {
      conform_action      = "allow"
      exceed_action       = "deny(429)"
      enforce_on_key      = "HTTP_HEADER"
      enforce_on_key_name = "Authorization"
      rate_limit_threshold {
        count        = var.edge_authenticated_per_minute
        interval_sec = 60
      }
    }
  }

  rule {
    priority    = 2147483647
    action      = "allow"
    description = "Por defecto: al edge, que decide por plan."
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}

# ---------- Balanceador ----------

resource "google_compute_region_network_endpoint_group" "edge" {
  count = local.edge_armor

  name                  = "${local.name}-edge"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.edge[0].name
  }
}

resource "google_compute_backend_service" "edge" {
  count = local.edge_armor

  name                  = "${local.name}-edge"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  security_policy       = google_compute_security_policy.edge[0].id

  backend {
    group = google_compute_region_network_endpoint_group.edge[0].id
  }

  log_config {
    enable      = true
    sample_rate = 1.0 # Las denegaciones de Cloud Armor se ven en los logs del balanceador.
  }
}

resource "google_compute_url_map" "edge" {
  count = local.edge_armor

  name            = "${local.name}-edge"
  default_service = google_compute_backend_service.edge[0].id
}

resource "google_compute_global_address" "edge" {
  count = local.edge_armor

  name = "${local.name}-edge"
}

# HTTPS con certificado gestionado si hay dominio; si no, HTTP en el 80 solo para validar (p. ej.
# antes de apuntar el DNS). El SSE del edge funciona en los dos.
resource "google_compute_managed_ssl_certificate" "edge" {
  count = local.edge_https

  name = "${local.name}-edge"
  managed {
    domains = [var.edge_domain]
  }
}

resource "google_compute_target_https_proxy" "edge" {
  count = local.edge_https

  name             = "${local.name}-edge"
  url_map          = google_compute_url_map.edge[0].id
  ssl_certificates = [google_compute_managed_ssl_certificate.edge[0].id]
}

resource "google_compute_target_http_proxy" "edge" {
  count = local.edge_http

  name    = "${local.name}-edge"
  url_map = google_compute_url_map.edge[0].id
}

resource "google_compute_global_forwarding_rule" "edge" {
  count = local.edge_armor

  name                  = "${local.name}-edge"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.edge[0].id
  port_range            = local.edge_https == 1 ? "443" : "80"
  # El único proxy que exista (HTTPS con dominio, HTTP sin él).
  target = one(concat(
    google_compute_target_https_proxy.edge[*].id,
    google_compute_target_http_proxy.edge[*].id,
  ))
  labels = local.labels
}
