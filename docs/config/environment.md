# Environment Configuration

Environment-specific overrides and extensions for workspace deployments. **Workspaces define WHAT infrastructure to build**, **environments define HOW to customize it** for different deployment contexts (production, staging, development) without modifying workspace definitions.

## Purpose

- Consistent workspace replication across environments
- Environment-specific variable and secret values
- Feature flag management per environment
- Custom properties for environment metadata

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: <environment_name> # Required: ^[a-z][a-z0-9_]*$
  annotations:
    description: <description>
  labels:
    environment: <environment> # production, staging, development
    version: "<version>"
spec:
  properties: {} # Environment-specific settings
  custom: {} # Organizational metadata
  overrides: # Override workspace resources, modules, and providers
    resources: [] # Resource-level overrides
    modules: [] # Module-level overrides (images, chart versions, enabled state)
    providers: [] # Provider-level overrides
    remotes: [] # Remote reference overrides (pin version/tag/branch per environment)
    properties: {} # Override workspace properties
    includes: [] # Terraform file includes
  variables: [] # Override/extend workspace variables
  secrets: [] # Override/extend workspace secrets
  features: {} # Feature flags
  audit: # Audit event policy and sink routing
    policy:
      events: {} # Event type → enabled flag
    sinks: [] # Where to send audit events
    structure: by-execution # Deploy-log directory layout
    deploy_log_path: null # Custom deploy-log base path (default: .strata/deploy-log)
```

## Properties & Custom

**Properties** - Environment-specific configuration:

```yaml
properties:
  debug_mode: false
  log_level: "INFO"
  backup_enabled: true
```

**Custom** - Organizational metadata:

```yaml
custom:
  costcenter: "PROD-INFRA-001"
  owner: "Platform Team"
  compliance: "PCI-DSS"
  backup_retention_days: 30
```

## Overrides

Environment overrides let you customize workspace resources, modules, and providers
without modifying their source definitions. All override fields are optional — only
specify what you need to change.

### Module Overrides

Override module settings per environment: enable/disable modules, pin container
image versions, pin Helm chart versions, or merge additional configuration.

```yaml
spec:
  overrides:
    modules:
      - module: xyz_backend           # Module meta.name (required)
        services:                     # Override service container images
          - name: server
            image: registry.omp.com/product/backend:2025.3.0
          - name: worker
            image: registry.omp.com/product/backend:2025.3.0

      - module: xyz_frontend
        chart_version: "26.1.0"       # Pin Helm chart version
        services:
          - name: app
            image: registry.omp.com/product/frontend:2025.3.0

      - module: xyz_monitoring
        enabled: false                # Disable in this environment
```

**Scoping:** When a module appears in multiple resources or slots, use optional
qualifiers to narrow the override:

```yaml
overrides:
  modules:
    - module: xyz_backend             # Applies to ALL instances
      services:
        - name: server
          image: registry.omp.com/product/backend:2025.3.0

    - module: xyz_backend             # Narrow to canary slot only
      slot_type: canary
      services:
        - name: server
          image: registry.omp.com/product/backend:2025.3.0-rc.3

    - module: xyz_backend             # Narrow to specific resource
      resource: kubernetes_cluster
      services:
        - name: server
          image: registry.omp.com/product/backend:2025.3.0
```

| Field           | Required | Description                                                              |
| --------------- | -------- | ------------------------------------------------------------------------ |
| `module`        | Yes      | Module `meta.name` to override                                           |
| `resource`      | No       | Narrow to module within this resource                                    |
| `namespace`     | No       | Narrow to module within this namespace                                   |
| `slot_type`     | No       | Narrow to specific slot (`main`, `staging`, `canary`, `sidecar`, `init`) |
| `enabled`       | No       | Enable or disable the module                                             |
| `chart_version` | No       | Override Helm chart version                                              |
| `services`      | No       | List of service image overrides (`name` + `image`)                       |
| `configuration` | No       | Arbitrary configuration merged into module config                        |

**Constraints:**
- `resource` and `namespace` are mutually exclusive
- Service names within a module override must be unique
- When multiple overrides match, the most specific one wins (resource/namespace > module-only)

### Resource Overrides

Override resource-level settings such as instance count, enabled state, or configuration:

```yaml
spec:
  overrides:
    resources:
      - resource: manager
        count: 3
        enabled: true
        configuration:
          instance_type: "Standard_D4s_v3"
