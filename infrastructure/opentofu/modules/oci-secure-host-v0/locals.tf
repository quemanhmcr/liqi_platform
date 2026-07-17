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
      mode     = "0710"
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

  runtime_configuration = {
    environment_variable = "LIQI_CONFIG_PATH"
    cli_argument         = "--config"
    maximum_file_bytes   = 1048576
    files = [
      {
        service = "liqi-api"
        path    = "/etc/liqi/api.json"
        owner   = "root"
        group   = "liqi"
        mode    = "0640"
      },
      {
        service = "liqi-realtime"
        path    = "/etc/liqi/realtime.json"
        owner   = "root"
        group   = "liqi"
        mode    = "0640"
      },
      {
        service = "liqi-worker"
        path    = "/etc/liqi/worker.json"
        owner   = "root"
        group   = "liqi"
        mode    = "0640"
      }
    ]
  }

  execution_control = {
    manager                  = "systemd"
    provider_budget_contract = "contracts/platform/infrastructure-capacity-budget-v0.json"
    parent = {
      slice               = "liqi-platform.slice"
      parent              = null
      cpu_quota_percent   = 300
      memory_max_mib      = 20480
      memory_swap_max_mib = 0
    }
    runtime = {
      slice               = "liqi-platform-runtime.slice"
      parent              = "liqi-platform.slice"
      cpu_quota_percent   = 145
      memory_max_mib      = 7168
      memory_swap_max_mib = 0
    }
    database = {
      slice               = "liqi-platform-database.slice"
      parent              = "liqi-platform.slice"
      cpu_quota_percent   = 120
      memory_max_mib      = 7936
      memory_swap_max_mib = 0
    }
    operations = {
      slice               = "liqi-platform-operations.slice"
      parent              = "liqi-platform.slice"
      cpu_quota_percent   = 25
      memory_max_mib      = 1024
      memory_swap_max_mib = 0
    }
    edge = {
      slice               = "liqi-platform-edge.slice"
      parent              = "liqi-platform.slice"
      cpu_quota_percent   = 10
      memory_max_mib      = 256
      memory_swap_max_mib = 0
    }
    services = [
      {
        service             = "liqi-api"
        unit                = "liqi-api.service"
        slice               = "liqi-platform-runtime.slice"
        cpu_quota_percent   = 45
        memory_max_mib      = 2048
        memory_swap_max_mib = 0
        config_path         = "/etc/liqi/api.json"
      },
      {
        service             = "liqi-realtime"
        unit                = "liqi-realtime.service"
        slice               = "liqi-platform-runtime.slice"
        cpu_quota_percent   = 65
        memory_max_mib      = 3072
        memory_swap_max_mib = 0
        config_path         = "/etc/liqi/realtime.json"
      },
      {
        service             = "liqi-worker"
        unit                = "liqi-worker.service"
        slice               = "liqi-platform-runtime.slice"
        cpu_quota_percent   = 35
        memory_max_mib      = 2048
        memory_swap_max_mib = 0
        config_path         = "/etc/liqi/worker.json"
      }
    ]
    cpu_aggregation = {
      hard_ceiling_semantics       = "additive-admission-with-parent-enforcement"
      hard_ceiling_limit_ocpu      = 3
      steady_state_limit_ocpu      = 3
      host_scheduling_reserve_ocpu = 1
      parent_enforcement           = "liqi-platform.slice"
    }
    memory_aggregation = {
      hard_limits_additive = true
      hard_limit_mib       = 20480
      host_reserve_mib     = 4096
      swap_is_capacity     = false
    }
    container_policy     = "inner-limit-must-not-exceed-systemd-outer-limit"
    assignment_semantics = "provider-base-units-must-attach-to-published-slice"
  }

}
