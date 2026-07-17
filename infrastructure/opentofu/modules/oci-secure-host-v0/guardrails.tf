resource "terraform_data" "cost_guard" {
  input = {
    capacity_profile    = var.capacity_profile.name
    cost_classification = var.capacity_profile.cost_classification
  }

  lifecycle {
    precondition {
      condition = (
        var.capacity_profile.cost_classification == "always-free-safe" ||
        var.acknowledge_non_always_free_profile
      )
      error_message = "The selected capacity profile is not verified Always Free. Set acknowledge_non_always_free_profile=true only after owner cost review."
    }
  }
}

resource "terraform_data" "security_guard" {
  input = {
    enable_admin_ssh       = var.enable_admin_ssh
    admin_ssh_source_cidrs = sort(tolist(var.admin_ssh_source_cidrs))
  }

  lifecycle {
    precondition {
      condition     = !var.enable_admin_ssh || length(var.admin_ssh_source_cidrs) > 0
      error_message = "enable_admin_ssh=true requires at least one explicit non-world CIDR."
    }

    precondition {
      condition = alltrue([
        for cidr in var.admin_ssh_source_cidrs : cidr != "0.0.0.0/0"
      ])
      error_message = "Public SSH must never allow 0.0.0.0/0."
    }
  }
}

resource "terraform_data" "capacity_guard" {
  input = var.capacity_profile

  lifecycle {
    precondition {
      condition = (
        var.capacity_profile.ocpus <= 4 &&
        var.capacity_profile.memory_gb <= 24 &&
        var.capacity_profile.boot_volume_gb + var.capacity_profile.data_volume_gb <= 200
      )
      error_message = "V0 capacity exceeds 4 OCPU, 24 GB RAM, or 200 GB combined storage."
    }

    precondition {
      condition = (
        var.capacity_profile.name != "free-tier-a1-4x24" ||
        (
          var.capacity_profile.shape == "VM.Standard.A1.Flex" &&
          var.capacity_profile.architecture == "aarch64" &&
          var.capacity_profile.ocpus == 4 &&
          var.capacity_profile.memory_gb == 24
        )
      )
      error_message = "free-tier-a1-4x24 must remain VM.Standard.A1.Flex with 4 OCPUs and 24 GB RAM."
    }
  }
}

resource "terraform_data" "bootstrap_revision" {
  input = {
    version = var.bootstrap_version
    sha256  = sha256(var.cloud_init_user_data)
  }
}
