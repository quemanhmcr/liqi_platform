# ADR 5001: V1 OCI A1 4/24 Cost Gate

## Status

Accepted for the V1 OCI live closeout on 2026-07-18.

## Context

V1 requires one `VM.Standard.A1.Flex` host with 4 OCPUs and 24 GiB RAM. Oracle's current Always Free documentation states that A1 receives 1,500 OCPU-hours and 9,000 GB-hours per month, equivalent for an Always Free tenancy to **2 OCPUs and 12 GB RAM total**. Oracle separately documents 200 GB combined boot and block-volume Always Free storage in the tenancy home region.

Primary source:

- <https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm>

Shape visibility, service limits and available capacity do not establish billing eligibility. The mission approval `V1-SELFHOSTED-CONTROL-OCI-LIVE-20260718` explicitly forbids creating paid or unknown resources without a new approval.

## Decision

- Keep the required V1 host envelope fixed at 4 OCPU/24 GiB; do not silently downsize runtime capacity.
- Classify this compute profile as `free-trial-only`, not Always Free.
- Permit a read-only saved-plan review only after explicit capacity/quota/cost acknowledgement.
- Block `approved-apply` in OpenTofu source and in the saved-plan validator under the current approval.
- Require a new reviewed source revision and an explicit paid or tenancy-specific non-billable entitlement approval before apply can be enabled.
- Keep the 180 GiB combined boot/block-volume profile classified as Always Free eligible only after existing tenancy usage is inspected in the home region.

## Consequences

OCI mutation remains zero until both IAM access and cost authority are resolved. A green shape/quota preflight alone is insufficient to remove this blocker.
