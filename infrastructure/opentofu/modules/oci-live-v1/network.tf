resource "oci_core_vcn" "main" {
  compartment_id = oci_identity_compartment.environment.id
  cidr_blocks    = [var.network_config.vcn_cidr]
  display_name   = var.resource_names.vcn
  dns_label      = var.network_config.vcn_dns_label
  freeform_tags  = local.common_tags
}

resource "oci_core_internet_gateway" "main" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = var.resource_names.internet_gateway
  enabled        = true
  freeform_tags  = local.common_tags
}

resource "oci_core_route_table" "edge" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = var.resource_names.route_table
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.main.id
    description       = "Public edge egress."
  }
  freeform_tags = local.common_tags
}

resource "oci_core_security_list" "empty" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = var.resource_names.security_list
  freeform_tags  = local.common_tags
}

resource "oci_core_subnet" "edge" {
  compartment_id             = oci_identity_compartment.environment.id
  vcn_id                     = oci_core_vcn.main.id
  cidr_block                 = var.network_config.edge_subnet_cidr
  display_name               = var.resource_names.subnet
  dns_label                  = var.network_config.edge_subnet_label
  route_table_id             = oci_core_route_table.edge.id
  security_list_ids          = [oci_core_security_list.empty.id]
  prohibit_public_ip_on_vnic = false
  freeform_tags              = local.common_tags
}

resource "oci_core_network_security_group" "host" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = var.resource_names.nsg
  freeform_tags  = local.common_tags
}

resource "oci_core_network_security_group_security_rule" "edge_ingress" {
  for_each = local.public_edge_ports

  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"
  stateless                 = false
  description               = each.key == "http" ? "HTTP redirect and ACME only." : "Public HTTPS edge only."
  tcp_options {
    destination_port_range {
      min = each.value
      max = each.value
    }
  }
}

resource "oci_core_network_security_group_security_rule" "path_mtu_ingress" {
  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "INGRESS"
  protocol                  = "1"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"
  stateless                 = false
  description               = "IPv4 fragmentation-needed for path MTU discovery."
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
  description               = "Package, certificate and HTTPS egress."
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
  description               = "OCI VCN resolver UDP."
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
  description               = "OCI VCN resolver TCP fallback."
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
  description               = "OCI local NTP service."
  udp_options {
    destination_port_range {
      min = 123
      max = 123
    }
  }
}

resource "oci_core_network_security_group_security_rule" "management_tunnel_egress" {
  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "EGRESS"
  protocol                  = "17"
  destination               = var.management_wireguard_peer_cidr
  destination_type          = "CIDR_BLOCK"
  stateless                 = false
  description               = "Outbound-only encrypted WireGuard tunnel to the independent management plane."
  udp_options {
    destination_port_range {
      min = var.management_wireguard_port
      max = var.management_wireguard_port
    }
  }
}
