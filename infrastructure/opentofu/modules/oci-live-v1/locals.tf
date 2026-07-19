locals {
  environment = "v1-live"
  prefix      = "liqi-${local.environment}"

  capacity_profiles = {
    a1-target = {
      shape                    = "VM.Standard.A1.Flex"
      architecture             = "aarch64"
      target_triple            = "aarch64-unknown-linux-gnu"
      ocpus                    = 4
      memory_gib               = 24
      boot_volume_gib          = 50
      data_volume_gib          = 130
      combined_storage_gib     = 180
      cost_classification      = "free-trial-only"
      temporary                = false
      migration_target_profile = null
    }
    e5-temporary = {
      shape                    = "VM.Standard.E5.Flex"
      architecture             = "x86_64"
      target_triple            = "x86_64-unknown-linux-gnu"
      ocpus                    = 4
      memory_gib               = 24
      boot_volume_gib          = 200
      data_volume_gib          = 130
      combined_storage_gib     = 330
      cost_classification      = "paid-approved"
      temporary                = true
      migration_target_profile = "a1-target"
    }
  }
  capacity = local.capacity_profiles[var.capacity_profile]

  common_tags = merge({
    "liqi-project"          = "liqi-platform"
    "liqi-environment"      = local.environment
    "liqi-classification"   = "production"
    "liqi-managed-by"       = "opentofu"
    "liqi-source-sha"       = var.source_git_sha
    "liqi-capacity-profile" = var.capacity_profile
    }, var.capacity_profile == "e5-temporary" ? {
    "liqi-temporary-expires-at" = var.temporary_e5_expires_at
    "liqi-migration-target"     = "a1-target"
  } : {})

  stateful_tags = merge(local.common_tags, {
    "liqi-stateful" = "true"
  })

  public_edge_ports = {
    http  = 80
    https = 443
  }

  bastion_ssh_sources = {
    "10.42.20.100/32" = "OCI Bastion service VNIC primary IP only"
    "10.42.20.109/32" = "OCI Bastion private endpoint IP only"
  }

  regional_oracle_services = [
    for service in data.oci_core_services.regional.services : service
    if can(regex("^All .* Services In Oracle Services Network$", service.name))
  ]
  regional_oracle_service = one(local.regional_oracle_services)

  vault_secret_statements = [
    for secret_ocid in sort(tolist(var.vault_secret_ocids)) :
    "Allow dynamic-group ${oci_identity_dynamic_group.host.name} to read secret-bundles in compartment id ${oci_identity_compartment.environment.id} where target.secret.id = '${secret_ocid}'"
  ]
}
