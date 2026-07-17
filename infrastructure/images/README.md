# Host image policy V0

V0 uses an Oracle Linux 9 AArch64 OCI platform image pinned by OCID. The selected OCID is an explicit environment input and is emitted in `oci_host_v0.host.image_id` for replacement and audit.

A custom image pipeline is intentionally disabled in V0. Introduce one only after a decision note demonstrates measured need such as unacceptable bootstrap time, package-repository drift, compliance scanning, or repeated host replacement. A future image must preserve the same architecture, users, directories, ports, readiness, and replacement contract.

Never select `latest` implicitly during apply. Resolve and review an image OCID during plan preparation, then keep that value fixed for the integration window.
