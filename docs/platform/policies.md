# Policies

Declarative guardrails evaluated at specific lifecycle phases before or after infrastructure changes are applied.

**Built-in types:** `tenant_zone` | `resource_type_restrictions` | `required_labels` | `naming_pattern` | `ref_convention` | `script` | `sbom_pinned_versions` | `sbom_allowed_registries` | `sbom_denied_packages` | `sbom_max_components` | `sbom_license` | `cve_max_severity` | `cost_threshold` | `checkov` | `opa` | `path_convention` | `layer_agreement` | `ai_review` | `change_reference_required` | **Phases:** `validate` / `build` / `plan` / `deploy` / `deploy_before` / `destroy_before` | **Enforcement:** `deny` / `warn` / `audit` | **Declared in:** `configuration.spec.policies`

---

## Overview

Policies let you declare constraints that strata enforces automatically as part of its standard commands — no extra scripts or wrappers required.

Unlike [lifecycle hooks](lifecycles.md), which run arbitrary scripts at named points in the pipeline, policies evaluate structured data against a rule and produce a **pass / fail result**. The enforcement level determines what happens on failure: block the pipeline, emit a warning, or silently record the result.

| Feature          | Lifecycle hooks                         | Policies                                       |
| ---------------- | --------------------------------------- | ---------------------------------------------- |
| What they do     | Run arbitrary scripts                   | Evaluate structured rules against config/plans |
| Where declared   | `lifecycle:` on any YAML document       | `configuration.spec.policies`                  |
| Failure mode     | Non-zero exit code stops the pipeline   | Configurable: `deny`, `warn`, or `audit`       |
| Scope            | Single document (workspace/namespace/…) | Whole deployment (cross-document visibility)   |
| Example use case | Run a backup before apply               | Prevent resources in disallowed regions        |

The practical difference: if you need to *enforce* something about the infrastructure plan or configuration values, use a policy. If you need to *do* something at a lifecycle point (download a file, rotate a secret, send a notification), use a lifecycle hook.

---

## Configuration

Policies are declared in your `configuration.yaml` under `spec.policies`. Each policy is an item in the list.

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: my-configuration
spec:
  policies:
    - name: zone_enforcement
      type: tenant_zone
      phase: plan
      enforcement: deny
      description: "Reject any Terraform resource targeting a disallowed region"
```

### Fields

| Field           | Required | Type    | Description                                                                        |
| --------------- | -------- | ------- | ---------------------------------------------------------------------------------- |
| `name`          | Yes      | string  | Unique identifier for this policy. Used in logs and manifest records.              |
| `type`          | Yes      | string  | Policy implementation to use. See [Policy types](#policy-types).                   |
| `phase`         | Yes      | string  | Lifecycle phase when this policy runs. See [Phases](#phases).                      |
| `enforcement`   | No       | string  | What happens on failure: `deny`, `warn`, or `audit`. Default: `deny`.              |
| `enabled`       | No       | bool    | Set to `false` to temporarily disable without removing the entry. Default: `true`. |
| `description`   | No       | string  | Human-readable note recorded in logs and the deployment manifest.                  |
| `configuration` | No       | mapping | Type-specific settings. Required by some policy types (e.g. `script`).             |

---

## Policy Types

### Built-in types

| Type                         | Phase                              | What it checks                                                                           |
| ---------------------------- | ---------------------------------- | ---------------------------------------------------------------------------------------- |
| `tenant_zone`                | `plan`                             | Terraform resource regions vs. the tenant's allowed zones                                |
| `resource_type_restrictions` | `plan`                             | Allow or deny Terraform resource types (allowlist or blocklist)                          |
| `required_labels`            | `build`                            | Required labels present on selected entities (namespaces/resources/modules)              |
| `naming_pattern`             | `validate`                         | `meta.name` fields match a configured regex pattern                                      |
| `ref_convention`             | `validate`                         | Remote references follow configured tag naming conventions                               |
| `script`                     | any                                | Delegates to an external command (OPA, Checkov, custom script)                           |
| `sbom_pinned_versions`       | `build`                            | SBOM components have pinned, non-floating version tags                                   |
| `sbom_allowed_registries`    | `build`                            | Container images originate only from approved registries                                 |
| `sbom_denied_packages`       | `build`                            | No SBOM component matches a purl/name blocklist pattern                                  |
| `sbom_max_components`        | `build`                            | Total SBOM component count stays within a configured budget                              |
| `sbom_license`               | `build`                            | SBOM component licenses match an allow/deny list (via `strata:license` property)         |
| `cve_max_severity`           | `build`                            | CVE vulnerability findings stay within a configured severity threshold                   |
| `cost_threshold`             | `plan`                             | Estimated monthly cost (from `cost.json`) stays within a configured maximum              |
| `checkov`                    | `build`                            | Runs Checkov against generated Terraform and gates on a severity threshold               |
| `opa`                        | any                                | Evaluates a Rego rule via an OPA server or the `opa eval` CLI                            |
| `path_convention`            | `validate`                         | Files on disk follow declared directory-structure conventions                            |
| `layer_agreement`            | `validate`                         | Explicit `spec.layers.segments` values agree with what the file's path derives           |
| `ai_review`                  | `plan`                             | AI-assessed risk of a Terraform plan stays below a configured threshold                  |
| `change_reference_required`  | `deploy_before` / `destroy_before` | An external change/ticket reference (`--change-id`) was supplied for this deploy/destroy |

### `tenant_zone`

Evaluates at the `plan` phase, after `terraform plan` produces a plan JSON and before `terraform apply` runs.

It reads:

1. The tenant's allowed zones from `tenant.auto.tfvars.json` in the working directory.
2. The zone-to-region mapping from `configuration.spec.zones`.
3. Every `create` and `update` resource change in the plan JSON.

For each resource change, it checks whether the target region (from `location` or `region` in the plan) falls within the tenant's allowed regions. Any resource targeting a disallowed region is reported as a violation.

No `configuration` block is needed — the policy reads zone data from the configuration file automatically.

### `resource_type_restrictions`

Evaluates at the `plan` phase. Reads every `create` and `update` resource change from the Terraform plan JSON and checks the resource type against a configured list.

Two modes:

- **`deny`** (default) — the policy fails if any planned resource has a type on the denied list. Use this to block specific resource types outright.
- **`allow`** — the policy fails if any planned resource has a type that is _not_ on the allowed list. Use this for a strict allowlist.

```yaml
# Block bare VMs — use managed node pools instead
- name: no_bare_vms
  type: resource_type_restrictions
  phase: plan
  enforcement: deny
  configuration:
    mode: deny
    types:
      - azurerm_virtual_machine
      - aws_instance
      - google_compute_instance

