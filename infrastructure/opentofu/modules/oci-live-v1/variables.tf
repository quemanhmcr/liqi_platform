variable "tenancy_ocid" {
  description = "OCI tenancy OCID containing the V1 environment compartment and tenancy-level IAM resources."
  type        = string
  validation {
    condition     = can(regex("^ocid1\\.tenancy\\.", var.tenancy_ocid))
    error_message = "tenancy_ocid must be an OCI tenancy OCID."
  }
}

variable "region" {
  description = "OCI home region selected for the production-shaped V1 environment."
  type        = string
  default     = "ap-singapore-2"
  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.region))
    error_message = "region must be an OCI region identifier."
  }
}

variable "availability_domain" {
  description = "Availability domain for the A1 host and preserved block volume."
  type        = string
}

variable "oracle_linux_image_ocid" {
  description = "Pinned Oracle Linux 9 aarch64 image OCID reviewed for the V1 release."
  type        = string
  validation {
    condition     = can(regex("^ocid1\\.image\\.", var.oracle_linux_image_ocid))
    error_message = "oracle_linux_image_ocid must be an OCI image OCID."
  }
}

variable "cloud_init_user_data" {
  description = "Rendered source-controlled cloud-init document. Secret values are forbidden."
  type        = string
}

variable "source_git_sha" {
  description = "Exact Git SHA whose infrastructure source is being planned or applied."
  type        = string
  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.source_git_sha))
    error_message = "source_git_sha must be a lowercase 40-character Git SHA."
  }
}

variable "operation_mode" {
  description = "Read-only planning or explicitly approved apply mode."
  type        = string
  default     = "plan"
  validation {
    condition     = contains(["plan", "approved-apply"], var.operation_mode)
    error_message = "operation_mode must be plan or approved-apply."
  }
}

variable "apply_approval_reference" {
  description = "Non-secret owner approval reference. Required only for an approved apply plan."
  type        = string
  default     = ""
  validation {
    condition     = length(var.apply_approval_reference) <= 256
    error_message = "apply_approval_reference must be at most 256 characters."
  }
}

variable "acknowledge_capacity_availability_and_cost" {
  description = "Explicit acknowledgement that A1 capacity/quota and all storage usage were reviewed for this tenancy."
  type        = bool
  default     = false
}

variable "enable_reserved_public_ip" {
  description = "Use a reserved public IPv4 address after explicit cost/quota approval. Disabled by default."
  type        = bool
  default     = false
}

variable "acknowledge_reserved_public_ip" {
  description = "Explicit acknowledgement required when reserved public IP is enabled."
  type        = bool
  default     = false
}

variable "vault_secret_ocids" {
  description = "OCI Vault secret references readable by the instance principal. Values never enter OpenTofu."
  type        = set(string)
  default     = []
  validation {
    condition = alltrue([
      for secret_ocid in var.vault_secret_ocids : can(regex("^ocid1\\.vaultsecret\\.", secret_ocid))
    ])
    error_message = "vault_secret_ocids must contain only OCI Vault secret OCIDs."
  }
}

variable "state_backend_lock_evidence_id" {
  description = "Machine evidence identifier proving S3-compatible lockfile behavior against the approved state bucket."
  type        = string
  default     = ""
  validation {
    condition     = length(var.state_backend_lock_evidence_id) <= 256
    error_message = "state_backend_lock_evidence_id must be at most 256 characters."
  }
}

variable "host_bundle_signing_key_id" {
  description = "Non-secret Ed25519 public-key identity trusted by the baseline host."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9._-]{2,63}$", var.host_bundle_signing_key_id))
    error_message = "host_bundle_signing_key_id must be a stable lowercase identifier."
  }
}

variable "host_bundle_signing_public_key_sha256" {
  description = "SHA-256 of the exact PEM public key embedded in baseline cloud-init."
  type        = string
  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.host_bundle_signing_public_key_sha256))
    error_message = "host_bundle_signing_public_key_sha256 must be a lowercase SHA-256 digest."
  }
}

variable "acknowledge_host_bundle_signing_key" {
  description = "Explicit acknowledgement that the production signing public key and offline/private signer ownership were reviewed."
  type        = bool
  default     = false
}
