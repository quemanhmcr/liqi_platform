data "oci_core_services" "regional" {}

resource "oci_core_vcn" "main" {
  compartment_id = oci_identity_compartment.environment.id
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${local.prefix}-vcn"
  dns_label      = "liqiv0"
  freeform_tags  = local.always_free_tags
}

resource "oci_core_internet_gateway" "main" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.prefix}-internet-gateway"
  enabled        = true
  freeform_tags  = local.always_free_tags
}

resource "oci_core_service_gateway" "object_storage" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.prefix}-object-storage-service-gateway"

  services {
    service_id = local.object_storage_service.id
  }

  freeform_tags = local.always_free_tags
}

resource "oci_core_route_table" "edge" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.prefix}-edge-routes"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.main.id
    description       = "Public edge egress; ingress remains constrained by the NSG."
  }

  route_rules {
    destination       = local.object_storage_service.cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.object_storage.id
    description       = "Private OCI Object Storage access through the service gateway."
  }

  freeform_tags = local.always_free_tags
}

resource "oci_core_security_list" "empty_baseline" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.prefix}-empty-security-list"


  freeform_tags = local.always_free_tags
}

resource "oci_core_subnet" "edge" {
  compartment_id             = oci_identity_compartment.environment.id
  vcn_id                     = oci_core_vcn.main.id
  cidr_block                 = var.edge_subnet_cidr
  display_name               = "${local.prefix}-edge-subnet"
  dns_label                  = "edge"
  route_table_id             = oci_core_route_table.edge.id
  security_list_ids          = [oci_core_security_list.empty_baseline.id]
  prohibit_public_ip_on_vnic = false
  freeform_tags              = local.always_free_tags
}

resource "oci_core_network_security_group" "host" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "${local.prefix}-host-nsg"
  freeform_tags  = local.always_free_tags
}

resource "oci_core_network_security_group_security_rule" "edge_ingress" {
  for_each = local.edge_ingress_ports

  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"
  stateless                 = false
  description               = each.key == "http" ? "Public HTTP redirect/ACME edge only." : "Public TLS edge only."

  tcp_options {
    destination_port_range {
      min = each.value
      max = each.value
    }
  }
}

resource "oci_core_network_security_group_security_rule" "ssh_ingress" {
  for_each = var.enable_admin_ssh ? var.admin_ssh_source_cidrs : toset([])

  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = each.value
  source_type               = "CIDR_BLOCK"
  stateless                 = false
  description               = "Explicitly allowlisted administrative SSH source."

  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }

  depends_on = [terraform_data.security_guard]
}

resource "oci_core_network_security_group_security_rule" "path_mtu_ingress" {
  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "INGRESS"
  protocol                  = "1"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"
  stateless                 = false
  description               = "ICMP fragmentation-needed for IPv4 path MTU discovery."

  icmp_options {
    type = 3
    code = 4
  }
}

resource "oci_core_network_security_group_security_rule" "web_egress" {
  for_each = toset(["80", "443"])

  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
  stateless                 = false
  description               = "Bounded package, certificate, and external HTTPS/HTTP egress."

  tcp_options {
    destination_port_range {
      min = tonumber(each.value)
      max = tonumber(each.value)
    }
  }
}

resource "oci_core_network_security_group_security_rule" "dns_udp_egress" {
  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "EGRESS"
  protocol                  = "17"
  destination               = "169.254.169.254/32"
  destination_type          = "CIDR_BLOCK"
  stateless                 = false
  description               = "OCI VCN DNS resolver egress."

  udp_options {
    destination_port_range {
      min = 53
      max = 53
    }
  }
}

resource "oci_core_network_security_group_security_rule" "dns_tcp_egress" {
  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "169.254.169.254/32"
  destination_type          = "CIDR_BLOCK"
  stateless                 = false
  description               = "OCI VCN DNS resolver TCP fallback egress."

  tcp_options {
    destination_port_range {
      min = 53
      max = 53
    }
  }
}

resource "oci_core_network_security_group_security_rule" "ntp_egress" {
  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "EGRESS"
  protocol                  = "17"
  destination               = "169.254.169.254/32"
  destination_type          = "CIDR_BLOCK"
  stateless                 = false
  description               = "OCI local NTP service egress."

  udp_options {
    destination_port_range {
      min = 123
      max = 123
    }
  }
}

resource "oci_core_network_security_group_security_rule" "object_storage_egress" {
  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = local.object_storage_service.cidr_block
  destination_type          = "SERVICE_CIDR_BLOCK"
  stateless                 = false
  description               = "Private Object Storage HTTPS through the service gateway."

  tcp_options {
    destination_port_range {
      min = 443
      max = 443
    }
  }
}