# Strict allowlist
- name: approved_types_only
  type: resource_type_restrictions
  phase: plan
  enforcement: deny
  configuration:
    mode: allow
    types:
      - azurerm_kubernetes_cluster
      - azurerm_storage_account
```

**Configuration fields:**

| Field     | Type           | Default            | Description                                                                        |
| --------- | -------------- | ------------------ | ---------------------------------------------------------------------------------- |
| `mode`    | string         | `deny`             | Operating mode: `deny` (block listed types) or `allow` (permit only listed types). |
| `types`   | list of string | required           | Terraform resource type strings (e.g. `azurerm_virtual_machine`, `aws_instance`).  |
| `actions` | list of string | `[create, update]` | Which plan actions trigger the check (`create`, `update`, `delete`, `replace`).    |

Skipped gracefully when no plan data is available or `types` is empty.

### `required_labels`

Evaluates at the `build` phase, after all builders have completed and the platform artifact has been generated.

It checks that every entity in the configured `targets` (default: `namespaces`) has all labels listed in `configuration.required_labels`. Any matching entity missing a required label is reported as a violation.

Skipped gracefully when no platform artifact has been generated, or when `required_labels` is not configured.

**Configuration fields:**

| Field                  | Type           | Default          | Description                                                                                     |
| ---------------------- | -------------- | ---------------- | ----------------------------------------------------------------------------------------------- |
| `required_labels`      | list of string | required         | Label keys that must be present on every matched entity.                                        |
| `targets`              | list of string | `[namespaces]`   | Entity collections to check: `namespaces`, `resources`, `modules`.                              |
| `filter.name`          | list of glob   | none (no filter) | Only entities whose name matches one of these `fnmatch` patterns are checked (e.g. `"prod-*"`). |
| `filter.resource_type` | list of string | none (no filter) | `resources` target only — only resources whose `properties.resource_type` is in this list.      |

Use `targets` + `filter` to declare several narrowly-scoped policies instead of one policy that forces every entity to carry the same labels:

```yaml
# Every namespace must carry standard cost-tracking labels
- name: require_standard_labels
  type: required_labels
  phase: build
  enforcement: deny
  configuration:
    targets: [namespaces]
    required_labels:
      - environment
      - project
      - owner

# Only VM resources must carry an 'owner' label — other resource types are untouched
- name: vm_owner_label
  type: required_labels
  phase: build
  enforcement: warn
  configuration:
    targets: [resources]
    filter:
      resource_type: [vm]
    required_labels: [owner]

