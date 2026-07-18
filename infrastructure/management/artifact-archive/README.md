# Independent V1 artifact archive and bootstrap delivery

Signed host, Mix release and native artifacts are archived on independent management/storage hardware. This directory defines the provider boundary; it is not an OCI Object Storage adapter.

Create the protected archive root on independent storage and mark it deliberately:

```bash
install -d -m 0750 /independent-storage/liqi-artifacts
install -m 0440 /dev/null /independent-storage/liqi-artifacts/.liqi-independent-management-storage
python infrastructure/deployment/archive_host_bundle.py \
  --bundle-dir /protected/build/host-bundle \
  --bundle-id <bundle-id> \
  --public-key /protected/trust/host-bundle.pub.pem \
  --archive-root /independent-storage/liqi-artifacts \
  --approval-reference <approval> \
  --execute \
  --output /protected/evidence/host-bundle-archive-result-v1.json
```

The archive is immutable by bundle ID. Private signing keys never enter the archive or OCI host.

For the first host bootstrap, use OCI Compute Instance Run Command to copy only the public signed bundle triplet into:

```text
/var/lib/liqi/incoming/host/<bundle-id>/
```

The directory and files must be root-owned and not group/world writable. Run Command must not carry private keys, Vault plaintext, PostgreSQL credentials or WireGuard private keys. The host installer verifies the embedded public trust root, exact manifest signature, archive digest and member inventory before any installation.

After the management WireGuard tunnel is live and evidenced, subsequent artifact delivery uses the encrypted private management path. SSH remains masked and no public management ingress is created.
