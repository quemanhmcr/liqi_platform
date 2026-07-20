resource "oci_core_instance" "host" {
  availability_domain = var.availability_domain
  compartment_id      = oci_identity_compartment.environment.id
  display_name        = var.resource_names.legacy_instance
  shape               = local.capacity.shape
  freeform_tags       = local.common_tags

  shape_config {
    ocpus         = local.capacity.ocpus
    memory_in_gbs = local.capacity.memory_gib
  }

  create_vnic_details {
    assign_public_ip = "false"
    display_name     = var.resource_names.legacy_vnic
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
    # The temporary E5 host is adopted after a separate technical-acceptance
    # process. Immutable launch metadata is evidence-owned and must not trigger
    # replacement during state adoption. The later A1 migration uses a new
    # reviewed source revision and parallel instance rather than in-place drift.
    ignore_changes = [
      source_details,
      metadata,
      create_vnic_details,
      availability_config,
      preserve_boot_volume,
    ]
    prevent_destroy = true
    precondition {
      condition = (
        (var.capacity_profile == "a1-target" && local.capacity.architecture == "aarch64") ||
        (var.capacity_profile == "e5-temporary" && local.capacity.architecture == "x86_64")
      )
      error_message = "Capacity profile architecture does not match its reviewed target."
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

# New production primary created alongside the retained legacy host. It is
# never assigned a public IP and its primary VNIC is born in the immutable
# private subnet, avoiding in-place VNIC or subnet surgery.
resource "oci_core_instance" "private_host" {
  availability_domain = var.availability_domain
  compartment_id      = oci_identity_compartment.environment.id
  display_name        = var.resource_names.instance
  shape               = local.capacity.shape
  freeform_tags       = merge(local.common_tags, { "liqi-host-role" = "primary" })

  shape_config {
    ocpus         = local.capacity.ocpus
    memory_in_gbs = local.capacity.memory_gib
  }

  create_vnic_details {
    assign_public_ip = "false"
    display_name     = var.resource_names.vnic
    hostname_label   = "host01"
    nsg_ids          = [oci_core_network_security_group.host.id]
    subnet_id        = oci_core_subnet.private_host.id
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

  preserve_boot_volume = true

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [
    terraform_data.operation_guard,
    terraform_data.capacity_guard,
    terraform_data.management_plane_guard,
    terraform_data.reserved_ip_guard,
    terraform_data.host_bundle_trust_guard,
    terraform_data.cutover_guard,
  ]
}

# A distinct recovery host is bootstrapped while RUNNING, verified, then moved
# to STOPPED by a later reviewed saved plan before the recovery exercise.
resource "oci_core_instance" "private_fallback" {
  availability_domain = var.availability_domain
  compartment_id      = oci_identity_compartment.environment.id
  display_name        = var.resource_names.fallback_instance
  shape               = local.capacity.shape
  state               = var.fallback_desired_state
  freeform_tags       = merge(local.common_tags, { "liqi-host-role" = "recovery-fallback" })

  shape_config {
    ocpus         = local.capacity.ocpus
    memory_in_gbs = local.capacity.memory_gib
  }

  create_vnic_details {
    assign_public_ip = "false"
    display_name     = var.resource_names.fallback_vnic
    hostname_label   = "fallback01"
    nsg_ids          = [oci_core_network_security_group.host.id]
    subnet_id        = oci_core_subnet.private_host.id
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
    recovery_action = "STOP_INSTANCE"
  }

  preserve_boot_volume = true

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [
    oci_core_instance.private_host,
    terraform_data.operation_guard,
    terraform_data.capacity_guard,
    terraform_data.management_plane_guard,
    terraform_data.reserved_ip_guard,
    terraform_data.host_bundle_trust_guard,
    terraform_data.cutover_guard,
  ]
}

resource "oci_core_volume_attachment" "data" {
  attachment_type                     = "paravirtualized"
  device                              = "/dev/oracleoci/oraclevdb"
  display_name                        = var.resource_names.data_attachment
  instance_id                         = oci_core_instance.private_host.id
  volume_id                           = oci_core_volume.data.id
  is_pv_encryption_in_transit_enabled = true
  is_read_only                        = false
  is_shareable                        = false
}
