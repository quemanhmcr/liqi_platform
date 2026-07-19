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
    vcn_cidr                 = string
    vcn_dns_label            = string
    host_subnet_cidr         = string
    host_subnet_label        = string
    public_edge_subnet_cidr  = string
    public_edge_subnet_label = string
  })
  default = {
    vcn_cidr                 = "10.40.0.0/16"
    vcn_dns_label            = "liqiv1"
    host_subnet_cidr         = "10.40.10.0/24"
    host_subnet_label        = "host"
    public_edge_subnet_cidr  = "10.40.30.0/24"
    public_edge_subnet_label = "edge"
  }
}

variable "resource_names" {
  type = object({
    compartment             = string
    vcn                     = string
    internet_gateway        = string
    nat_gateway             = string
    service_gateway         = string
    route_table             = string
    public_edge_route_table = string
    security_list           = string
    subnet                  = string
    public_edge_subnet      = string
    nsg                     = string
    nlb_nsg                 = string
    network_load_balancer   = string
    instance                = string
    vnic                    = string
    data_volume             = string
    data_attachment         = string
    vault                   = string
    key                     = string
    reserved_public_ip      = string
    dynamic_group           = string
    policy                  = string
  })
  default = {
    compartment             = "liqi-v1-live"
    vcn                     = "liqi-v1-live-vcn"
    internet_gateway        = "liqi-v1-live-internet-gateway"
    nat_gateway             = "liqi-v1-live-nat-gateway"
    service_gateway         = "liqi-v1-live-service-gateway"
    route_table             = "liqi-v1-live-host-routes"
    public_edge_route_table = "liqi-v1-live-public-edge-routes"
    security_list           = "liqi-v1-live-empty-security-list"
    subnet                  = "liqi-v1-live-host-subnet"
    public_edge_subnet      = "liqi-v1-live-public-edge-subnet"
    nsg                     = "liqi-v1-live-host-nsg"
    nlb_nsg                 = "liqi-v1-live-public-edge-nsg"
    network_load_balancer   = "liqi-v1-live-edge-nlb"
    instance                = "liqi-v1-live-host-01"
    vnic                    = "liqi-v1-live-host-vnic"
    data_volume             = "liqi-v1-live-data"
    data_attachment         = "liqi-v1-live-data-attachment"
    vault                   = "liqi-v1-live-vault"
    key                     = "liqi-v1-live-software-key"
    reserved_public_ip      = "liqi-v1-live-edge-ip"
    dynamic_group           = "liqi_v1_live_host"
    policy                  = "liqi_v1_live_host_policy"
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

variable "bastion_ssh_source_cidrs" {
  type    = set(string)
  default = ["10.42.20.100/32", "10.42.20.109/32"]
  validation {
    condition     = var.bastion_ssh_source_cidrs == toset(["10.42.20.100/32", "10.42.20.109/32"])
    error_message = "Only the accepted OCI Bastion /32 addresses are permitted."
  }
}

variable "management_plane_evidence_id" {
  type    = string
  default = ""
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
