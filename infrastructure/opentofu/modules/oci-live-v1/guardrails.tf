resource "terraform_data" "operation_guard" {
  input = {
    operation_mode           = var.operation_mode
    apply_approval_reference = var.apply_approval_reference
    capacity_profile         = var.capacity_profile
    temporary_e5_expires_at  = var.temporary_e5_expires_at
  }

  lifecycle {
    precondition {
      condition = (
        var.operation_mode == "plan" ||
        (var.operation_mode == "approved-apply" && length(trimspace(var.apply_approval_reference)) >= 3)
      )
      error_message = "approved-apply requires a non-empty explicit approval reference."
    }
    precondition {
      condition = (
        var.operation_mode == "plan" ||
        var.capacity_profile == "e5-temporary"
      )
      error_message = "approved apply is enabled only for the explicitly temporary E5 bridge in this source revision; the target A1 lane requires a later reviewed approval/source revision."
    }
    precondition {
      condition = (
        var.capacity_profile != "e5-temporary" ||
        (
          can(timecmp(var.temporary_e5_expires_at, timestamp())) &&
          timecmp(var.temporary_e5_expires_at, timestamp()) > 0 &&
          timecmp(var.temporary_e5_expires_at, timeadd(timestamp(), "2160h")) <= 0
        )
      )
      error_message = "e5-temporary requires an RFC3339 expiry in the future and no more than 90 days from plan time."
    }
  }
}

resource "terraform_data" "capacity_guard" {
  input = local.capacity

  lifecycle {
    precondition {
      condition     = var.acknowledge_capacity_availability_and_cost
      error_message = "The selected 4 OCPU/24 GiB profile requires explicit capacity, quota and cost acknowledgement before a live plan."
    }
    precondition {
      condition = (
        local.capacity.ocpus == 4 &&
        local.capacity.memory_gib == 24 &&
        local.capacity.data_volume_gib == 130 &&
        local.capacity.boot_volume_gib + local.capacity.data_volume_gib == local.capacity.combined_storage_gib
      )
      error_message = "V1 capacity must remain 4 OCPU/24 GiB with the preserved 130 GiB data volume."
    }
    precondition {
      condition = (
        (var.capacity_profile == "a1-target" &&
          local.capacity.shape == "VM.Standard.A1.Flex" &&
          local.capacity.architecture == "aarch64" &&
          local.capacity.boot_volume_gib == 50 &&
        local.capacity.combined_storage_gib == 180) ||
        (var.capacity_profile == "e5-temporary" &&
          local.capacity.shape == "VM.Standard.E5.Flex" &&
          local.capacity.architecture == "x86_64" &&
          local.capacity.boot_volume_gib == 200 &&
        local.capacity.combined_storage_gib == 330)
      )
      error_message = "capacity_profile does not match its reviewed shape, architecture or storage envelope."
    }
  }
}

resource "terraform_data" "management_plane_guard" {
  input = {
    bastion_source_cidrs      = sort(tolist(var.bastion_ssh_source_cidrs))
    management_evidence_id    = var.management_plane_evidence_id
    state_backend_evidence_id = var.state_backend_lock_evidence_id
  }

  lifecycle {
    precondition {
      condition     = var.bastion_ssh_source_cidrs == toset(["10.42.20.100/32", "10.42.20.109/32"])
      error_message = "Management SSH must remain restricted to the two technically accepted OCI Bastion /32 addresses."
    }
    precondition {
      condition     = length(trimspace(var.management_plane_evidence_id)) >= 3
      error_message = "Tested OCI Bastion/Run Command and independent management authority evidence is required before a live plan."
    }
    precondition {
      condition     = length(trimspace(var.state_backend_lock_evidence_id)) >= 3
      error_message = "TLS, locking, encrypted backup and restore evidence for the independent PostgreSQL OpenTofu backend is required before a live plan."
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
    version        = "1.1.0"
    target_triple  = local.capacity.target_triple
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
