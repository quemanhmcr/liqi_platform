locals {
  public_ipv4 = var.enable_reserved_public_ip ? one(oci_core_public_ip.reserved[*].ip_address) : oci_core_instance.host.public_ip

  oci_live_v1 = {
    schema_version                = "liqi.infrastructure.oci-live/v1"
    environment                   = local.environment
    classification                = "production-shaped-development"
    git_sha                       = var.source_git_sha
    infrastructure_output_version = "1.2.0"
    region = {
      name                = var.region
      availability_domain = var.availability_domain
    }
    capacity = {
      profile                     = var.capacity_profile
      shape                       = local.capacity.shape
      architecture                = local.capacity.architecture
      target_triple               = local.capacity.target_triple
      ocpus                       = local.capacity.ocpus
      memory_gib                  = local.capacity.memory_gib
      boot_volume_gib             = local.capacity.boot_volume_gib
      data_volume_gib             = local.capacity.data_volume_gib
      combined_storage_gib        = local.capacity.combined_storage_gib
      provider_cpu_ceiling        = 3
      provider_memory_ceiling_gib = 20
      provider_disk_ceiling_gib   = local.capacity.combined_storage_gib
      host_reserve = {
        ocpus            = 1
        memory_gib       = 4
        disk_gib         = 20
        swap_is_capacity = false
      }
      cost_classification      = local.capacity.cost_classification
      temporary                = local.capacity.temporary
      expires_at               = local.capacity.temporary ? var.temporary_e5_expires_at : null
      migration_target_profile = local.capacity.migration_target_profile
    }
    network = {
      vcn_id         = oci_core_vcn.main.id
      edge_subnet_id = oci_core_subnet.edge.id
      host_nsg_id    = oci_core_network_security_group.host.id
      public_ingress = [
        { protocol = "tcp", port = 80, purpose = "http-redirect-and-acme" },
        { protocol = "tcp", port = 443, purpose = "https-edge" }
      ]
      ssh_default_enabled = false
      loopback_services   = ["phoenix-http", "postgresql", "pgbouncer", "otlp-grpc", "otlp-http", "metrics"]
      management_tunnel = {
        mode                    = "outbound-only"
        protocol                = "wireguard-udp"
        peer_cidr               = var.management_wireguard_peer_cidr
        peer_port               = var.management_wireguard_port
        public_ingress_required = false
        preflight_status        = length(trimspace(var.management_plane_evidence_id)) > 0 ? "validated" : "pending-management-plane-preflight"
      }
    }
    host = {
      instance_id                = oci_core_instance.host.id
      image_id                   = var.oracle_linux_image_ocid
      private_ipv4               = oci_core_instance.host.private_ip
      public_ipv4                = local.public_ipv4
      public_ip_mode             = var.enable_reserved_public_ip ? "reserved-approved" : "ephemeral"
      legacy_imds_disabled       = true
      run_command_plugin_enabled = true
    }
    storage = {
      data_volume_id               = oci_core_volume.data.id
      data_volume_preserved        = true
      application_backup_authority = "independent-management-storage"
      artifact_archive_authority   = "independent-management-storage"
      kms_key_id                   = oci_kms_key.main.id
      vault_id                     = oci_kms_vault.main.id
    }
    identity = {
      dynamic_group_id      = oci_identity_dynamic_group.host.id
      instance_principal    = true
      capabilities          = ["vault-secret-bundle-read"]
      user_api_keys_present = false
    }
    state_backend = {
      kind                  = "postgresql-self-hosted"
      remote                = true
      versioned             = false
      locking               = "postgresql-advisory-locks"
      credentials_in_source = false
      compatibility_status  = length(trimspace(var.state_backend_lock_evidence_id)) > 0 ? "validated" : "pending-state-backend-evidence"
    }
    mutation = {
      approval_required  = true
      applied            = var.operation_mode == "approved-apply"
      approval_reference = var.operation_mode == "approved-apply" ? var.apply_approval_reference : null
      plan_sha256        = null
    }
  }
}

output "oci_live_v1" {
  description = "Consumer-ready V1 OCI environment references and guardrail state; never contains plaintext secrets."
  value       = local.oci_live_v1
}

output "host_bootstrap_sha256" {
  description = "Exact rendered cloud-init digest bound to host replacement."
  value       = sha256(var.cloud_init_user_data)
}

output "replacement_impact" {
  description = "Stateful preservation and external recovery authority for replacement planning."
  value = {
    host_replaceable            = true
    data_volume_preserved       = true
    external_recovery_authority = true
    vault_preserved             = true
    public_ip_stable            = var.enable_reserved_public_ip
  }
}
