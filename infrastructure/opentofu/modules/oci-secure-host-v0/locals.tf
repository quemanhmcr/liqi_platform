locals {
  prefix = "liqi-${var.environment}"

  common_tags = {
    "liqi-project"     = "liqi-platform"
    "liqi-environment" = var.environment
    "liqi-owner"       = var.owner
    "liqi-managed-by"  = "opentofu"
  }

  always_free_tags = merge(local.common_tags, {
    "liqi-cost-classification" = "always-free-safe"
  })

  compute_tags = merge(local.common_tags, {
    "liqi-cost-classification" = var.capacity_profile.cost_classification
    "liqi-capacity-profile"    = var.capacity_profile.name
  })

  stateful_tags = merge(local.always_free_tags, {
    "liqi-stateful" = "true"
  })

  edge_ingress_ports = merge(
    var.enable_http_redirect ? { http = 80 } : {},
    var.enable_https_edge ? { https = 443 } : {}
  )

  object_storage_services = [
    for service in data.oci_core_services.regional.services : service
    if can(regex("Object Storage", service.name)) && !can(regex("All .* Services", service.name))
  ]

  object_storage_service = one(local.object_storage_services)
  backup_bucket_name     = "${local.prefix}-backups"

  host_ports = [
    {
      name            = "edge-http"
      protocol        = "tcp"
      port            = 80
      bind_scope      = "public-edge"
      exposure        = "public-redirect-only"
      default_enabled = var.enable_http_redirect
    },
    {
      name            = "edge-https"
      protocol        = "tcp"
      port            = 443
      bind_scope      = "public-edge"
      exposure        = "public-edge"
      default_enabled = var.enable_https_edge
    },
    {
      name            = "administration-ssh"
      protocol        = "tcp"
      port            = 22
      bind_scope      = "private-address"
      exposure        = "admin-allowlist-only"
      default_enabled = var.enable_admin_ssh
    },
    {
      name            = "postgresql"
      protocol        = "tcp"
      port            = 5432
      bind_scope      = "loopback"
      exposure        = "host-internal-only"
      default_enabled = false
    },
    {
      name            = "pgbouncer"
      protocol        = "tcp"
      port            = 6432
      bind_scope      = "loopback"
      exposure        = "host-internal-only"
      default_enabled = false
    },
    {
      name            = "liqi-api"
      protocol        = "tcp"
      port            = 8080
      bind_scope      = "loopback"
      exposure        = "host-internal-only"
      default_enabled = false
    },
    {
      name            = "liqi-realtime"
      protocol        = "tcp"
      port            = 8081
      bind_scope      = "loopback"
      exposure        = "host-internal-only"
      default_enabled = false
    },
    {
      name            = "liqi-worker-admin"
      protocol        = "tcp"
      port            = 8082
      bind_scope      = "loopback"
      exposure        = "host-internal-only"
      default_enabled = false
    },
    {
      name            = "otel-otlp-grpc"
      protocol        = "tcp"
      port            = 4317
      bind_scope      = "loopback"
      exposure        = "host-internal-only"
      default_enabled = false
    },
    {
      name            = "otel-otlp-http"
      protocol        = "tcp"
      port            = 4318
      bind_scope      = "loopback"
      exposure        = "host-internal-only"
      default_enabled = false
    }
  ]

  host_directories = [
    {
      purpose  = "application-releases"
      path     = "/opt/liqi/releases"
      owner    = "root"
      group    = "liqi"
      mode     = "0750"
      storage  = "boot-volume"
      consumer = "senior-4"
    },
    {
      purpose  = "current-release-symlink-parent"
      path     = "/opt/liqi"
      owner    = "root"
      group    = "liqi"
      mode     = "0750"
      storage  = "boot-volume"
      consumer = "shared"
    },
    {
      purpose  = "runtime-configuration"
      path     = "/etc/liqi"
      owner    = "root"
      group    = "liqi"
      mode     = "0750"
      storage  = "boot-volume"
      consumer = "senior-3"
    },
    {
      purpose  = "secrets-materialization"
      path     = "/run/liqi/secrets"
      owner    = "root"
      group    = "liqi"
      mode     = "0750"
      storage  = "tmpfs"
      consumer = "shared"
    },
    {
      purpose  = "postgresql-data"
      path     = "/var/lib/liqi/postgresql/data"
      owner    = "postgres"
      group    = "postgres"
      mode     = "0700"
      storage  = "data-volume"
      consumer = "senior-2"
    },
    {
      purpose  = "postgresql-backup-staging"
      path     = "/var/lib/liqi/postgresql/backup-staging"
      owner    = "postgres"
      group    = "postgres"
      mode     = "0700"
      storage  = "data-volume"
      consumer = "senior-2"
    },
    {
      purpose  = "platform-logs"
      path     = "/var/log/liqi"
      owner    = "root"
      group    = "liqi"
      mode     = "0750"
      storage  = "boot-volume"
      consumer = "senior-4"
    },
    {
      purpose  = "temporary-data"
      path     = "/var/tmp/liqi"
      owner    = "root"
      group    = "liqi"
      mode     = "0750"
      storage  = "boot-volume"
      consumer = "shared"
    }
  ]
}
