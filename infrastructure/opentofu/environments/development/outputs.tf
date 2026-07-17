output "oci_host_v0" {
  description = "Direct consumer seam for Senior 2, Senior 3, and Senior 4."
  value       = module.secure_host.oci_host_v0
}

output "infrastructure_output_version" {
  description = "Compatibility gate for consumer tooling."
  value       = module.secure_host.infrastructure_output_version
}

output "replacement_impact" {
  description = "Host replacement and stateful-resource preservation semantics."
  value       = module.secure_host.replacement_impact
}
