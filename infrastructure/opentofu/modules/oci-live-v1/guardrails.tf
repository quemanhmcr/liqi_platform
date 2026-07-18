resource "terraform_data" "operation_guard" {
  input = {
    operation_mode           = var.operation_mode
    apply_approval_reference = var.apply_approval_reference
  }

  lifecycle {
    precondition {
      condition = (
        var.operation_mode == "plan" ||
        (var.operation_mode == "approved-apply" && length(trimspace(var.apply_approval_reference)) >= 3)
      )
      error_message = "approved-apply requires a non-empty explicit approval reference."
    }
  }
}

resource "terraform_data" "capacity_guard" {
  input = local.capacity

  lifecycle {
    precondition {
      condition     = var.acknowledge_capacity_availability_and_cost
      error_message = "A1 4/24 capacity, tenancy quota, block volume and Object Storage cost must be explicitly acknowledged before producing a live plan."
    }
    precondition {
      condition = (
        local.capacity.ocpus == 4 &&
        local.capacity.memory_gib == 24 &&
        local.capacity.combined_storage_gib == 180 &&
        local.capacity.boot_volume_gib + local.capacity.data_volume_gib == local.capacity.combined_storage_gib
      )
      error_message = "V1 capacity must remain A1 4 OCPU/24 GiB and 180 GiB provider storage."
    }
  }
}

resource "terraform_data" "reserved_ip_guard" {
  input = {
    enabled      = var.enable_reserved_public_ip
    acknowledged = var.acknowledge_reserved_public_ip
  }
  lifecycle {
    precondition {
      condition     = !var.enable_reserved_public_ip || var.acknowledge_reserved_public_ip
      error_message = "Reserved public IP requires explicit cost/quota acknowledgement."
    }
  }
}

resource "terraform_data" "bootstrap_revision" {
  input = {
    source_git_sha = var.source_git_sha
    sha256         = sha256(var.cloud_init_user_data)
    version        = "1.0.0"
  }
  lifecycle {
    precondition {
      condition     = length(base64gzip(var.cloud_init_user_data)) <= 16384
      error_message = "OCI metadata user_data must remain at or below the 16 KiB encoded limit."
    }
  }
}

resource "terraform_data" "host_bundle_trust_guard" {
  input = {
    key_id            = var.host_bundle_signing_key_id
    public_key_sha256 = var.host_bundle_signing_public_key_sha256
    acknowledged      = var.acknowledge_host_bundle_signing_key
  }

  lifecycle {
    precondition {
      condition     = var.acknowledge_host_bundle_signing_key
      error_message = "The host-bundle Ed25519 trust root and offline private-key ownership must be explicitly acknowledged before a live plan."
    }
    precondition {
      condition     = var.host_bundle_signing_key_id != "source-validation-v1"
      error_message = "The source-validation signing key is forbidden in a live plan."
    }
  }
}