# Only resources whose name starts with 'prod-' need a cost_center label
- name: prod_cost_center
  type: required_labels
  phase: build
  enforcement: deny
  configuration:
    targets: [resources]
    filter:
      name: ["prod-*"]
    required_labels: [cost_center]
```

### `naming_pattern`

Evaluates at the `validate` phase, after structural validation completes. Requires the `--deep` flag on `strata validate` to load the configuration service.

It checks whether the `meta.name` field in the loaded configuration file matches the regex in `configuration.pattern`, using a full-string match.

Skipped gracefully when no configuration service has been loaded (i.e. `strata validate` was run without `--deep`).

```yaml
- name: naming_convention
  type: naming_pattern
  phase: validate
  enforcement: warn
  configuration:
    pattern: "^[a-z][a-z0-9-]*$"
```

### `script`

Delegates policy evaluation to an external command — OPA, Checkov, a custom shell script, or any executable that can read JSON from stdin.

strata launches the command, writes a JSON context object to its standard input, and waits for it to exit. Exit code `0` is a pass; any non-zero exit code is a failure. Stdout is captured as the violation message.

**Context sent on stdin:**

```json
{"phase": "plan", "work_path": "/path/to/workspace"}
```

The `script` type can run at any phase — set `phase` to whichever phase you want the evaluation to occur. Timeout defaults to `30` seconds. Skipped gracefully when no `command` is configured.

```yaml
- name: opa_check
  type: script
  phase: plan
  enforcement: deny
  configuration:
    command: "opa eval --data policy.rego --input /dev/stdin -"
    timeout: 30
```

### `ref_convention`

Evaluates at the `validate` phase. Checks that remote references (git tags, branches, or commit SHAs) in deployments and environments follow the naming conventions declared on each remote itself.

Useful for enforcing that production environments only pin to semantic version tags (e.g., `v1.2.0`), not branch names like `main` or raw commit SHAs. Works in tandem with [ADR 0017 (tag-based release workflows)](../decisions/0017-tag-based-release-workflow-option-c.md) to validate release discipline and detect accidental manual pins.

This policy is config-free. Patterns are declared once, on the remote itself, via `spec.remotes[].conventions` — never duplicated inside the policy:

```yaml
spec:
  remotes:
    - name: my-service
      repository: https://github.com/acme/my-service
      reference: main
      conventions:
        release_pattern: "^v\\d+\\.\\d+\\.\\d+$"    # semver only
        quality_pattern: "^tested(-\\d+)?$"          # quality gate tags
    - name: tf-landscape
      repository: https://github.com/acme/tf-landscape
      reference: main
      conventions:
        release_pattern: "^v\\d+\\.\\d+\\.\\d+$"

  policies:
    - name: release_tag_conventions
      type: ref_convention
      phase: validate
      enforcement: warn
```

**`RemoteConventionsModel` fields** (on `spec.remotes[].conventions`):

| Field             | Type   | Description                                                                    |
| ----------------- | ------ | ------------------------------------------------------------------------------ |
| `release_pattern` | string | Full-match regex for release tags (e.g., `"^v\\d+\\.\\d+\\.\\d+$"` for semver) |
| `quality_pattern` | string | Full-match regex for quality-gate tags (e.g., `"^tested(-\\d+)?$"`)            |

At least one pattern must be set for a remote to be checked. Remotes without `conventions` are skipped entirely — no default/implicit pattern is ever assumed.

Skipped gracefully when no deployments/environments are found, or when no remotes declare `conventions`.

**Validation logic:**

1. For each deployment and environment in the workspace
2. For each `spec.overrides.remotes[]` reference
3. Look up the remote by name in `configuration.spec.remotes[]`; if it has `conventions`, check the reference against them
4. Report a violation if the reference matches neither pattern

**Example violations:**

```
environment 'production' → remote 'my-service' reference 'main' 
  does not match expected pattern (release: ^v\d+\.\d+\.\d+$ or quality: ^tested(-\d+)?$)

deployment 'acme' → remote 'tf-landscape' reference 'abc1234f' 
  does not match expected pattern (release: ^v\d+\.\d+\.\d+$)
