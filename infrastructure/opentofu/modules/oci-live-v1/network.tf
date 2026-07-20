data "oci_core_services" "regional" {}

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

resource "oci_core_nat_gateway" "outbound" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = var.resource_names.nat_gateway
  block_traffic  = false
  freeform_tags  = local.common_tags
}

resource "oci_core_service_gateway" "oracle_services" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = var.resource_names.service_gateway
  services {
    service_id = local.regional_oracle_service.id
  }
  freeform_tags = local.common_tags
}

# Retained route table for the legacy non-publicly-addressed host. Its stable
# state address avoids a risky state move during the blue-green correction.
resource "oci_core_route_table" "edge" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = var.resource_names.legacy_route_table
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.outbound.id
    description       = "Private workload internet egress through NAT."
  }
  route_rules {
    destination       = local.regional_oracle_service.cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.oracle_services.id
    description       = "Private Oracle Services path."
  }
  freeform_tags = local.common_tags

  lifecycle {
    prevent_destroy = true
  }
}

# Existing private route table adopted for the new primary and recovery host.
resource "oci_core_route_table" "private_host" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = var.resource_names.route_table
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.outbound.id
    description       = "Private workload internet egress through NAT."
  }
  route_rules {
    destination       = local.regional_oracle_service.cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.oracle_services.id
    description       = "Private Oracle Services path."
  }
  freeform_tags = local.common_tags

  lifecycle {
    prevent_destroy = true
  }
}

# A separate route table is used only by the public NLB edge subnet. Enabling
# the IGW therefore never gives the primary instance a direct public route.
resource "oci_core_route_table" "public_edge" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = var.resource_names.public_edge_route_table
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.main.id
    description       = "Public NLB edge route only."
  }
  freeform_tags = local.common_tags
}

resource "oci_core_security_list" "empty" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = var.resource_names.security_list

  # Preserve the technically accepted subnet baseline: no ingress and one
  # stateful outbound rule. Workload NSGs remain the least-privilege egress
  # authority attached to the primary and NLB.
  egress_security_rules {
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    protocol         = "all"
    stateless        = false
    description      = "Stateful outbound traffic only; no unsolicited ingress"
  }

  freeform_tags = local.common_tags
}

# Retain the original public-IP-capable subnet without replacement. No
# production backend is attached to this subnet after the blue-green apply.
resource "oci_core_subnet" "edge" {
  compartment_id             = oci_identity_compartment.environment.id
  vcn_id                     = oci_core_vcn.main.id
  cidr_block                 = var.network_config.legacy_host_subnet_cidr
  display_name               = var.resource_names.legacy_subnet
  dns_label                  = var.network_config.legacy_host_subnet_label
  route_table_id             = oci_core_route_table.edge.id
  security_list_ids          = [oci_core_security_list.empty.id]
  prohibit_public_ip_on_vnic = false
  freeform_tags              = local.common_tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_core_subnet" "private_host" {
  compartment_id             = oci_identity_compartment.environment.id
  vcn_id                     = oci_core_vcn.main.id
  cidr_block                 = var.network_config.host_subnet_cidr
  display_name               = var.resource_names.subnet
  dns_label                  = var.network_config.host_subnet_label
  route_table_id             = oci_core_route_table.private_host.id
  security_list_ids          = [oci_core_security_list.empty.id]
  prohibit_public_ip_on_vnic = true
  freeform_tags              = local.common_tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_core_subnet" "public_edge" {
  compartment_id             = oci_identity_compartment.environment.id
  vcn_id                     = oci_core_vcn.main.id
  cidr_block                 = var.network_config.public_edge_subnet_cidr
  display_name               = var.resource_names.public_edge_subnet
  dns_label                  = var.network_config.public_edge_subnet_label
  route_table_id             = oci_core_route_table.public_edge.id
  security_list_ids          = [oci_core_security_list.empty.id]
  prohibit_public_ip_on_vnic = false
  freeform_tags              = local.common_tags
}

# Adopt the tech-lead-managed workload NSG. SSH is restricted to the exact
# OCI Bastion private addresses; public web ingress reaches the host only from
# the NLB NSG.
resource "oci_core_network_security_group" "host" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = var.resource_names.nsg
  freeform_tags  = local.common_tags
}

resource "oci_core_network_security_group" "public_edge" {
  compartment_id = oci_identity_compartment.environment.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = var.resource_names.nlb_nsg
  freeform_tags  = local.common_tags
}

resource "oci_core_network_security_group_security_rule" "bastion_ssh_ingress" {
  for_each = local.bastion_ssh_sources

  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = each.key
  source_type               = "CIDR_BLOCK"
  stateless                 = false
  description               = each.value
  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}

