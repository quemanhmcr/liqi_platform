variable "tenancy_ocid" {
  description = "OCI tenancy OCID containing the V1 environment compartment and tenancy-level IAM resources."
  type        = string
  validation {
    condition     = can(regex("^ocid1\\.tenancy\\.", var.tenancy_ocid))
    error_message = "tenancy_ocid must be an OCI tenancy OCID."
  }
}

variable "region" {
  description = "OCI region selected for the live V1 environment."
  type        = string
  default     = "ap-singapore-2"
  validation {
    condition     = var.region == "ap-singapore-2"
    error_message = "V1 live is restricted to ap-singapore-2."
  }
}

variable "capacity_profile" {
  description = "Explicit host lane: target A1 remains the default; E5 is a time-bounded paid bridge while A1 capacity is unavailable."
  type        = string
  default     = "a1-target"
  validation {
    condition     = contains(["a1-target", "e5-temporary"], var.capacity_profile)
    error_message = "capacity_profile must be a1-target or e5-temporary."
  }
}

variable "temporary_e5_expires_at" {
  description = "RFC3339 expiry for the temporary E5 bridge. Required only for e5-temporary and bounded to 90 days by guardrails."
  type        = string
  default     = ""
  validation {
    condition     = var.temporary_e5_expires_at == "" || can(timecmp(var.temporary_e5_expires_at, "2026-01-01T00:00:00Z"))
    error_message = "temporary_e5_expires_at must be empty or an RFC3339 timestamp."
  }
}

variable "network_config" {
  description = "Reviewed VCN, private host subnet, and separated public NLB edge subnet profile."
  type = object({
    vcn_cidr                 = string
    vcn_dns_label            = string
    legacy_host_subnet_cidr  = string
    legacy_host_subnet_label = string
    host_subnet_cidr         = string
    host_subnet_label        = string
    public_edge_subnet_cidr  = string
    public_edge_subnet_label = string
  })
  default = {
    vcn_cidr                 = "10.40.0.0/16"
    vcn_dns_label            = "liqiv1"
    legacy_host_subnet_cidr  = "10.40.10.0/24"
    legacy_host_subnet_label = "legacy"
    host_subnet_cidr         = "10.40.20.0/24"
    host_subnet_label        = "host"
    public_edge_subnet_cidr  = "10.40.30.0/24"
    public_edge_subnet_label = "edge"
  }
  validation {
    condition = contains([
      "10.40.0.0/16|10.40.10.0/24|10.40.20.0/24|10.40.30.0/24|liqiv1|legacy|host|edge",
      "10.42.0.0/16|10.42.10.0/24|10.42.20.0/24|10.42.30.0/24|liqilive|live|livepriv|edge",
      ], join("|", [
        var.network_config.vcn_cidr,
        var.network_config.legacy_host_subnet_cidr,
        var.network_config.host_subnet_cidr,
        var.network_config.public_edge_subnet_cidr,
        var.network_config.vcn_dns_label,
        var.network_config.legacy_host_subnet_label,
        var.network_config.host_subnet_label,
        var.network_config.public_edge_subnet_label,
    ]))
    error_message = "network_config must match an explicitly reviewed private-host/public-NLB profile."
  }
}

variable "resource_names" {
  description = "Reviewed OCI display names. Override only to adopt an existing environment without replacement-by-rename."
  type = object({
    compartment              = string
    vcn                      = string
    internet_gateway         = string
    nat_gateway              = string
    service_gateway          = string
    legacy_route_table       = string
    route_table              = string
    public_edge_route_table  = string
    security_list            = string
    legacy_subnet            = string
    subnet                   = string
    public_edge_subnet       = string
    nsg                      = string
    nlb_nsg                  = string
    network_load_balancer    = string
    legacy_instance          = string
    legacy_vnic              = string
    legacy_fallback_instance = string
    data_volume              = string
    data_attachment          = string
    vault                    = string
    key                      = string
    reserved_public_ip       = string
    dynamic_group            = string
    policy                   = string
  })
  default = {
    compartment              = "liqi-v1-live"
    vcn                      = "liqi-v1-live-vcn"
    internet_gateway         = "liqi-v1-live-internet-gateway"
    nat_gateway              = "liqi-v1-live-nat-gateway"
    service_gateway          = "liqi-v1-live-service-gateway"
    legacy_route_table       = "liqi-v1-live-legacy-host-routes"
    route_table              = "liqi-v1-live-host-routes"
    public_edge_route_table  = "liqi-v1-live-public-edge-routes"
    security_list            = "liqi-v1-live-empty-security-list"
    legacy_subnet            = "liqi-v1-live-legacy-host-subnet"
    subnet                   = "liqi-v1-live-host-subnet"
    public_edge_subnet       = "liqi-v1-live-public-edge-subnet"
    nsg                      = "liqi-v1-live-host-nsg"
    nlb_nsg                  = "liqi-v1-live-public-edge-nsg"
    network_load_balancer    = "liqi-v1-live-edge-nlb"
    legacy_instance          = "liqi-v1-live-legacy-host-01"
    legacy_vnic              = "liqi-v1-live-legacy-host-vnic"
    legacy_fallback_instance = "liqi-v1-live-legacy-fallback"
    data_volume              = "liqi-v1-live-data"
    data_attachment          = "liqi-v1-live-data-attachment"
    vault                    = "liqi-v1-live-vault"
    key                      = "liqi-v1-live-software-key"
    reserved_public_ip       = "liqi-v1-live-edge-ip"
    dynamic_group            = "liqi_v1_live_host"
    policy                   = "liqi_v1_live_host_policy"
  }
  validation {
    condition = alltrue([
      for value in values(var.resource_names) : can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{2,95}$", value))
    ])
    error_message = "resource_names values must be stable OCI-safe identifiers."
  }
}

