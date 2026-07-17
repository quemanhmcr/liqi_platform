locals {
  host_contract = {
    schema_version                = "liqi.platform.oci-host/v0"
    infrastructure_output_version = var.infrastructure_output_version
    environment                   = var.environment
    region = {
      name                = var.region
      availability_domain = var.availability_domain
    }
    capacity_profile = {
      name                = var.capacity_profile.name
      shape               = var.capacity_profile.shape
      architecture        = var.capacity_profile.architecture
      ocpus               = var.capacity_profile.ocpus
      memory_gb           = var.capacity_profile.memory_gb
      boot_volume_gb      = var.capacity_profile.boot_volume_gb
      data_volume_gb      = var.capacity_profile.data_volume_gb
      cost_classification = var.capacity_profile.cost_classification
    }
    host = {
      instance_id  = oci_core_instance.host.id
      display_name = oci_core_instance.host.display_name
      image_id     = var.oracle_linux_image_ocid
      os = {
        family        = "Oracle Linux"
        major_version = "9"
        architecture  = var.capacity_profile.architecture
      }
      addresses = {
        private_ipv4              = oci_core_instance.host.private_ip
        public_ipv4               = oci_core_instance.host.public_ip
        private_address_semantics = "service-and-approved-administration"
        public_address_semantics  = "edge-only"
      }
    }
    identities = {
      runtime_group = {
        name = "liqi"
        gid  = 2200
      }
      services = [
        {
          service     = "liqi-api"
          user        = "liqi-api"
          uid         = 2210
          group       = "liqi"
          login_shell = "/sbin/nologin"
        },
        {
          service     = "liqi-realtime"
          user        = "liqi-realtime"
          uid         = 2211
          group       = "liqi"
          login_shell = "/sbin/nologin"
        },
        {
          service     = "liqi-worker"
          user        = "liqi-worker"
          uid         = 2212
          group       = "liqi"
          login_shell = "/sbin/nologin"
        }
      ]
      transport_user = {
        name          = "opc"
        purpose       = "approved-administration-and-release-transport"
        runs_services = false
      }
    }
    directories           = local.host_directories
    ports                 = local.host_ports
    runtime_configuration = local.runtime_configuration
    execution_control     = local.execution_control
    object_storage_references = [
      {
        purpose             = "postgresql-backups"
        region              = var.region
        namespace           = data.oci_objectstorage_namespace.current.namespace
        bucket_name         = oci_objectstorage_bucket.backups.name
        prefix              = "postgresql/"
        uri                 = "oci://${data.oci_objectstorage_namespace.current.namespace}/${oci_objectstorage_bucket.backups.name}/postgresql/"
        access_path         = "service-gateway"
        cost_classification = "always-free-safe"
      }
    ]
    secret_reference_format = {
      provider             = "oci-vault"
      uri_template         = "oci-vault://<secret-ocid>@<version-or-CURRENT>"
      materialization_root = "/run/liqi/secrets"
      plaintext_in_output  = false
    }
    service_gateway = {
      enabled        = true
      service        = local.object_storage_service.name
      private_access = true
    }
    instance_principal = {
      enabled = true
      capabilities = concat(
        [
          "object-storage:read-buckets-in-environment-compartment",
          "object-storage:list-read-create-objects-in-designated-backup-bucket"
        ],
        length(var.vault_secret_ocids) > 0 ? ["oci-vault:read-designated-secret-bundles"] : []
      )
      user_credentials_present = false
    }
    readiness = {
      file           = "/run/liqi/host-ready.json"
      schema_version = "liqi.platform.host-readiness/v0"
      atomic_write   = true
      ready_status   = "ready"
      required_checks = [
        "runtime-identities",
        "runtime-directories",
        "data-volume-mounted",
        "swap-disabled",
        "selinux-enforcing",
        "firewall-policy",
        "ssh-root-disabled",
        "ssh-password-auth-disabled",
        "legacy-imds-disabled",
        "capacity-controls"
      ]
    }
    release_target = {
      transport_user              = "opc"
      staging_path                = "/var/tmp/liqi/releases"
      deployment_path             = "/opt/liqi/releases"
      current_symlink             = "/opt/liqi/current"
      transport                   = "ssh-over-approved-admin-path"
      installation_semantics      = "upload-to-staging-then-root-owned-atomic-install"
      privileged_install_required = true
      admin_path_default_enabled  = false
    }
    replacement = {
      host_replaceable                     = true
      data_volume_preserved                = true
      destruction_acknowledgement_required = true
      stateful_resource_kinds              = ["block-volume", "object-storage-bucket"]
    }
  }
}

output "oci_host_v0" {
  description = "Complete consumer-ready OCI host V0 contract; contains identifiers and references, never plaintext secrets."
  value       = local.host_contract
}

output "infrastructure_output_version" {
  description = "Compatibility alias for consumers that gate on output version before decoding oci_host_v0."
  value       = var.infrastructure_output_version
}

output "replacement_impact" {
  description = "Replacement behavior visible to release and recovery tooling."
  value = {
    host_replacement_changes_public_ip = true
    data_volume_preserved              = true
    backup_bucket_preserved            = true
    explicit_stateful_destroy_required = true
  }
}
