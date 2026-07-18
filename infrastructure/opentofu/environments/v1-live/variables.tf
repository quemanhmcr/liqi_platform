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


variable "management_wireguard_peer_cidr" {
  type = string
  validation {
    condition     = can(cidrhost(var.management_wireguard_peer_cidr, 0)) && can(regex("/32$", var.management_wireguard_peer_cidr))
    error_message = "management_wireguard_peer_cidr must be an exact IPv4 /32 peer endpoint."
  }
}

variable "management_wireguard_port" {
  type    = number
  default = 51820
  validation {
    condition     = var.management_wireguard_port >= 1 && var.management_wireguard_port <= 65535
    error_message = "management_wireguard_port must be between 1 and 65535."
  }
}

variable "management_plane_evidence_id" {
  type    = string
  default = ""
  validation {
    condition     = length(var.management_plane_evidence_id) <= 256
    error_message = "management_plane_evidence_id must be at most 256 characters."
  }
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