variable "availability_domain" {
  description = "Availability domain for the selected temporary-E5 or target-A1 host and preserved block volume."
  type        = string
}

variable "oracle_linux_image_ocid" {
  description = "Pinned Oracle Linux 9 image OCID whose architecture matches capacity_profile."
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
  description = "Explicit acknowledgement that capacity, quota, expiry and E5 cost were reviewed."
  type        = bool
  default     = false
}

variable "enable_reserved_public_ip" {
  description = "Use a reserved IPv4 for the public NLB after explicit cost/quota approval. Disabled by default."
  type        = bool
  default     = false
}

variable "acknowledge_reserved_public_ip" {
  description = "Explicit acknowledgement required when the NLB reserved public IP is enabled."
  type        = bool
  default     = false
}

variable "public_backend_enabled" {
  description = "Deliberate public cutover switch. False creates or retains NLB backends offline; true is allowed only in an approved apply plan with explicit cutover acknowledgement."
  type        = bool
  default     = false
}

variable "acknowledge_public_cutover" {
  description = "Explicit acknowledgement that private health, recovery, monitoring and backup evidence passed before enabling public NLB backends."
  type        = bool
  default     = false
}

variable "fallback_desired_state" {
  description = "Observed steady-state lifecycle for the retained first-release fallback. Recovery drills perform bounded start/stop outside OpenTofu."
  type        = string
  default     = "STOPPED"
  validation {
    condition     = var.fallback_desired_state == "STOPPED"
    error_message = "The retained first-release fallback must remain STOPPED outside an evidence-producing recovery drill."
  }
}

variable "retained_fallback_instance_ocid" {
  description = "Protected identifier of the existing stopped first-release recovery instance. It is referenced for instance-principal authorization but is not managed or destroyed by this module."
  type        = string
  sensitive   = true
  validation {
    condition     = can(regex("^ocid1\\.instance\\.", var.retained_fallback_instance_ocid))
    error_message = "retained_fallback_instance_ocid must be an OCI compute instance OCID."
  }
}

variable "retained_fallback_private_ipv4" {
  description = "Protected private IPv4 of the existing stopped recovery instance, bound into the non-secret output contract without assigning a public IP."
  type        = string
  sensitive   = true
  validation {
    condition     = can(cidrhost("${var.retained_fallback_private_ipv4}/32", 0))
    error_message = "retained_fallback_private_ipv4 must be an IPv4 address."
  }
}

variable "bastion_ssh_source_cidrs" {
  description = "Exact OCI Bastion private /32 addresses allowed to reach host SSH."
  type        = set(string)
  default     = ["10.42.20.100/32", "10.42.20.109/32"]
  validation {
    condition     = var.bastion_ssh_source_cidrs == toset(["10.42.20.100/32", "10.42.20.109/32"])
    error_message = "bastion_ssh_source_cidrs must remain the two technically accepted OCI Bastion /32 addresses."
  }
}

variable "management_plane_evidence_id" {
  description = "Evidence identifier for tested OCI Bastion/Run Command access and independent management authority."
  type        = string
  default     = ""
  validation {
    condition     = length(var.management_plane_evidence_id) <= 256
    error_message = "management_plane_evidence_id must be at most 256 characters."
  }
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
  description = "Evidence identifier proving TLS, PostgreSQL advisory locking, encrypted backup and isolated restore for the independent state backend."
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