```

### `sbom_pinned_versions`

Evaluates at the `build` phase, after the SBOM has been generated. Checks that every SBOM component from the selected collectors has an explicit, deterministic version — no missing versions, no floating tags (e.g. `:latest`, any tag marked `strata:tag-stability=floating`).

Skipped gracefully when no SBOM components are available.

| `configuration` key | Type           | Default | Description                                                              |
| ------------------- | -------------- | ------- | ------------------------------------------------------------------------ |
| `collectors`        | list of string | all     | Restrict checks to these collector names (`image`, `compose`, `deps`, …) |
| `allow_latest`      | bool           | `false` | When `true`, the literal tag `latest` is not treated as a violation      |
| `require_digest`    | bool           | `false` | When `true`, every component's purl must include a `@sha256:…` digest    |

```yaml
- name: pin_all_images
  type: sbom_pinned_versions
  phase: build
  enforcement: deny
  configuration:
    collectors: [image, compose]
    allow_latest: false
    require_digest: false
```

### `sbom_allowed_registries`

Evaluates at the `build` phase. Checks that every container image component (`pkg:docker/…` or `pkg:oci/…`) originates from one of the configured registry prefixes. Non-docker/OCI purls (Helm charts, Python packages, etc.) are ignored.

Skipped gracefully when no SBOM components are available or when `allowed` is not configured.

| `configuration` key | Type           | Default            | Description                                                         |
| ------------------- | -------------- | ------------------ | ------------------------------------------------------------------- |
| `allowed`           | list of string | *(required)*       | Registry prefix strings — e.g. `ghcr.io/myorg`, `mcr.microsoft.com` |
| `collectors`        | list of string | `[image, compose]` | Restrict checks to these collector names                            |

Single-segment image names (e.g. `pkg:docker/nginx@1.25`) are resolved to `docker.io/library`.

```yaml
- name: trusted_registries
  type: sbom_allowed_registries
  phase: build
  enforcement: deny
  configuration:
    allowed:
      - ghcr.io/myorg
      - mcr.microsoft.com
      - docker.io/library
```

### `sbom_denied_packages`

Evaluates at the `build` phase. Blocks any SBOM component whose `purl` or `name` matches one of the configured glob patterns (Python `fnmatch` syntax). Use this for emergency CVE responses, deprecated package blocks, or license violations.

Skipped gracefully when no SBOM components are available or when `denied` is not configured.

| `configuration` key | Type           | Default                         | Description                                                        |
| ------------------- | -------------- | ------------------------------- | ------------------------------------------------------------------ |
| `denied`            | list of string | *(required)*                    | purl glob patterns (`pkg:pypi/bad-pkg@*`) or name globs (`log4j*`) |
| `reason`            | string         | `"Package is on the deny list"` | Custom message appended to each violation                          |

```yaml
- name: block_vulnerable
  type: sbom_denied_packages
  phase: build
  enforcement: deny
  configuration:
    denied:
      - "pkg:pypi/setuptools@<70.0.0"
      - "pkg:npm/event-stream@*"
      - "log4j*"
    reason: "Blocked by security policy SEC-2026-041"
```

### `sbom_max_components`

Evaluates at the `build` phase. Enforces an upper bound on the total number of SBOM components and optionally per-collector limits. Useful as a complexity budget to detect dependency sprawl before it ships.

Skipped gracefully when no SBOM components are available or when neither `max_count` nor `per_collector` is configured.

| `configuration` key | Type             | Default | Description                                         |
| ------------------- | ---------------- | ------- | --------------------------------------------------- |
| `max_count`         | int              | none    | Maximum total component count across all collectors |
| `per_collector`     | map string → int | none    | Per-collector limits keyed by collector name        |

```yaml
- name: complexity_budget
  type: sbom_max_components
  phase: build
  enforcement: warn
  configuration:
    max_count: 200
    per_collector:
      image: 50
      deps: 150
```

### `sbom_license`

Evaluates at the `build` phase. Checks each component's `strata:license` property against configurable allow and deny lists using `fnmatch` glob patterns. Useful for enforcing open-source license compliance without external API calls — works entirely offline with data already in the SBOM.

Collectors or lockfile parsers that capture license metadata set `properties["strata:license"] = "MIT"` on their `SbomComponentModel`. Components without this property are governed by the `unknown_action` setting.

Skipped gracefully when no SBOM components are available or when neither `allowed` nor `denied` is configured.

| `configuration` key | Type           | Default  | Description                                                             |
| ------------------- | -------------- | -------- | ----------------------------------------------------------------------- |
| `allowed`           | list of string | none     | SPDX license globs — only these are permitted (e.g. `MIT`, `BSD-*`)     |
| `denied`            | list of string | none     | SPDX license globs — these are always blocked (e.g. `GPL-*`, `AGPL-*`)  |
| `unknown_action`    | string         | `"warn"` | What to do when `strata:license` is missing: `allow`, `warn`, or `deny` |

When both `allowed` and `denied` are configured, `denied` is checked first — an explicit deny always wins.

```yaml
- name: license_compliance
  type: sbom_license
  phase: build
  enforcement: deny
  configuration:
    allowed:
      - MIT
      - Apache-2.0
      - BSD-*
      - ISC
    denied:
      - GPL-3.0-only
      - AGPL-*
    unknown_action: warn
