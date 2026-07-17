# OCI IAM boundary for the V0 host

The V0 host authenticates to OCI exclusively through an instance principal. OCI CLI user credentials and API signing keys are operator credentials and must never be copied to the host.

The dynamic group matches the exact instance OCID rather than every instance in the environment compartment. Host replacement updates the matching rule and can take a short IAM propagation window before Object Storage or Vault calls succeed.

Default capabilities:

- Read bucket metadata in the environment compartment.
- List, read, and create objects only in the designated private PostgreSQL backup bucket. The runtime principal cannot delete backup objects.
- Read only explicitly supplied Vault secret OCIDs; no Vault or secret is created by default.

Anyone with shell access to the instance inherits the instance principal's permissions. Public SSH is therefore disabled by default and may only be enabled for explicit non-world CIDRs.

No policy grants `manage all-resources`, tenancy-wide object access, user credential access, or permission to create infrastructure from the runtime host.
