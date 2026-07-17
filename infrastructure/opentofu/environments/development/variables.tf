variable "tenancy_ocid" {
  description = "OCI tenancy OCID. Supply via TF_VAR_tenancy_ocid or an uncommitted tfvars file."
  type        = string
}

variable "region" {
  description = "OCI region."
  type        = string
  default     = "ap-singapore-2"
}

variable "availability_domain" {
  description = "OCI availability domain."
  type        = string
}

variable "oracle_linux_image_ocid" {
  description = "Pinned Oracle Linux 9 image compatible with VM.Standard.A1.Flex."
  type        = string
}

variable "admin_ssh_public_key" {
  description = "OpenSSH public key only."
  type        = string
}

variable "acknowledge_non_always_free_profile" {
  description = "Required because the mandated 4/24 profile is not verified Always Free by current Oracle documentation."
  type        = bool
  default     = false
}

variable "enable_admin_ssh" {
  description = "Enable allowlisted SSH ingress."
  type        = bool
  default     = false
}

variable "admin_ssh_source_cidrs" {
  description = "Exact non-world IPv4 CIDRs permitted for SSH."
  type        = set(string)
  default     = []
}

variable "vault_secret_ocids" {
  description = "Optional secret references readable by instance principal; values never enter OpenTofu."
  type        = set(string)
  default     = []
}
