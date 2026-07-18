locals {
  environment = "v1-live"
  prefix      = "liqi-${local.environment}"

  common_tags = {
    "liqi-project"        = "liqi-platform"
    "liqi-environment"    = local.environment
    "liqi-classification" = "production-shaped-development"
    "liqi-managed-by"     = "opentofu"
    "liqi-source-sha"     = var.source_git_sha
  }

  stateful_tags = merge(local.common_tags, {
    "liqi-stateful" = "true"
  })

  capacity = {
    shape                = "VM.Standard.A1.Flex"
    architecture         = "aarch64"
    ocpus                = 4
    memory_gib           = 24
    boot_volume_gib      = 50
    data_volume_gib      = 130
    combined_storage_gib = 180
  }

  public_edge_ports = {
    http  = 80
    https = 443
  }

  object_storage_services = [
    for service in data.oci_core_services.regional.services : service
    if can(regex("Object Storage", service.name)) && !can(regex("All .* Services", service.name))
  ]
  object_storage_service = one(local.object_storage_services)

  backup_bucket_name = "${local.prefix}-backups"

  vault_secret_statements = [
    for secret_ocid in sort(tolist(var.vault_secret_ocids)) :
    "Allow dynamic-group ${oci_identity_dynamic_group.host.name} to read secret-bundles in compartment id ${oci_identity_compartment.environment.id} where target.secret.id = '${secret_ocid}'"
  ]
}