```

---

### `cost_threshold`

Evaluates at the `plan` phase. Reads `cost.json` from the deployment build directory
(generated by `strata cost show`) and checks that the total estimated monthly cost
does not exceed a configured maximum.

Skipped gracefully when:
- `cost.json` does not exist (run `strata cost show -f deploy.yaml` first)
- `max_monthly` is not configured
- The total cost is zero or unparseable

When `environment_pattern` is set, the policy only activates for environments whose
name matches the glob pattern — useful for applying different thresholds per tier.

| `configuration` key   | Type   | Default | Description                                                                          |
| --------------------- | ------ | ------- | ------------------------------------------------------------------------------------ |
| `max_monthly`         | number | —       | Maximum allowed monthly cost. Required.                                              |
| `currency`            | string | `USD`   | Currency label used in violation messages (informational — not used for conversion). |
| `environment_pattern` | string | none    | Glob pattern for environment names. Policy is skipped for non-matching environments. |

**Prerequisites:** `strata cost show` must run before `strata deploy` to generate `cost.json`.
The policy reads the pre-computed cost file — it does not call Infracost itself.

```yaml
# Block production deployments over €10,000/month
- name: prod_cost_gate
  type: cost_threshold
  phase: plan
  enforcement: deny
  description: "Block deployments with monthly cost over €10,000"
  configuration:
    max_monthly: 10000
    currency: EUR

# Warn when dev environments exceed €500/month
- name: dev_cost_cap
  type: cost_threshold
  phase: plan
  enforcement: warn
  configuration:
    max_monthly: 500
    environment_pattern: "dev*"
    currency: EUR
```

### `cve_max_severity`

Evaluates at the `build` phase, after the SBOM has been generated. Fails when the number of CVE findings at or above `max_severity` exceeds `max_count` (default: `0`).

If a CVE audit was already computed during the current build (`strata build run --audit`), the policy reuses that result. Otherwise it runs the CVE scanner itself against the build's `sbom.json`. Findings listed in `.strata/cve-allowed.yaml` (with a non-expired `expires` date) are excluded before counting.

Skipped gracefully when: no SBOM is available, no CVE scanner is installed, or `max_severity` is not configured.

| `configuration` key  | Type   | Default      | Description                                                                            |
| -------------------- | ------ | ------------ | -------------------------------------------------------------------------------------- |
| `max_severity`       | string | *(required)* | Minimum severity that counts toward the violation: `CRITICAL`\|`HIGH`\|`MEDIUM`\|`LOW` |
| `max_count`          | int    | `0`          | Fail when the number of findings at or above `max_severity` exceeds this               |
| `severity_threshold` | string | `MEDIUM`     | Minimum severity requested from the scanner itself                                     |

```yaml
- name: no_critical_cves
  type: cve_max_severity
  phase: build
  enforcement: deny
  description: "Block builds with CRITICAL vulnerabilities"
  configuration:
    max_severity: CRITICAL
    max_count: 0
    severity_threshold: MEDIUM
```

### `checkov`

Evaluates at the `build` phase. Runs [Checkov](https://www.checkov.io/) against the Terraform artifacts generated by `strata build run` and fails when findings at or above `severity_gate` are detected.

The policy locates the Terraform directory automatically (deployment-scoped build path, `{build_path}/terraform/`, or `{build_path}/` itself — whichever contains `.tf` files first).

Skipped gracefully when: Checkov is not installed, no Terraform artifacts exist in the build path, or the scan subprocess fails.

| `configuration` key | Type           | Default     | Description                                                                 |
| ------------------- | -------------- | ----------- | --------------------------------------------------------------------------- |
| `framework`         | string         | `terraform` | Checkov framework to scan                                                   |
| `severity_gate`     | string         | `high`      | Minimum severity that fails the policy: `critical`\|`high`\|`medium`\|`low` |
| `skip_checks`       | list of string | none        | Checkov check IDs to suppress (e.g. `CKV_AWS_1`)                            |
| `include_checks`    | list of string | none        | If set, run only these checks                                               |
| `custom_checks_dir` | string         | none        | Directory of custom Checkov checks                                          |
| `timeout`           | int            | `120`       | Scan subprocess timeout, in seconds                                         |

```yaml
- name: terraform_security_baseline
  type: checkov
  phase: build
  enforcement: deny
  description: "Block builds with HIGH or CRITICAL Checkov findings"
  configuration:
    framework: terraform
    severity_gate: high
    skip_checks:
      - CKV_AWS_1
      - CKV_AWS_20
    timeout: 120
