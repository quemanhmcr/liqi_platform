data "oci_objectstorage_namespace" "current" {
  compartment_id = var.tenancy_ocid
}

resource "oci_kms_vault" "main" {
  compartment_id = oci_identity_compartment.environment.id
  display_name   = "${local.prefix}-vault"
  vault_type     = "DEFAULT"
  freeform_tags  = local.stateful_tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_kms_key" "main" {
  compartment_id      = oci_identity_compartment.environment.id
  display_name        = "${local.prefix}-software-key"
  management_endpoint = oci_kms_vault.main.management_endpoint
  protection_mode     = "SOFTWARE"
  freeform_tags       = local.stateful_tags

  key_shape {
    algorithm = "AES"
    length    = 32
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_objectstorage_bucket" "backups" {
  compartment_id        = oci_identity_compartment.environment.id
  namespace             = data.oci_objectstorage_namespace.current.namespace
  name                  = local.backup_bucket_name
  access_type           = "NoPublicAccess"
  storage_tier          = "Standard"
  versioning            = "Enabled"
  auto_tiering          = "Disabled"
  object_events_enabled = false
  kms_key_id            = oci_kms_key.main.id
  freeform_tags         = local.stateful_tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_core_volume" "data" {
  availability_domain = var.availability_domain
  compartment_id      = oci_identity_compartment.environment.id
  display_name        = "${local.prefix}-data"
  size_in_gbs         = local.capacity.data_volume_gib
  vpus_per_gb         = 0
  freeform_tags       = local.stateful_tags

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.capacity_guard]
}
