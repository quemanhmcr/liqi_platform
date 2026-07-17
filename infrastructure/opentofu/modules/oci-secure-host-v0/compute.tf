resource "oci_core_instance" "host" {
  availability_domain = var.availability_domain
  compartment_id      = oci_identity_compartment.environment.id
  display_name        = "${local.prefix}-host-01"
  shape               = var.capacity_profile.shape
  freeform_tags       = local.compute_tags

  shape_config {
    ocpus         = var.capacity_profile.ocpus
    memory_in_gbs = var.capacity_profile.memory_gb
  }

  create_vnic_details {
    assign_public_ip = true
    display_name     = "${local.prefix}-host-vnic"
    hostname_label   = "host01"
    nsg_ids          = [oci_core_network_security_group.host.id]
    subnet_id        = oci_core_subnet.edge.id
  }

  source_details {
    source_type             = "image"
    source_id               = var.oracle_linux_image_ocid
    boot_volume_size_in_gbs = var.capacity_profile.boot_volume_gb
    boot_volume_vpus_per_gb = 10
  }

  metadata = {
    ssh_authorized_keys = trimspace(var.admin_ssh_public_key)
    user_data           = base64gzip(var.cloud_init_user_data)
  }

  instance_options {
    are_legacy_imds_endpoints_disabled = true
  }

  availability_config {
    recovery_action = "RESTORE_INSTANCE"
  }

  preserve_boot_volume = false

  lifecycle {
    replace_triggered_by = [terraform_data.bootstrap_revision]

    precondition {
      condition     = var.capacity_profile.architecture == "aarch64"
      error_message = "V0 Oracle Linux image and VM.Standard.A1.Flex host contract require aarch64."
    }

    precondition {
      condition     = length(base64gzip(var.cloud_init_user_data)) <= 16384
      error_message = "OCI user_data must remain at or below the documented 16 KiB encoded limit."
    }
  }

  depends_on = [
    terraform_data.cost_guard,
    terraform_data.security_guard,
    terraform_data.capacity_guard
  ]
}

resource "oci_core_volume_attachment" "data" {
  attachment_type                     = "paravirtualized"
  device                              = "/dev/oracleoci/oraclevdb"
  display_name                        = "${local.prefix}-data-attachment"
  instance_id                         = oci_core_instance.host.id
  volume_id                           = oci_core_volume.data.id
  is_pv_encryption_in_transit_enabled = true
  is_read_only                        = false
  is_shareable                        = false
}
