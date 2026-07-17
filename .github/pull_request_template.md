## Purpose

<!-- One bounded change. Explain the operational or provider value. -->

## Ownership and seams

- Owner:
- Consumers:
- Contract/seam changed:
- Compatibility: additive / versioned / breaking-with-adapter

## Risk controls

- [ ] No secret or credential material is present.
- [ ] No OCI apply, host mutation or production deployment is enabled by default.
- [ ] Resource budgets and failure behavior are declared where applicable.
- [ ] Provider logic is invoked rather than duplicated.
- [ ] Rollback/recovery effect is documented when release semantics change.

## Validation

```text
<exact commands and expected result>
```

## Communication footer

```text
Communication:
Consumers: Senior <n>, ...
Seam: <file, endpoint, output or lifecycle>
Change: <what changed>
Action required: <consumer action or none>
Compatibility: <additive, versioned, breaking-with-adapter>
Validation: <exact command>
Decision note: <ADR path if applicable>
```