```

### Provider Overrides

Override the provider file binding or individual provider properties per environment.

**Swap the entire provider file** (`file`) — the provider's `spec.properties`, `spec.references`,
and all other settings come from the replacement file. This is the recommended approach for
targeting different regions or cloud accounts:

```yaml
# env-dev-eu.yaml
spec:
  overrides:
    providers:
      - provider: azure
        file: "@config/providers/azure-westeurope.yaml"   # replaces workspace binding for this env

# env-dev-us.yaml
spec:
  overrides:
    providers:
      - provider: azure
        file: "@config/providers/azure-eastus.yaml"
```

**Override individual properties** (`configuration`) — applied on top of whichever provider
file is loaded (after `file` resolution if both are set). Useful for minor per-environment
tweaks without maintaining separate provider files:

```yaml
spec:
  overrides:
    providers:
      - provider: azure
        configuration:
          region: westeurope    # overrides spec.properties.region in the loaded provider file
```

Configuration keys that do not match known `spec.properties` fields are logged and skipped.

| Field           | Required | Description                                                                  |
| --------------- | -------- | ---------------------------------------------------------------------------- |
| `provider`      | Yes      | Must match a provider `name` in the workspace                                |
| `file`          | No       | Replace the workspace's provider file binding for this environment           |
| `description`   | No       | Overrides the description on the workspace provider reference                |
| `configuration` | No       | Key-value overrides applied to `spec.properties` in the loaded provider file |

The provider YAML file defines which variable/secret keys are required; the actual credential
values are supplied via deployment `variables` and `secrets` — which can differ per deployment.

### Remote Reference Overrides

Each remote's **base reference** (default version, tag, or branch) is defined once in
`configuration.spec.remotes[].reference`. This is the version used for every environment
unless an override is present.

To use a different version in a specific environment, add an entry under
`spec.overrides.remotes`. The `remote` name must match an entry in `configuration.spec.remotes`.

```yaml
# configuration.yaml — defines the base reference used when no override is present
spec:
  remotes:
    - name: tf_landscape
      type: gitops
      repository: https://github.com/org/tf-landscape.git
      reference: main          # ← base/default for all environments
      source_path: terraform
      deploy_path: repos/tf_landscape
```

```yaml
# env-prd.yaml — pins production to a specific release tag
spec:
  overrides:
    remotes:
      - remote: tf_landscape   # must match configuration spec.remotes[].name
        reference: v1.2.3      # overrides the base reference for this environment only
```

**Resolution chain (highest to lowest priority):**
1. Environment `spec.overrides.remotes[name].reference` — per-environment pin
2. Configuration `spec.remotes[name].reference` — base/default for all environments

> **Design note:** The base reference is intentionally defined in one place (configuration)
> and overridden per environment. There is no module-level version pin — versioning is a
> deployment concern, not a module concern.

At build time the remote is fetched, the working tree is verified clean, and the ref
is checked out in detached-HEAD mode. The resolved commit SHA is logged.

**Validation:**
- Each `remote` name must exist in `configuration.spec.remotes` (Phase 2 cross-validation — build fails if not found)
- Duplicate remote names within one environment are rejected at parse time

### Build Output File Overrides (Terraform)

An environment can **append** extra output file definitions on top of the workspace
provisioner's `output.files[]`. This is additive only — environments cannot remove or
replace workspace-level file definitions.

```yaml
spec:
  overrides:
    output_files:
      - name: compliance.auto.tfvars.json
        variable: compliance_config
        type: object
        source: properties
        key: compliance_config

      - name: tenant_overrides.auto.tfvars.json
        type: script
        script: "@my_repo/scripts/build_tenant_overrides.py"
```

Use this when certain environments require extra Terraform variables not present in other
environments — for example, a production-only compliance config or a tenant-specific
override file.

## Variables

Environment-specific variables **override or extend** workspace variables:

```yaml
variables:
  - key: ENVIRONMENT # UPPER_SNAKE_CASE
    source: constant # constant, env, file, computed
    value: production
    type: string     # optional: string, number, bool, object, list, map
