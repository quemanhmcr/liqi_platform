data "oci_objectstorage_namespace" "current" {
  compartment_id = var.tenancy_ocid
}

resource "oci_objectstorage_bucket" "backups" {
  compartment_id        = oci_identity_compartment.environment.id
  namespace             = data.oci_objectstorage_namespace.current.namespace
  name                  = local.backup_bucket_name
  access_type           = "NoPublicAccess"
  storage_tier          = "Standard"
  versioning            = "Disabled"
  auto_tiering          = "Disabled"
  object_events_enabled = false
  freeform_tags         = local.stateful_tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_core_volume" "data" {
  availability_domain = var.availability_domain
  compartment_id      = oci_identity_compartment.environment.id
  display_name        = "${local.prefix}-data"
  size_in_gbs         = var.capacity_profile.data_volume_gb
  vpus_per_gb         = var.capacity_profile.data_volume_vpus_gb
  freeform_tags       = local.stateful_tags

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.capacity_guard]
}