```

### `opa`

Evaluates a Rego rule against a JSON document describing the current deployment (platform artifact, configuration, deployment model, and Terraform plan data, where available). Can run at any phase.

Two evaluation modes are tried in order: an OPA server reachable at `configuration.endpoint` (or the `OPA_ENDPOINT` environment variable), or the `opa eval` CLI as a stateless subprocess. The Rego rule must return a set of violation strings.

Skipped gracefully when: neither an OPA server nor the `opa` binary is available, `rule` is not configured, or `policy_dir` (CLI mode) does not exist.

| `configuration` key | Type   | Default      | Description                                                               |
| ------------------- | ------ | ------------ | ------------------------------------------------------------------------- |
| `rule`              | string | *(required)* | Fully-qualified Rego rule to evaluate (e.g. `data.strata.zones.deny`)     |
| `policy_dir`        | string | none         | Directory of `.rego` files, resolved relative to the workspace (CLI mode) |
| `endpoint`          | string | none         | OPA server URL (HTTP mode); falls back to `OPA_ENDPOINT` env var          |
| `timeout`           | int    | `30`         | Evaluation timeout, in seconds                                            |

```yaml
- name: zone_enforcement
  type: opa
  phase: build
  enforcement: deny
  description: "Enforce tenant zone restrictions via OPA"
  configuration:
    rule: "data.strata.zones.deny"
    policy_dir: ".strata/policies/"
    timeout: 30
```

### `path_convention`

Evaluates at the `validate` phase. Checks that the file currently being validated follows the directory-structure conventions declared in `configuration.spec.paths` — a `scope` glob, a `pattern` with `{segment}` captures, and optional per-segment `validate` rules.

An inline convention can also be declared directly on the policy (`configuration.scope` + `configuration.pattern`) without a configuration service — useful in deploy-only repositories.

Skipped gracefully when: no conventions are configured (neither `spec.paths` nor an inline convention), or the file being validated is not under the workspace path.

| `configuration` key | Type           | Description                                                                                     |
| ------------------- | -------------- | ----------------------------------------------------------------------------------------------- |
| `conventions`       | list of string | Restrict evaluation to these named `spec.paths` conventions                                     |
| `scope`             | string         | Inline mode: glob of files this convention applies to                                           |
| `pattern`           | string         | Inline mode: path pattern with `{segment}` captures                                             |
| `validate`          | mapping        | Inline mode: per-segment validation rules (each value is `{kind: yaml\|path, expression: ...}`) |

```yaml
# All spec.paths conventions, deny enforcement
- name: enforce-paths
  type: path_convention
  phase: validate
  enforcement: deny

# Deploy-repo: inline convention (no spec.paths required)
- name: deploy-layout
  type: path_convention
  phase: validate
  enforcement: deny
  configuration:
    scope: "deploy/**"
    pattern: "deploy/{landscape}/{ring}"
    validate:
      landscape:
        kind: path
        expression: "deploy/{landscape}/landscape.yaml"
```

### `layer_agreement`

Evaluates at the `validate` phase (ADR-0072). A `configuration.spec.paths` convention with `resolves: layers` lets `deployment.spec.layers` values be *derived* from the deployment file's own path instead of hand-typed. This policy is the separate, opt-in check for whether an **explicitly**-declared `spec.layers.segments` value is allowed to *disagree* with the value its own path would derive for that same segment — declaring the convention turns derivation on; declaring this policy is what makes disagreement an enforced violation instead of silently letting the explicit value win.

No `configuration` block — it reads the resolved convention and layer values directly from the loaded deployment and configuration.

Skipped gracefully when: no deployment is loaded, `spec.layers.segments` declares no explicit values, no configuration service is available, or no `resolves: layers` convention applies to this file. A segment that the deployment's path doesn't reach (e.g. a shallower deployment within the same convention family) has nothing to compare against and is not a violation.

```yaml
- name: layer-agreement
  type: layer_agreement
  phase: validate
  enforcement: warn
