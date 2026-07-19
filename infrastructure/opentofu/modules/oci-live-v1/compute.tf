resource "oci_core_instance" "host" {
  availability_domain = var.availability_domain
  compartment_id      = oci_identity_compartment.environment.id
  display_name        = var.resource_names.instance
  shape               = local.capacity.shape
  freeform_tags       = local.common_tags

  shape_config {
    ocpus         = local.capacity.ocpus
    memory_in_gbs = local.capacity.memory_gib
  }

  create_vnic_details {
    assign_public_ip = var.enable_reserved_public_ip ? "false" : "true"
    display_name     = var.resource_names.vnic
    hostname_label   = "host01"
    nsg_ids          = [oci_core_network_security_group.host.id]
    subnet_id        = oci_core_subnet.edge.id
  }

  source_details {
    source_type             = "image"
    source_id               = var.oracle_linux_image_ocid
    boot_volume_size_in_gbs = local.capacity.boot_volume_gib
    boot_volume_vpus_per_gb = 10
  }

  metadata = {
    user_data = base64gzip(var.cloud_init_user_data)
  }

  instance_options {
    are_legacy_imds_endpoints_disabled = true
  }

  agent_config {
    are_all_plugins_disabled = false
    is_management_disabled   = false
    is_monitoring_disabled   = false
    plugins_config {
      name          = "Compute Instance Run Command"
      desired_state = "ENABLED"
    }
  }

  availability_config {
    recovery_action = "RESTORE_INSTANCE"
  }

  preserve_boot_volume = false

  lifecycle {
    replace_triggered_by = [terraform_data.bootstrap_revision]
    precondition {
      condition     = local.capacity.architecture == "aarch64"
      error_message = "V1 A1 host requires aarch64."
    }
  }

  depends_on = [
    terraform_data.operation_guard,
    terraform_data.capacity_guard,
    terraform_data.management_plane_guard,
    terraform_data.reserved_ip_guard,
    terraform_data.host_bundle_trust_guard
  ]
}

resource "oci_core_volume_attachment" "data" {
  attachment_type                     = "paravirtualized"
  device                              = "/dev/oracleoci/oraclevdb"
  display_name                        = var.resource_names.data_attachment
  instance_id                         = oci_core_instance.host.id
  volume_id                           = oci_core_volume.data.id
  is_pv_encryption_in_transit_enabled = true
  is_read_only                        = false
  is_shareable                        = false
}

data "oci_core_vnic_attachments" "host" {
  compartment_id = oci_identity_compartment.environment.id
  instance_id    = oci_core_instance.host.id
}

data "oci_core_private_ips" "host" {
  vnic_id = one(data.oci_core_vnic_attachments.host.vnic_attachments).vnic_id
}

resource "oci_core_public_ip" "reserved" {
  count = var.enable_reserved_public_ip ? 1 : 0

  compartment_id = oci_identity_compartment.environment.id
  display_name   = var.resource_names.reserved_public_ip
  lifetime       = "RESERVED"
  private_ip_id  = one([for address in data.oci_core_private_ips.host.private_ips : address.id if address.is_primary])
  freeform_tags  = local.common_tags
}
