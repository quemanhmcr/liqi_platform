resource "oci_identity_compartment" "environment" {
  compartment_id = var.tenancy_ocid
  name           = local.prefix
  description    = "LIQI V1 production-shaped development environment; OpenTofu single-writer."
  enable_delete  = false
  freeform_tags  = local.common_tags
}
