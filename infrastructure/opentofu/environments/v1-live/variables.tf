variable "tenancy_ocid" {
  type = string
}

variable "region" {
  type    = string
  default = "ap-singapore-2"
}

variable "availability_domain" {
  type = string
}

variable "oracle_linux_image_ocid" {
  type = string
}

variable "source_git_sha" {
  type = string
}

variable "operation_mode" {
  type    = string
  default = "plan"
}

variable "apply_approval_reference" {
  type    = string
  default = ""
}

variable "acknowledge_capacity_availability_and_cost" {
  type    = bool
  default = false
}

variable "enable_reserved_public_ip" {
  type    = bool
  default = false
}

variable "acknowledge_reserved_public_ip" {
  type    = bool
  default = false
}

variable "vault_secret_ocids" {
  type    = set(string)
  default = []
}

variable "state_backend_lock_evidence_id" {
  type    = string
  default = ""
}

variable "host_bundle_signing_key_id" {
  type = string
}

variable "host_bundle_signing_public_key_pem" {
  type      = string
  sensitive = false
  validation {
    condition = (
      startswith(var.host_bundle_signing_public_key_pem, "-----BEGIN PUBLIC KEY-----") &&
      endswith(trimspace(var.host_bundle_signing_public_key_pem), "-----END PUBLIC KEY-----")
    )
    error_message = "host_bundle_signing_public_key_pem must be a PEM public key."
  }
}

variable "acknowledge_host_bundle_signing_key" {
  type    = bool
  default = false
}
