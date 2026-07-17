locals {
  environment                   = "development"
  infrastructure_output_version = "0.3.0"
  bootstrap_version             = "0.3.0"

  capacity_profile = {
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

  cloud_init_user_data = templatefile("${path.root}/../../../cloud-init/host-bootstrap.yaml.tftpl", {
    bootstrap_version             = local.bootstrap_version
    infrastructure_output_version = local.infrastructure_output_version
    host_contract_schema_version  = "liqi.platform.oci-host/v0"
    enable_admin_ssh              = var.enable_admin_ssh
    enable_http_redirect          = true
    enable_https_edge             = true
    data_device_path              = "/dev/oracleoci/oraclevdb"
    data_mount_path               = "/var/lib/liqi"
    liqi_api_unit                 = file("${path.root}/../../../../services/systemd/liqi-api.service")
    liqi_realtime_unit            = file("${path.root}/../../../../services/systemd/liqi-realtime.service")
    liqi_worker_unit              = file("${path.root}/../../../../services/systemd/liqi-worker.service")
    nginx_config                  = file("${path.root}/../../../edge/nginx.conf")
    nginx_hardening               = file("${path.root}/../../../edge/nginx-liqi-hardening.conf")
  })
}

module "secure_host" {
  source = "../../modules/oci-secure-host-v0"

  tenancy_ocid                        = var.tenancy_ocid
  region                              = var.region
  availability_domain                 = var.availability_domain
  environment                         = local.environment
  capacity_profile                    = local.capacity_profile
  acknowledge_non_always_free_profile = var.acknowledge_non_always_free_profile
  oracle_linux_image_ocid             = var.oracle_linux_image_ocid
  admin_ssh_public_key                = var.admin_ssh_public_key
  enable_admin_ssh                    = var.enable_admin_ssh
  admin_ssh_source_cidrs              = var.admin_ssh_source_cidrs
  enable_http_redirect                = true
  enable_https_edge                   = true
  cloud_init_user_data                = local.cloud_init_user_data
  bootstrap_version                   = local.bootstrap_version
  infrastructure_output_version       = local.infrastructure_output_version
  vault_secret_ocids                  = var.vault_secret_ocids
}
