locals {
  infrastructure_root = abspath("${path.module}/../../..")

  cloud_init_files = [
    { path = "/usr/local/libexec/liqi-bootstrap-host", owner = "root:root", permissions = "0755", content = file("${local.infrastructure_root}/bin/liqi-bootstrap-host") },
    { path = "/usr/local/libexec/liqi-prepare-data-volume", owner = "root:root", permissions = "0755", content = file("${local.infrastructure_root}/bin/liqi-prepare-data-volume") },
    { path = "/usr/local/libexec/liqi-install-host-bundle", owner = "root:root", permissions = "0755", content = file("${local.infrastructure_root}/bin/liqi-install-host-bundle") },
    { path = "/etc/liqi/trust/host-bundle-ed25519.pub.pem", owner = "root:root", permissions = "0444", content = var.host_bundle_signing_public_key_pem },
    { path = "/etc/liqi/trust/host-bundle-key-id", owner = "root:root", permissions = "0444", content = "${var.host_bundle_signing_key_id}\n" },
  ]
  cloud_init_user_data = templatefile("${local.infrastructure_root}/cloud-init/host-bootstrap-v1.yaml.tftpl", {
    files          = local.cloud_init_files
    source_git_sha = var.source_git_sha
  })
}

module "v1_live" {
  source = "../../modules/oci-live-v1"

  tenancy_ocid                               = var.tenancy_ocid
  region                                     = var.region
  availability_domain                        = var.availability_domain
  oracle_linux_image_ocid                    = var.oracle_linux_image_ocid
  cloud_init_user_data                       = local.cloud_init_user_data
  source_git_sha                             = var.source_git_sha
  operation_mode                             = var.operation_mode
  apply_approval_reference                   = var.apply_approval_reference
  acknowledge_capacity_availability_and_cost = var.acknowledge_capacity_availability_and_cost
  enable_reserved_public_ip                  = var.enable_reserved_public_ip
  acknowledge_reserved_public_ip             = var.acknowledge_reserved_public_ip
  vault_secret_ocids                         = var.vault_secret_ocids
  management_wireguard_peer_cidr             = var.management_wireguard_peer_cidr
  management_wireguard_port                  = var.management_wireguard_port
  management_plane_evidence_id               = var.management_plane_evidence_id
  state_backend_lock_evidence_id             = var.state_backend_lock_evidence_id
  host_bundle_signing_key_id                 = var.host_bundle_signing_key_id
  host_bundle_signing_public_key_sha256      = sha256(var.host_bundle_signing_public_key_pem)
  acknowledge_host_bundle_signing_key        = var.acknowledge_host_bundle_signing_key
}