```

### `ai_review`

Evaluates at the `plan` phase. Sends the Terraform plan to a configured `ai_agent` integration for risk assessment and denies the deployment when the assessed risk meets or exceeds `risk_threshold`.

Skipped gracefully when: no `ai_agent` integration is configured (or the named one is not found), or the AI provider reports itself unavailable.

| `configuration` key | Type   | Default         | Description                                                                            |
| ------------------- | ------ | --------------- | -------------------------------------------------------------------------------------- |
| `integration`       | string | first available | Name of the `ai_agent` integration to use (`configuration.spec.integrations`)          |
| `risk_threshold`    | string | `critical`      | Deny when assessed risk is at or above this level: `low`\|`medium`\|`high`\|`critical` |
| `ai_prompt`         | string | none            | Optional `.strata/prompts` override for the review prompt                              |

```yaml
- name: ai_plan_gate
  type: ai_review
  phase: plan
  enforcement: deny
  configuration:
    integration: ai-advisor
    risk_threshold: high
```

### `change_reference_required`

Evaluates once per invocation, before any stage executes, rather than per-stage like
`plan`/`deploy` phase policies (ADR-0074 Phase 2). Fails when no change/ticket reference was
supplied via `--change-id` (with `--change-system` and `--reason`, or the corresponding
`STRATA_CHANGE_*` environment variables) — see
[deployment.md#change-reference](../config/deployment.md#change-reference).

**Two distinct phases, evaluated by two different commands — not one shared phase:**

| Phase            | Evaluated by            |
| ---------------- | ----------------------- |
| `deploy_before`  | `strata deploy run`     |
| `destroy_before` | `strata deploy destroy` |

Declare separate policies for each phase to set independent enforcement — e.g. `deny` on
`destroy_before` in production while only `warn` on `deploy_before`, since destroying
infrastructure is arguably the riskier action. A policy declared for one phase has no effect
on the other; there is no fallback or inheritance between them.

No `configuration` block — it is a plain existence check against whatever
`BaseDeployCommand._resolve_change_reference()` resolved for this invocation (shared by both
commands; Phase 1 capture already works identically for `run` and `destroy`).

`--dry-run` never reaches this policy for either command — nothing is changed, so nothing to
require a reference for. `--force` does **not** bypass it: `--force` only skips interactive
confirmation prompts and approval gates, which is a separate mechanism from policy evaluation.

Skipped gracefully when: no configuration service is loaded (mirrors every other policy type).

> A `deny` result here behaves like any other `deploy_before`/`destroy_before`/`plan`/`deploy`-phase
> policy denial: it aborts the pipeline with exit code `1` (system/execution error), not `3`.
> Only schema/cross-reference validation failures produce exit code `3` — see ADR-0004.

```yaml
- name: production-change-record-deploy
  type: change_reference_required
  phase: deploy_before
  enforcement: deny

- name: production-change-record-destroy
  type: change_reference_required
  phase: destroy_before
  enforcement: deny
```

---

## Enforcement Levels

| Level   | Behaviour on failure                                                                                          |
| ------- | ------------------------------------------------------------------------------------------------------------- |
| `deny`  | Stops the pipeline immediately. Exit code `3` (validation failure). No apply is run.                          |
| `warn`  | Logs a warning to the console and continues. The pipeline completes normally.                                 |
| `audit` | Records the result in the deployment manifest only. No console output unless `--verbose`. Pipeline continues. |

Use `deny` for hard constraints (compliance, security, data residency). Use `warn` for advisory checks during a rollout period. Use `audit` to collect baseline data before deciding whether to enforce.

---

## Phases

| Phase            | Triggered by              | When it runs                                                                  |
| ---------------- | ------------------------- | ----------------------------------------------------------------------------- |
| `validate`       | `strata validate -f …`    | After Pydantic structural validation and cross-reference checks pass          |
| `build`          | `strata build run …`      | After the platform artifact is generated                                      |
| `plan`           | `strata deploy run …`     | After `terraform plan`, before `terraform apply`                              |
| `deploy`         | `strata deploy run …`     | After `terraform apply` completes, before the manifest is persisted           |
| `deploy_before`  | `strata deploy run …`     | Once per invocation, before any stage executes (not evaluated on `--dry-run`) |
| `destroy_before` | `strata deploy destroy …` | Once per invocation, before any stage executes (not evaluated on `--dry-run`) |

The `plan` phase has the highest impact: it sits between planning and applying, giving policies access to the full Terraform plan JSON. A `deny` at this phase prevents any infrastructure change from being made.

---

## Example Configuration

### Single policy — zone enforcement

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: production
  annotations:
    description: Production environment configuration
spec:
  zones:
    - name: eu-west
      regions:
        - westeurope
        - northeurope
    - name: eu-central
      regions:
        - germanywestcentral
        - swedencentral

  policies:
    - name: zone_enforcement
      type: tenant_zone
      phase: plan
      enforcement: deny
      description: "All provisioned resources must be in customer-allowed zones"
```