```

**Variable types** — declare the data type for Terraform inputs (v1.4.0+):

| Type     | Description                               | Example                            |
| -------- | ----------------------------------------- | ---------------------------------- |
| `string` | Text value (default)                      | `"vpc-id-123"`                     |
| `number` | Integer or float                          | `3`, `1.5`                         |
| `bool`   | Boolean value                             | `true`, `false`                    |
| `object` | Key-value map (native YAML mapping)       | `{ vpc_id: "x", subnet: "y" }`     |
| `list`   | Sequence of values (native YAML sequence) | `[ "az1", "az2", "az3" ]`          |
| `map`    | String-keyed mapping                      | `{ env: prod, region: us-east-1 }` |

When `type` is declared, values are emitted as native JSON types in `.auto.tfvars.json` (not as strings).
This enables Terraform to validate object shapes and list types at plan time. Omit `type` for backward compatibility (defaults to string).

**Merging:** Same key overrides workspace, new keys extend workspace.

## Secrets

Environment-specific secrets **override or extend** workspace secrets:

```yaml
secrets:
  - key: DATABASE_PASSWORD
    source: bitwarden # bitwarden, vault, env, file
    value: prod-db-password-id
```

**Merging:** Same key overrides workspace, new keys extend workspace.

## Features

Feature flags for environment-specific capabilities:

```yaml
features:
  monitoring_enabled: true
  debug_logging: false
  auto_scaling: true
  canary_deployments: false
```

## Audit

`spec.audit` configures which events are written to the deploy-log and where they are forwarded.
Settings are merged across all `environments[]` listed in the deployment (later entries override earlier ones).

### Event Policy

Control which event types are active:

```yaml
audit:
  policy:
    events:
      deploy_audit: true      # Every deploy execution (default: on)
      cli_action: true        # CLI command invocations (default: on)
      policy_violation: true  # Policy rule failures (default: on)
      secret_access: true     # Secret reads at build/deploy time (default: on)
      lock_event: false       # Deploy-lock acquire/release (default: off)
      validation_result: false # Validate command outcomes (default: off)
      drift_alert: false      # Infrastructure drift detections (default: off)
      build_event: false      # Build command outcomes (default: off)
```

### Sinks

Each sink routes audit events to a destination. Multiple sinks can be active simultaneously.

#### Built-in sink types

| Type      | Required fields | Optional fields            | Notes                            |
| --------- | --------------- | -------------------------- | -------------------------------- |
| `stdout`  | —               | —                          | Human-readable to terminal       |
| `ndjson`  | `path`          | —                          | Appends one JSON object per line |
| `syslog`  | `address`       | `format` (`json` or `cef`) | UDP/TCP to `host:port`           |
| `webhook` | `url`           | `headers`                  | HTTP POST, JSON body             |

```yaml
audit:
  sinks:
    - name: local_log
      type: ndjson
      path: .strata/audit.ndjson

    - name: ops_syslog
      type: syslog
      address: "10.0.0.5:514"
      format: cef          # Common Event Format for SIEM ingestion

    - name: alert_hook
      type: webhook
      url: "https://hooks.example.com/strata"
      headers:
        Authorization: "Bearer ${WEBHOOK_TOKEN}"
      events: [policy_violation]   # Only forward violations
```

#### Integration-backed sinks (SIEM)

Reference a named SIEM integration declared in `configuration.spec.integrations`:

```yaml
audit:
  sinks:
    - name: splunk
      integration: splunk_hec    # Must match configuration.spec.integrations[].name
    - name: elk_stack
      integration: elk_logstash
      events: [deploy_audit, policy_violation]
```

See [configuration.md](configuration.md) for how to declare Splunk, ELK, OpenTelemetry, and Azure Sentinel integrations.

### Deploy-Log Structure

Controls the directory layout under the deploy-log base path:

| Value          | Path template                                           |
| -------------- | ------------------------------------------------------- |
| `flat`         | `<base>/<deployment>/`                                  |
| `by-stage`     | `<base>/<deployment>/<stage>/`                          |
| `by-execution` | `<base>/<deployment>/<timestamp>/` _(default)_          |
| `by-tenant`    | `<base>/<tenant>/<deployment>/<timestamp>/`             |
| `full`         | `<base>/<tenant>/<workspace>/<deployment>/<timestamp>/` |

```yaml
audit:
  structure: by-execution          # default
  deploy_log_path: logs/deploy     # relative to workspace root (default: .strata/deploy-log)
