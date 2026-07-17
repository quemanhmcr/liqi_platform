variable "tenancy_ocid" {
  description = "OCI tenancy OCID; the environment compartment and tenancy-level IAM resources are created beneath it."
  type        = string

  validation {
    condition     = can(regex("^ocid1\\.tenancy\\.", var.tenancy_ocid))
    error_message = "tenancy_ocid must be an OCI tenancy OCID."
  }
}

variable "region" {
  description = "OCI region for the V0 environment."
  type        = string
  default     = "ap-singapore-2"
}

variable "availability_domain" {
  description = "Availability domain selected for both the host and its durable data volume."
  type        = string
}

variable "environment" {
  description = "Environment identifier used in compartment, resource names, tags, and output contract."
  type        = string
  default     = "development"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,31}$", var.environment))
    error_message = "environment must be a lowercase DNS-safe identifier."
  }
}

variable "owner" {
  description = "Operational owner tag applied to provider-owned resources."
  type        = string
  default     = "liqi-platform"
}

variable "capacity_profile" {
  description = "Parameterized capacity profile consumed by the host contract."
  type = object({
    name                = string
    shape               = string
    architecture        = string
    ocpus               = number
    memory_gb           = number
    boot_volume_gb      = number
    data_volume_gb      = number
    data_volume_vpus_gb = number
    cost_classification = string
  })

  default = {
    name                = "free-tier-a1-4x24"
    shape               = "VM.Standard.A1.Flex"
    architecture        = "aarch64"
    ocpus               = 4
    memory_gb           = 24
    boot_volume_gb      = 50
    data_volume_gb      = 100
    data_volume_vpus_gb = 0
    cost_classification = "free-trial-only"
  }

  validation {
    condition = contains(
      ["always-free-safe", "free-trial-only", "paid", "unknown"],
      var.capacity_profile.cost_classification
    )
    error_message = "capacity_profile.cost_classification must use an approved cost class."
  }

  validation {
    condition = (
      var.capacity_profile.ocpus > 0 &&
      var.capacity_profile.memory_gb > 0 &&
      var.capacity_profile.boot_volume_gb >= 50 &&
      var.capacity_profile.data_volume_gb >= 50 &&
      var.capacity_profile.boot_volume_gb + var.capacity_profile.data_volume_gb <= 200
    )
    error_message = "capacity profile must stay within the 200 GB combined boot/block storage envelope."
  }
}

variable "acknowledge_non_always_free_profile" {
  description = "Explicit acknowledgement required before planning or applying a profile not verified as Always Free."
  type        = bool
  default     = false
}

variable "admin_ssh_public_key" {
  description = "OpenSSH public key installed for the OCI image default user. Public material only; never pass a private key."
  type        = string

  validation {
    condition = can(regex(
      "^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)) [A-Za-z0-9+/=]+(?: .*)?$",
      trimspace(var.admin_ssh_public_key)
    ))
    error_message = "admin_ssh_public_key must contain one valid OpenSSH public key line."
  }
}

variable "enable_admin_ssh" {
  description = "Enable allowlisted public SSH ingress. Disabled by default."
  type        = bool
  default     = false
}

variable "admin_ssh_source_cidrs" {
  description = "Exact IPv4 CIDRs allowed to reach SSH when enable_admin_ssh is true."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for cidr in var.admin_ssh_source_cidrs :
      can(cidrnetmask(cidr)) && cidr != "0.0.0.0/0"
    ])
    error_message = "SSH source CIDRs must be valid and must never include 0.0.0.0/0."
  }
}

variable "enable_http_redirect" {
  description = "Expose TCP/80 only for HTTP-to-HTTPS redirect and ACME edge handling."
  type        = bool
  default     = true
}

variable "enable_https_edge" {
  description = "Expose the approved TLS edge on TCP/443."
  type        = bool
  default     = true
}

variable "vcn_cidr" {
  description = "Development VCN IPv4 CIDR."
  type        = string
  default     = "10.20.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.vcn_cidr))
    error_message = "vcn_cidr must be valid IPv4 CIDR notation."
  }
}

variable "edge_subnet_cidr" {
  description = "Public edge subnet hosting the single V0 node."
  type        = string
  default     = "10.20.10.0/24"

  validation {
    condition     = can(cidrnetmask(var.edge_subnet_cidr))
    error_message = "edge_subnet_cidr must be valid IPv4 CIDR notation."
  }
}

variable "oracle_linux_image_ocid" {
  description = "Pinned Oracle Linux 9 aarch64 platform image OCID. Pin before first apply."
  type        = string

  validation {
    condition     = can(regex("^ocid1\\.image\\.", var.oracle_linux_image_ocid))
    error_message = "oracle_linux_image_ocid must be an OCI image OCID."
  }
}

variable "cloud_init_user_data" {
  description = "Rendered deterministic cloud-init document. It must not contain secrets."
  type        = string
}

variable "bootstrap_version" {
  description = "Version included in readiness and used to force host replacement when bootstrap semantics change."
  type        = string
  default     = "0.1.0"
}

variable "infrastructure_output_version" {
  description = "Additive consumer output version under the oci-host-v0 schema."
  type        = string
  default     = "0.1.0"

  validation {
    condition     = can(regex("^0\\.[0-9]+\\.[0-9]+$", var.infrastructure_output_version))
    error_message = "infrastructure_output_version must be a 0.x.y semantic version."
  }
}

variable "vault_secret_ocids" {
  description = "Optional OCI Vault secret references readable by the instance principal; no secret value enters OpenTofu."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for secret_ocid in var.vault_secret_ocids : can(regex("^ocid1\\.vaultsecret\\.", secret_ocid))
    ])
    error_message = "vault_secret_ocids entries must be OCI Vault secret OCIDs."
  }
}