With this configuration, running `strata deploy run` will evaluate every resource in the Terraform plan against the customer's declared zones. If any resource targets `us-east-1` or another region outside the allowed set, the deploy stops before apply with exit code `3`.

### Two policies — zone enforcement + required labels

```yaml
  policies:
    - name: zone_enforcement
      type: tenant_zone
      phase: plan
      enforcement: deny
      description: "Block resources outside customer zones"

    - name: require_standard_labels
      type: required_labels
      phase: build
      enforcement: warn
      description: "Warn when required labels are missing"
      configuration:
        targets: [namespaces]
        required_labels:
          - environment
          - cost_center
```

### Complete example — naming, refs, tags, SBOM, zone, and OPA

The following configuration shows all built-in policy types working together on a single deployment. Policies are evaluated in declaration order within each phase.

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: production
spec:
  zones:
    - name: eu-west
      regions:
        - westeurope
        - northeurope

  remotes:
    - name: my-service
      repository: https://github.com/acme/my-service
      reference: main
      conventions:
        release_pattern: "^v\\d+\\.\\d+\\.\\d+$"    # semver only
        quality_pattern: "^tested(-\\d+)?$"          # quality gate tags

  policies:
    # validate phase: checked during `strata validate --deep`
    - name: naming_convention
      type: naming_pattern
      phase: validate
      enforcement: warn
      description: "Names must be lowercase, start with a letter"
      configuration:
        pattern: "^[a-z][a-z0-9-]*$"

    # validate phase: config-free — patterns come from spec.remotes[].conventions above
    - name: release_tag_conventions
      type: ref_convention
      phase: validate
      enforcement: warn
      description: "Remote references must follow the naming convention declared on that remote"

    # build phase: checked after `strata build run` completes
    - name: require_standard_labels
      type: required_labels
      phase: build
      enforcement: deny
      description: "All namespaces must carry standard cost-tracking labels"
      configuration:
        targets: [namespaces]
        required_labels:
          - environment
          - project
          - owner

    - name: pin_all_images
      type: sbom_pinned_versions
      phase: build
      enforcement: deny
      description: "Reject floating image tags — all images must be pinned"
      configuration:
        collectors: [image, compose]
        allow_latest: false

    - name: trusted_registries
      type: sbom_allowed_registries
      phase: build
      enforcement: deny
      description: "Images must come from approved registries"
      configuration:
        allowed:
          - ghcr.io/myorg
          - mcr.microsoft.com
          - docker.io/library

    - name: block_vulnerable
      type: sbom_denied_packages
      phase: build
      enforcement: deny
      description: "Block packages on the security blocklist"
      configuration:
        denied:
          - "pkg:npm/event-stream@*"
        reason: "Supply-chain attack risk"

    - name: complexity_budget
      type: sbom_max_components
      phase: build
      enforcement: warn
      description: "Alert when component count exceeds budget"
      configuration:
        max_count: 200

    - name: license_compliance
      type: sbom_license
      phase: build
      enforcement: deny
      description: "Only permissive open-source licenses allowed"
      configuration:
        allowed: [MIT, Apache-2.0, "BSD-*", ISC, Unlicense]
        denied: ["GPL-*", "AGPL-*"]
        unknown_action: warn

    # plan phase: checked after `terraform plan`, before `terraform apply`
    - name: zone_enforcement
      type: tenant_zone
      phase: plan
      enforcement: deny
      description: "Resources must stay within customer-allowed regions"

    # plan phase: delegate to OPA for additional compliance checks
    - name: opa_compliance
      type: script
      phase: plan
      enforcement: deny
      description: "OPA baseline security policy"
      configuration:
        command: "opa eval --data policy.rego --input /dev/stdin -"
        timeout: 30
```

With this setup:
- `strata validate --deep` flags configuration names that don't follow the naming convention and warns if `my-service`'s pinned `reference` doesn't match its declared `release_pattern`/`quality_pattern`.
- `strata build run` stops if any namespace is missing required labels, finds a floating image tag, an unapproved registry, or a blocked package. A warning is emitted if the component budget is exceeded.
- `strata deploy run` runs zone enforcement and the OPA check in sequence against the Terraform plan. Both must pass before `terraform apply` is invoked.

---

## See Also

- [Lifecycles](lifecycles.md) — lifecycle hooks for running scripts at named pipeline points
- [Validators](validators.md) — structural validation of individual YAML files
- [Exit Codes](exit-codes.md) — `3` = validation failure (policy `deny` failure)
- [ADR 0006](../decisions/0006-policy-engine-for-deployment-guardrails.md) — design decisions behind the policy engine
