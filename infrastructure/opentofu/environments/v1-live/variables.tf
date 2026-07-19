variable "tenancy_ocid" {
  type = string
}

variable "region" {
  type    = string
  default = "ap-singapore-2"
}

variable "capacity_profile" {
  type    = string
  default = "a1-target"
  validation {
    condition     = contains(["a1-target", "e5-temporary"], var.capacity_profile)
    error_message = "capacity_profile must be a1-target or e5-temporary."
  }
}

variable "temporary_e5_expires_at" {
  type    = string
  default = ""
}

variable "network_config" {
  type = object({
    vcn_cidr          = string
    vcn_dns_label     = string
    edge_subnet_cidr  = string
    edge_subnet_label = string
  })
  default = {
    vcn_cidr          = "10.40.0.0/16"
    vcn_dns_label     = "liqiv1"
    edge_subnet_cidr  = "10.40.10.0/24"
    edge_subnet_label = "edge"
  }
}

variable "resource_names" {
  type = object({
    compartment        = string
    vcn                = string
    internet_gateway   = string
    route_table        = string
    security_list      = string
    subnet             = string
    nsg                = string
    instance           = string
    vnic               = string
    data_volume        = string
    data_attachment    = string
    vault              = string
    key                = string
    reserved_public_ip = string
    dynamic_group      = string
    policy             = string
  })
  default = {
    compartment        = "liqi-v1-live"
    vcn                = "liqi-v1-live-vcn"
    internet_gateway   = "liqi-v1-live-internet-gateway"
    route_table        = "liqi-v1-live-edge-routes"
    security_list      = "liqi-v1-live-empty-security-list"
    subnet             = "liqi-v1-live-edge-subnet"
    nsg                = "liqi-v1-live-host-nsg"
    instance           = "liqi-v1-live-host-01"
    vnic               = "liqi-v1-live-host-vnic"
    data_volume        = "liqi-v1-live-data"
    data_attachment    = "liqi-v1-live-data-attachment"
    vault              = "liqi-v1-live-vault"
    key                = "liqi-v1-live-software-key"
    reserved_public_ip = "liqi-v1-live-edge-ip"
    dynamic_group      = "liqi_v1_live_host"
    policy             = "liqi_v1_live_host_policy"
  }
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