resource "oci_core_network_security_group_security_rule" "host_edge_ingress" {
  for_each = local.public_edge_ports

  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = oci_core_network_security_group.public_edge.id
  source_type               = "NETWORK_SECURITY_GROUP"
  stateless                 = false
  description               = "${upper(each.key)} from the LIQI public NLB only."
  tcp_options {
    destination_port_range {
      min = each.value
      max = each.value
    }
  }
}

resource "oci_core_network_security_group_security_rule" "host_path_mtu_ingress" {
  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "INGRESS"
  protocol                  = "1"
  source                    = var.network_config.vcn_cidr
  source_type               = "CIDR_BLOCK"
  stateless                 = false
  description               = "VCN path-MTU fragmentation-needed messages."
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
  description               = "Package, certificate and HTTPS egress through NAT."
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

resource "oci_core_network_security_group_security_rule" "oracle_services_egress" {
  network_security_group_id = oci_core_network_security_group.host.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = local.regional_oracle_service.cidr_block
  destination_type          = "SERVICE_CIDR_BLOCK"
  stateless                 = false
  description               = "Oracle Services HTTPS through Service Gateway."
  tcp_options {
    destination_port_range {
      min = 443
      max = 443
    }
  }
}

resource "oci_core_network_security_group_security_rule" "public_edge_ingress" {
  for_each = local.public_edge_ports

  network_security_group_id = oci_core_network_security_group.public_edge.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"
  stateless                 = false
  description               = each.key == "http" ? "Public HTTP redirect and ACME to NLB." : "Public TLS/WebSocket pass-through to NLB."
  tcp_options {
    destination_port_range {
      min = each.value
      max = each.value
    }
  }
}

resource "oci_core_network_security_group_security_rule" "public_edge_path_mtu_ingress" {
  network_security_group_id = oci_core_network_security_group.public_edge.id
  direction                 = "INGRESS"
  protocol                  = "1"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"
  stateless                 = false
  description               = "Public path-MTU fragmentation-needed messages."
  icmp_options {
    type = 3
    code = 4
  }
}

resource "oci_core_network_security_group_security_rule" "public_edge_egress" {
  for_each = local.public_edge_ports

  network_security_group_id = oci_core_network_security_group.public_edge.id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = oci_core_network_security_group.host.id
  destination_type          = "NETWORK_SECURITY_GROUP"
  stateless                 = false
  description               = "NLB ${upper(each.key)} backend traffic to workload NSG only."
  tcp_options {
    destination_port_range {
      min = each.value
      max = each.value
    }
  }
}

resource "oci_core_public_ip" "reserved" {
  count = var.enable_reserved_public_ip ? 1 : 0

  compartment_id = oci_identity_compartment.environment.id
  display_name   = var.resource_names.reserved_public_ip
  lifetime       = "RESERVED"
  freeform_tags  = local.common_tags
}

resource "oci_network_load_balancer_network_load_balancer" "edge" {
  compartment_id                 = oci_identity_compartment.environment.id
  display_name                   = var.resource_names.network_load_balancer
  subnet_id                      = oci_core_subnet.public_edge.id
  is_private                     = false
  is_preserve_source_destination = false
  is_symmetric_hash_enabled      = false
  network_security_group_ids     = [oci_core_network_security_group.public_edge.id]
  nlb_ip_version                 = "IPV4"
  freeform_tags                  = local.common_tags

  dynamic "reserved_ips" {
    for_each = oci_core_public_ip.reserved[*].id
    content {
      id = reserved_ips.value
    }
  }
}

resource "oci_network_load_balancer_backend_set" "edge" {
  for_each = local.public_edge_ports

  name                     = "liqi-${each.key}-backends"
  network_load_balancer_id = oci_network_load_balancer_network_load_balancer.edge.id
  policy                   = "FIVE_TUPLE"
  is_fail_open             = false
  is_preserve_source       = false
  ip_version               = "IPV4"

  health_checker {
    protocol           = "TCP"
    port               = each.value
    interval_in_millis = 10000
    timeout_in_millis  = 3000
    retries            = 3
  }
}

resource "oci_network_load_balancer_backend" "host" {
  for_each = local.public_edge_ports

  network_load_balancer_id = oci_network_load_balancer_network_load_balancer.edge.id
  backend_set_name         = oci_network_load_balancer_backend_set.edge[each.key].name
  target_id                = oci_core_instance.host.id
  port                     = each.value
  is_backup                = false
  is_drain                 = false
  is_offline               = !var.public_backend_enabled
  weight                   = 1
}

resource "oci_network_load_balancer_listener" "edge" {
  for_each = local.public_edge_ports

  name                     = "liqi-${each.key}-listener"
  network_load_balancer_id = oci_network_load_balancer_network_load_balancer.edge.id
  default_backend_set_name = oci_network_load_balancer_backend_set.edge[each.key].name
  port                     = each.value
  protocol                 = "TCP"
  ip_version               = "IPV4"
  tcp_idle_timeout         = 1800
  is_ppv2enabled           = false
}
