resource "oci_identity_compartment" "environment" {
  compartment_id = var.tenancy_ocid
  name           = var.resource_names.compartment
  description    = "LIQI V1 production-shaped development environment; OpenTofu single-writer."
  enable_delete  = false
  freeform_tags  = local.common_tags
}
