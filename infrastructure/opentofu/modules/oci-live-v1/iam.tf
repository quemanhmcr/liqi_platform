resource "oci_identity_dynamic_group" "host" {
  compartment_id = var.tenancy_ocid
  name           = "liqi_v1_live_host"
  description    = "Exact-instance principal for the LIQI v1-live host."
  matching_rule  = "ALL {instance.id = '${oci_core_instance.host.id}'}"
  freeform_tags  = local.common_tags
}

resource "oci_identity_policy" "host" {
  compartment_id = var.tenancy_ocid
  name           = "liqi_v1_live_host_policy"
  description    = "Least-privilege Object Storage and Vault access for the exact v1-live host."
  statements = concat([
    "Allow dynamic-group ${oci_identity_dynamic_group.host.name} to read buckets in compartment id ${oci_identity_compartment.environment.id} where target.bucket.name = '${oci_objectstorage_bucket.backups.name}'",
    "Allow dynamic-group ${oci_identity_dynamic_group.host.name} to manage objects in compartment id ${oci_identity_compartment.environment.id} where all {target.bucket.name = '${oci_objectstorage_bucket.backups.name}', any {request.permission = 'OBJECT_INSPECT', request.permission = 'OBJECT_READ', request.permission = 'OBJECT_CREATE'}}",
    "Allow service objectstorage-${var.region} to use keys in compartment id ${oci_identity_compartment.environment.id} where target.key.id = '${oci_kms_key.main.id}'"
  ], local.vault_secret_statements)
  freeform_tags = local.common_tags
}
