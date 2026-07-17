resource "oci_identity_dynamic_group" "host" {
  compartment_id = var.tenancy_ocid
  name           = "${replace(local.prefix, "-", "_")}_host"
  description    = "Exact-instance principal for the LIQI ${var.environment} V0 host."
  matching_rule  = "ALL {instance.id = '${oci_core_instance.host.id}'}"
  freeform_tags  = local.always_free_tags
}

locals {
  object_storage_policy_statements = [
    "Allow dynamic-group ${oci_identity_dynamic_group.host.name} to read buckets in compartment id ${oci_identity_compartment.environment.id} where target.bucket.name = '${oci_objectstorage_bucket.backups.name}'",
    "Allow dynamic-group ${oci_identity_dynamic_group.host.name} to manage objects in compartment id ${oci_identity_compartment.environment.id} where all {target.bucket.name = '${oci_objectstorage_bucket.backups.name}', any {request.permission = 'OBJECT_INSPECT', request.permission = 'OBJECT_READ', request.permission = 'OBJECT_CREATE'}}"
  ]

  vault_policy_statements = [
    for secret_ocid in sort(tolist(var.vault_secret_ocids)) :
    "Allow dynamic-group ${oci_identity_dynamic_group.host.name} to read secret-bundles in compartment id ${oci_identity_compartment.environment.id} where target.secret.id = '${secret_ocid}'"
  ]
}

resource "oci_identity_policy" "host" {
  compartment_id = var.tenancy_ocid
  name           = "${replace(local.prefix, "-", "_")}_host_policy"
  description    = "Least-privilege OCI capabilities for the exact LIQI ${var.environment} host instance principal."
  statements     = concat(local.object_storage_policy_statements, local.vault_policy_statements)
  freeform_tags  = local.always_free_tags
}
