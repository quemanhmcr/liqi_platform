resource "oci_identity_compartment" "environment" {
  compartment_id = var.tenancy_ocid
  name           = "liqi-${var.environment}"
  description    = "LIQI Platform ${var.environment} environment; managed exclusively by OpenTofu."
  enable_delete  = true
  freeform_tags  = local.always_free_tags
}
