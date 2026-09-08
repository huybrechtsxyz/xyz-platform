# Policies

Rules that enforce governance across the deployment lifecycle.

Policies are declared under `spec.policies` in `configuration.yaml` and are
evaluated at six phases: `validate`, `build`, `plan`, `deploy`,
`deploy_before`, and `destroy_before`. Each policy can `deny`, `warn`, or
`audit`, depending on its enforcement mode.

See ADR-0006 for the full design. See `docs/platform/policies.md` for the
complete configuration reference for every built-in type.

---

## Policy Types

| Type                         | Phase                              | Trigger                                                                     |
| ---------------------------- | ---------------------------------- | --------------------------------------------------------------------------- |
| `tenant_zone`                | `plan`                             | Resource region outside the tenant's allowed zones                          |
| `resource_type_restrictions` | `plan`                             | Planned Terraform resource type is denied (or not allowlisted)              |
| `required_labels`            | `build`                            | Selected entity (namespace/resource/module) missing a required label        |
| `naming_pattern`             | `validate`                         | `meta.name` doesn't match a configured regex                                |
| `ref_convention`             | `validate`                         | Remote reference doesn't follow its declared tag convention                 |
| `script`                     | any                                | External command (OPA, custom script) exits non-zero                        |
| `sbom_pinned_versions`       | `build`                            | SBOM component has a missing or floating version                            |
| `sbom_allowed_registries`    | `build`                            | Container image not from an approved registry                               |
| `sbom_denied_packages`       | `build`                            | SBOM component matches a purl/name blocklist pattern                        |
| `sbom_max_components`        | `build`                            | Total (or per-collector) SBOM component count exceeds budget                |
| `sbom_license`               | `build`                            | SBOM component license not on the allow list (or on the deny list)          |
| `cve_max_severity`           | `build`                            | CVE findings at/above a severity exceed a configured count                  |
| `cost_threshold`             | `plan`                             | Estimated monthly cost (from `cost.json`) exceeds a maximum                 |
| `checkov`                    | `build`                            | Checkov finding at/above `severity_gate` in generated Terraform             |
| `opa`                        | any                                | OPA Rego rule returns one or more violations                                |
| `path_convention`            | `validate`                         | File path doesn't match a declared directory-structure convention           |
| `ai_review`                  | `plan`                             | AI-assessed plan risk at/above `risk_threshold`                             |
| `change_reference_required`  | `deploy_before` / `destroy_before` | No `--change-id` supplied for this `deploy run`/`deploy destroy` (ADR-0074) |

---

## Basic Example

```yaml
spec:
  zones:
    - name: prod-eu
      regions: [westeurope, northeurope]
    - name: prod-us
      regions: [eastus, westus]

  policies:
    # Enforce tagging
    - name: require_standard_labels
      type: required_labels
      phase: build
      enforcement: deny
      configuration:
        targets: [namespaces]
        required_labels: [environment, owner, cost_center]

    # Enforce zones
    - name: prod_zones
      type: tenant_zone
      phase: plan
      enforcement: deny

    # AI-powered plan review
    - name: ai_plan_gate
      type: ai_review
      phase: plan
      enforcement: deny
      configuration:
        integration: ai-advisor
        risk_threshold: high     # deny if AI says "high" or "critical"
```

---

## Enforcement Modes

| Mode    | Behavior                                                                              |
| ------- | ------------------------------------------------------------------------------------- |
| `deny`  | Policy failure blocks the command (exit 3)                                            |
| `warn`  | Policy fails but the command continues (logged as a warning)                          |
| `audit` | Result recorded in the deployment manifest only; no console output unless `--verbose` |

---

## Phases

| Phase      | When                      | Example                                       |
| ---------- | ------------------------- | --------------------------------------------- |
| `validate` | After YAML schema check   | Check naming, path, and ref conventions       |
| `build`    | After artifact generation | Check required labels, SBOM, CVEs             |
| `plan`     | After terraform plan      | Check zones, resource types, cost, AI risk    |
| `deploy`   | After terraform apply     | Final checks before the manifest is persisted |

---

## Discovery

Run `strata policy list -f deploy.yaml` to see every configured policy.
Run `strata policy check -f deploy.yaml` to evaluate all policies without deploying.

See `strata help --topic audit` for audit trail integration.