```

### Forwarding via CLI

Re-forward deploy-log entries to a SIEM integration without re-deploying:

```bash
strata audit export --siem splunk_hec
strata audit export --siem splunk_hec --out export.json   # also write JSON file
```

### Sink field reference

| Field         | Type     | Default | Description                                            |
| ------------- | -------- | ------- | ------------------------------------------------------ |
| `name`        | string   | —       | Unique sink name                                       |
| `type`        | string   | —       | Built-in type: `stdout`, `ndjson`, `syslog`, `webhook` |
| `integration` | string   | —       | SIEM integration name (mutually exclusive with `type`) |
| `enabled`     | bool     | `true`  | Toggle sink without removing it                        |
| `events`      | string[] | all     | Event type filter (`null` = all active events)         |
| `path`        | string   | —       | Output file path (`ndjson` only)                       |
| `address`     | string   | —       | `host:port` target (`syslog` only)                     |
| `format`      | string   | `json`  | Payload format: `json` or `cef` (`syslog` only)        |
| `url`         | string   | —       | POST target URL (`webhook` only)                       |
| `headers`     | map      | —       | HTTP headers (`webhook` only)                          |

## Examples

**Production:**

```yaml
meta:
  name: production
  labels:
    environment: production
spec:
  properties:
    log_level: "WARN"
    backup_enabled: true
  custom:
    costcenter: "PROD-INFRA-001"
    owner: "Platform Team"
    sla: "99.9%"
  variables:
    - key: ENVIRONMENT
      source: constant
      value: production
    - key: REPLICA_COUNT
      source: constant
      value: 5
  secrets:
    - key: DATABASE_PASSWORD
      source: bitwarden
      value: prod-db-password-id
  features:
    auto_scaling: true
    monitoring: true
    debug_mode: false
```

**Staging:**

```yaml
meta:
  name: staging
  labels:
    environment: staging
spec:
  properties:
    log_level: "INFO"
  variables:
    - key: ENVIRONMENT
      source: constant
      value: staging
    - key: REPLICA_COUNT
      source: constant
      value: 3
  secrets:
    - key: DATABASE_PASSWORD
      source: bitwarden
      value: staging-db-password-id
  features:
    monitoring: true
    canary_deployments: true
```

**Development:**

```yaml
meta:
  name: development
  labels:
    environment: development
spec:
  properties:
    log_level: "DEBUG"
    backup_enabled: false
  variables:
    - key: ENVIRONMENT
      source: constant
      value: development
    - key: REPLICA_COUNT
      source: constant
      value: 1
  secrets:
    - key: DATABASE_PASSWORD
      source: constant
      value: dev_password_123
  features:
    debug_mode: true
    hot_reload: true
```

## Workspace & Environment Relationship

**Workspace (WHAT to build):**

```yaml
# workspace.yaml
spec:
  topology:
    - name: cluster
      components:
        - name: worker
          resource:
            count: ${REPLICA_COUNT} # Parameterized
  variables:
    - key: REPLICA_COUNT
      source: constant
      value: 1 # Default
```

**Environment (HOW to customize):**

```yaml
# production.yaml - Override REPLICA_COUNT to 5
spec:
  variables:
    - key: REPLICA_COUNT
      source: constant
      value: 5

# development.yaml - Keep REPLICA_COUNT at 1
spec:
  variables:
    - key: REPLICA_COUNT
      source: constant
      value: 1
```

## Multi-file Composition

A deployment can list multiple environment files under `spec.environments`. strata merges
them left-to-right before applying overrides to the workspace. Later files win on conflict.

```yaml
# deploy-prd.yaml
spec:
  environments:
    - environments/base.yaml        # shared baseline
    - environments/eu-region.yaml   # region-specific settings
    - environments/prd.yaml         # production overrides — highest precedence
