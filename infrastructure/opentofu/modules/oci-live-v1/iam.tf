resource "oci_identity_dynamic_group" "host" {
  compartment_id = var.tenancy_ocid
  name           = var.resource_names.dynamic_group
  description    = "Exact-instance principals for the LIQI v1-live private primary and recovery fallback."
  matching_rule  = "ANY {instance.id = '${oci_core_instance.private_host.id}', instance.id = '${oci_core_instance.private_fallback.id}'}"
  freeform_tags  = local.common_tags
}

resource "oci_identity_policy" "host" {
  compartment_id = var.tenancy_ocid
  name           = var.resource_names.policy
  description    = "Least-privilege OCI Vault secret-bundle access for the exact v1-live host."
  statements     = local.vault_secret_statements
  freeform_tags  = local.common_tags

  lifecycle {
    precondition {
      condition     = length(local.vault_secret_statements) > 0
      error_message = "At least one exact OCI Vault secret OCID is required before a live plan."
    }
  }
}
