# Development OCI environment

This root module plans one LIQI V0 node in `ap-singapore-2`. It creates no resource until an owner explicitly runs `tofu apply`; apply is outside the default workflow and is forbidden without project-owner approval.

The project owner fixed V0 at `free-tier-a1-4x24` (`VM.Standard.A1.Flex`, 4 OCPUs, 24 GB RAM). The capacity is mandatory, while its independent cost classification remains `free-trial-only` because current Oracle Always Free documentation does not verify 4 OCPUs and 24 GB RAM as continuously free. Plan requires explicit acknowledgement and does not imply apply approval.

## Inputs

Supply values with environment variables or an uncommitted `terraform.tfvars`. Never commit the tenancy's OCI CLI credentials, PEM, private key, password, token, or secret contents.

## Read-only preparation

```bash
# Copy the tenancy OCID shown by OCI Console's configuration-file preview.
# Do not make infrastructure source parse the local OCI CLI configuration.
export TF_VAR_tenancy_ocid='ocid1.tenancy.oc1..REPLACE_ME'

export TF_VAR_availability_domain="$(oci iam availability-domain list \
  --compartment-id "$TF_VAR_tenancy_ocid" \
  --query 'data[0].name' \
  --raw-output)"

export TF_VAR_oracle_linux_image_ocid="$(oci compute image list \
  --compartment-id "$TF_VAR_tenancy_ocid" \
  --operating-system 'Oracle Linux' \
  --operating-system-version '9' \
  --shape 'VM.Standard.A1.Flex' \
  --sort-by TIMECREATED \
  --sort-order DESC \
  --query 'data[0].id' \
  --raw-output)"

export TF_VAR_admin_ssh_public_key="$(cat /path/to/existing/public-key.pub)"
```

The commands above only read OCI metadata. The public key is not a secret.

## Validation and plan

```bash
tofu init -backend=false
tofu fmt -check -recursive ../..
tofu validate

tofu plan \
  -refresh=false \
  -input=false \
  -lock=false \
  -var='acknowledge_non_always_free_profile=true'
```

Do not save a validation plan to a file. A plan is not approval to apply.