```

### Per-section merge strategy

| Section                  | Strategy                                                           |
| ------------------------ | ------------------------------------------------------------------ |
| `variables`              | Last-wins by `key`                                                 |
| `secrets`                | Last-wins by `key`                                                 |
| `features`               | Last-wins by `key` (each flag merged independently)                |
| `properties`             | Shallow `dict.update()` — keys absent in later files are preserved |
| `custom`                 | Shallow `dict.update()`                                            |
| `lifecycle`              | Last-wins (wholesale)                                              |
| `audit`                  | Last-wins (wholesale)                                              |
| `overrides.resources`    | Last-wins by `resource` name                                       |
| `overrides.modules`      | Last-wins by `(module, resource, namespace, slot_type)`            |
| `overrides.providers`    | Last-wins by `provider` name                                       |
| `overrides.remotes`      | Last-wins by `remote` name                                         |
| `overrides.properties`   | Shallow `dict.update()`                                            |
| `overrides.includes`     | Append, deduplicated by `source` path                              |
| `overrides.output_files` | Append, deduplicated by output `path`                              |

Single-file deployments follow the same rules — they simply produce an unchanged result.

See [Environment Composition](../guides/environment-composition.md) for patterns and examples.

## Configuration Merge Order

1. Workspace defaults (base)
2. Environment overrides (override/extend)
3. Runtime parameters (CLI args)

**Merge strategy:** Variables/secrets with the same key override; new keys extend.
Properties and custom use shallow dict merge. See the [composition section above](#multi-file-composition)
for the full per-section table when multiple environment files are listed.

## Use Cases

**Multi-environment deployment:**

```text
config/
├── workspaces/platform.yaml        # Infrastructure (WHAT)
└── environments/
    ├── production.yaml             # Prod config (HOW)
    ├── staging.yaml                # Staging config (HOW)
    └── development.yaml            # Dev config (HOW)
```

**Regional deployments:**

```yaml
# us-east.yaml
spec:
  variables:
    - key: REGION
      source: constant
      value: us-east-1

# eu-west.yaml
spec:
  variables:
    - key: REGION
      source: constant
      value: eu-west-1
```

**Feature flags:**

```yaml
# production.yaml
spec:
  features:
    new_dashboard: false    # Not ready
    legacy_api: true        # Keep compatibility

# staging.yaml
spec:
  features:
    new_dashboard: true     # Test in staging
    beta_features: true     # Enable for testing
```

## CLI Integration

```bash
# Validate a deployment YAML file
strata validate repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Deploy using the active profile's environment refs
strata deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Dry-run (plan only — no changes applied)
strata deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run

# Tear down infrastructure
strata deploy destroy -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
```

## File Location

Environment files should be in:

```
config/environments/<environment_name>.yaml
```

Examples:

- `config/environments/production.yaml`
- `config/environments/staging.yaml`
- `config/environments/development.yaml`

## Best Practices

- **Naming:** Use deployment stage names (`production`, `staging`, `development`)
- **Consistent structure:** Same structure across environment files
- **Secret management:** Use appropriate secret managers per environment
- **Minimal overrides:** Only override what's necessary
- **Default values:** Set sensible defaults in workspace
- **Testing:** Test environment configs in lower environments first
- **Documentation:** Document overrides and purpose
- **Version control:** Track environment files (exclude sensitive data)

## Validation

Platform validates:

- Valid environment name (lowercase, alphanumeric, underscores)
- Unique variable keys within environment
- Unique secret keys within environment
- Valid variable/secret sources

## Troubleshooting

**Validation failed:** Check required fields, verify unique keys, ensure valid sources  
**Variable not overriding:** Verify key matches workspace variable exactly (case-sensitive), check environment file specified in CLI  
**Secret not found:** Ensure source configured correctly, verify secret ID/path exists, check secret manager authentication  
**Feature flag not working:** Verify feature name referenced correctly in code, check environment file loaded

## Environment vs Workspace

| Aspect          | Workspace                       | Environment                      |
| --------------- | ------------------------------- | -------------------------------- |
| **Purpose**     | Define infrastructure structure | Customize for deployment context |
| **Scope**       | What to build                   | How to configure                 |
| **Changes**     | Infrastructure changes          | Configuration changes            |
| **Reusability** | Single definition               | Multiple environments            |
| **Frequency**   | Changes infrequently            | Changes per deployment           |

## Summary

Environment configurations enable **consistent infrastructure replication** across deployment contexts by providing variable/secret overrides, feature flag management, and custom properties. This separation allows a single workspace to deploy to multiple environments with appropriate customizations.