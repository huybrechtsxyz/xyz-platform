# Configuration

Platform-wide **validation schemas and defaults** for providers, resources, and topologies.

## Purpose

Define **validation rules** for providers/resources/topologies, establish **platform defaults**, support **multiple layered configs** with merge order (built-in → custom 1...N, later overrides).

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: <name> # ^[a-z][a-z0-9_]*$
spec:
  configuration: {} # Platform defaults
  properties: {} # Custom properties
  providers: [] # Provider definitions (regions, resources)
  topologies: [] # Topology schemas (components, rules)
```

## Providers

Define allowed regions and resource validation patterns:

```yaml
providers:
  - name: <provider>
    additional_regions: false # Restrict to listed regions
    regions: [eu-fr, us-ny]
    additional_resources: false # Restrict to defined resources
    resources:
      - name: virtualmachine
        category: compute
        configuration: # Regex validation
          cpu_cores: "^[1-9][0-9]?$" # 1-99
          ram_mb: "^(512|1024|2048|4096)$"
```

## Topologies

Define cluster component rules:

```yaml
topologies:
  - type: docker-swarm
    components:
      - role: manager
        is_control: true
        min_count: 1
        max_count: 7
      - role: worker
        min_count: 1
        max_count: 0 # unlimited
```

## Layering — Artifact Path Hierarchies

Define how deployment artifacts are organized into a hierarchical path structure. Use layering when different deployment files need to be placed into different directories during the build process.

> **Changed in ADR-0072 (breaking).** `spec.layering` and `spec.layerings` were removed and merged into `spec.paths` — the same mechanism that validates repository layout. One convention now declares a hierarchy family once (segment names, order, per-segment constraints), and a deployment's values are resolved from its own `spec.layers` **and**, as a fallback, from the deployment file's own location. See [Migration from `layering`/`layerings`](#migration-from-layeringlayerings) below.

### Declaring a hierarchy — `spec.paths` with `resolves: layers`

A path convention that declares `resolves: layers` does double duty: it validates repository layout (as any `spec.paths` entry does) *and* drives artifact-path construction.

```yaml
spec:
  paths:
    - name: zone-tenant
      scope: "zones/**"
      pattern: "zones/{zone}/customers/{customer}/{environment}"
      resolves: layers
      segments:
        - name: zone
          pattern: "^[a-z][a-z0-9-]*$"
        - name: customer
          pattern: "^[a-z][a-z0-9]{4}$"
        - name: environment
          pattern: "^[a-z0-9]{1,4}$"
          default: dev

    - name: landscape-ring
      scope: "landscape/**"
      pattern: "landscape/{landscape}/{ring}"
      resolves: layers
      segments:
        - name: landscape
        - name: ring
```

Each convention has:

- **`name`** — identifier, referenced by `deployment.spec.layers.follows`
- **`scope`** — `fnmatch` glob pre-filter on the file's path relative to the workspace root
- **`pattern`** — segment-aware path template; each `{segment}` captures exactly one path part
- **`resolves: layers`** — opts this convention into layer resolution
- **`segments`** — ordered segment definitions (this is the artifact-path order)

**One convention per hierarchy family, not per depth.** Use the family's *deepest* legitimate shape as the pattern; shallower deployments in the same family are handled on the deploy side. Give each family a **distinguishing literal prefix segment** (`zones/`, `landscape/`) so two families can never both match the same file — an ambiguous match is a hard validation error, not a silent pick.

**Literal-only patterns are valid.** If a stack encodes nothing in its paths and declares every value explicitly, use a pattern with no `{captures}` at all (e.g. `scope: "deploy/**"`, `pattern: "deploy"`). Nothing is derived, and explicit values are the only source.

### Segment Definition

Each `segments` entry has:

| Field         | Type  | Default | Description                                                                  |
| ------------- | ----- | ------- | ---------------------------------------------------------------------------- |
| `name`        | `str` | —       | Segment name (e.g., `zone`, `customer`, `ring`, `landscape`)                 |
| `description` | `str` | `null`  | Human-readable description                                                   |
| `pattern`     | `str` | `null`  | Regex validating any value that *does* get supplied, however it was supplied |
| `default`     | `str` | `null`  | Value used when neither explicitly declared nor derivable from the path      |

> There is no `required` field. A segment that resolves to nothing is **not applicable** to that deployment — not an error. Different deployments in the same family legitimately have different real depths (a shared-infra deployment genuinely has no customer or environment).

**Important:** Segment names are arbitrary and must be unique within a convention. The segment named `environment` has no special meaning.

### Declaring values — `deployment.spec.layers`

```yaml
# Values derived entirely from this file's own path
spec:
  layers:
    follows: zone-tenant

# Explicit values (shared infra — deeper segments simply don't apply)
spec:
  layers:
    follows: zone-tenant
    segments:
      zone: europe
```

- **`follows`** — names the convention to use. If omitted, the convention is auto-detected by matching this file's path against every `resolves: layers` convention's `scope` + `pattern`.
- **`segments`** — explicit per-segment values. Not all-or-nothing; any name omitted here falls back to path-derivation, then `default`.

### Resolution Order

**Level 1 — which convention applies:**

1. **Explicit** — `spec.layers.follows` names a convention → use it. Error if the name doesn't exist or doesn't declare `resolves: layers`.
2. **Auto-detected** — no `follows` → match the file's path against every `resolves: layers` convention. More than one match is a hard validation error naming both conventions.
3. **None** — no convention applies → `spec.layers.segments` is unvalidated free-form data.

**Level 2 — each segment's value:**

1. **Explicit** — `spec.layers.segments.<name>` is set → always wins.
2. **Derived** — the file's path reaches `<name>`'s position in the pattern → use the captured value.
3. **Default** — the segment declares `default` → use it.
4. **Not applicable** — none of the above → omitted; not an error.

### Artifact Path Resolution

The artifact path is the join of however many segments actually resolved, in the convention's declared `segments` order, **stopping at the first segment that didn't resolve**. It reflects actual depth, not the family's maximal shape.

```
Deployment files:
├── zones/europe/customers/contoso/prd/deploy.yaml  → zone-tenant → europe/contoso/prd
├── zones/europe/shared.yaml (segments: {zone: europe}) → zone-tenant → europe
├── landscape/platform/ring2/deploy.yaml            → landscape-ring → platform/ring2
└── shared/base.yaml                                → no match; layering not applied
```

Both `zones/` deployments follow the *same* convention — the shared-infra one simply resolves one segment instead of three.

### `validate:` rules check the resolved value

A convention's `validate:` rules (membership against `spec.<field>[*].<attr>`, or a file-existence template) are applied to the **resolved** value for each segment — the Level 1 + Level 2 outcome — not just the raw path capture. Explicitly-declared values and shallower deployments are checked exactly like path-derived ones.

### Migration from `layering`/`layerings`

`spec.layering` and `spec.layerings` were **removed** with no backward-compatible fallback. Because every strata model uses `extra: forbid`, an unmigrated configuration fails validation immediately with an "unknown field" error naming the removed field — a loud failure, never a silent behavior change.

**Before:**

```yaml
spec:
  layerings:
    - name: default
      scope: "zones/**"
      layers:
        - name: zone
          required: true
        - name: customer
          required: true
        - name: environment
          default: dev
```

**After:**

```yaml
spec:
  paths:
    - name: default
      scope: "zones/**"
      pattern: "zones/{zone}/customers/{customer}/{environment}"
      resolves: layers
      segments:
        - name: zone
        - name: customer
        - name: environment
          default: dev
```

Changes to make:

1. Move the entry from `spec.layerings` (or `spec.layering`) to `spec.paths`.
2. Rename `layers:` to `segments:` and add `resolves: layers`.
3. Add a `pattern:` describing the on-disk layout. If nothing is encoded in the path, use a **literal-only** pattern (no `{captures}`) so nothing is derived.
4. Drop every `required:` field — it no longer exists ("not applicable" replaces it).
5. On each deployment, nest the old flat `spec.layers` dict under `segments:`, and optionally add `follows:`.

**Deployment side, before and after:**

```yaml
# Before
spec:
  layers:
    zone: europe
    customer: contoso

# After
spec:
  layers:
    follows: default
    segments:
      zone: europe
      customer: contoso
```

> **Note on `scope` wildcards.** `scope` is matched with `fnmatch`, where `*` already crosses `/`. `"zones/**"` and `"zones/*"` compile to the identical regex — `**` is not a stronger "any depth" wildcard the way it is in gitignore or `pathlib.rglob`. Depth precision comes from `pattern` (which is genuinely segment-aware), not from `scope`.

## Example

```yaml
meta:
  name: cloud_validation
spec:
  providers:
    - name: kamatera
      additional_regions: false
      regions: [eu-fr, us-ny]
      resources:
        - name: virtualmachine
          category: compute
          configuration:
            cpu_cores: "^[1-9][0-9]?$" # 1-99
            ram_mb: "^(512|1024|2048|4096)$"
  topologies:
    - type: docker-swarm
      components:
        - role: manager
          is_control: true
          min_count: 1
          max_count: 7
        - role: worker
          min_count: 1
  paths:
    # Multi-tenant deployments with region isolation
    - name: regional-tenant
      scope: "zones/**"
      pattern: "zones/{zone}/customers/{customer}/{environment}"
      resolves: layers
      segments:
        - name: zone
        - name: customer
        - name: environment
          default: dev

    # Ring-based deployments (canary → production)
    - name: ring-promotion
      scope: "landscape/**"
      pattern: "landscape/{landscape}/{ring}"
      resolves: layers
      segments:
        - name: landscape
        - name: ring
```

## Configuration Schema Fields

The `configuration` dict on a resource (and `properties` at the spec level) maps field names to validation rules. Each entry is either a **shorthand regex string** or a **structured `ConfigurationSchemaField`**:

```yaml
resources:
  - name: virtualmachine
    configuration:
      # Shorthand: just the pattern string (required=true, no description)
      cpu_cores: "^[1-9][0-9]?$"

      # Structured: pattern + optional flags
      enable_backup:
        pattern: "^(true|false)$"
        required: false
        description: "Whether automated backups are enabled"
```

| Field         | Type   | Default | Description                                          |
| ------------- | ------ | ------- | ---------------------------------------------------- |
| `pattern`     | `str`  | —       | Regex the field value must fully match               |
| `required`    | `bool` | `true`  | Whether the field must be present in resource config |
| `description` | `str`  | `null`  | Human-readable description of the field              |

> **Boolean fields must use a pattern — there is no native `type: boolean` in the schema.**
> The configuration schema is regex-only; all values are validated as strings.
> Use `"^(true|false)$"` as the pattern and pass `"true"` or `"false"` as the value.
>
> ```yaml
> # Schema (in configuration YAML)
> enable_backup:
>   pattern: "^(true|false)$"
>   required: false
>
> # Usage (in deployment/resource YAML)
> configuration:
>   enable_backup: "true"   # string, not a YAML boolean
> ```

---

## Merge Behavior

Multiple configs merge: built-in → 00-_.yaml → 10-_.yaml → 99-\*.yaml  
**Properties:** Last wins (override)  
**Providers/Topologies:** Additive (extend list)

## Integrations — AI Agent

Add an `ai_agent` integration to enable advisory LLM analysis at build/deploy lifecycle points.
Requires no extra dependencies for Ollama; `openai`/`anthropic` SDKs are optional.

```yaml
spec:
  integrations:
    # Azure OpenAI via az login (recommended — no stored key)
    - name: ai-advisor
      type: ai_agent
      endpoints:
        address: https://my-aoai.openai.azure.com/
      authentication:
        method: cli
      properties:
        provider: azure_cli
        model: gpt-4o
        temperature: 0.1
        max_tokens: 4096
        timeout: 60
        enabled_hooks: [deploy_plan_after]

    # Local Ollama (air-gapped / zero-cost)
    - name: ai-local
      type: ai_agent
      properties:
        provider: ollama
        model: llama3
```

**CLI:** `strata build plan -f deploy.yaml --ai` — runs analysis after terraform plan.

**Policy gating** (`type: ai_review`) blocks or warns when the AI rates a plan above a configurable risk threshold.

See `strata help --topic ai_agent` and [ADR-0025](../decisions/0025-ai-agent-integration-for-build-and-deploy.md) for full reference.

## Validation

- Valid regex patterns in resource configuration
- min_count ≤ max_count for topology components
- Unique provider/topology names after merge
- Defined regions/resources when additional\_\* = false

## Path Convention Policy

Declare directory structure conventions in `spec.paths`. A `path_convention` policy type
enforces these at validation time.

### Declaring conventions

```yaml
spec:
  paths:
    - name: zone-deployment-tree
      scope: "zones/**"                              # files in this subtree are candidates
      pattern: "zones/{zone}/customers/{tenant}/{env}" # {segment} captures one path part
      validate:
        zone:
          kind: yaml
          expression: spec.zones[*].name             # captured value must be a declared zone
        tenant:
          kind: path
          expression: "customers/{tenant}/tenant.yaml" # file at this path must exist
        # {env} is captured but not validated — validate: entries are optional per segment

    - name: tenant-registry
      scope: "customers/**"
      pattern: "customers/{tenant}"
      validate:
        tenant:
          kind: path
          expression: "customers/{tenant}/tenant.yaml"

    - name: landscape-registry
      scope: "landscape/**"
      pattern: "landscape/{landscape}"
      validate:
        landscape:
          kind: path
          expression: "landscape/{landscape}/landscape.yaml"
```

### Custom tenant file location

A convention can declare `resolves: tenant` to override the default `tenants/{code}.yaml` location. The pattern must include a `{code}` segment (tenant code substitutes into it). At most one convention per configuration may declare `resolves: tenant`. For example:

```yaml
spec:
  paths:
    - name: tenant-location
      resolves: tenant                                # This convention drives tenant resolution
      pattern: "customers/{code}/customer.yaml"      # {code} holds the tenant code
```

When declared, both validation and build phases use this pattern to locate tenant files. If no convention declares `resolves: tenant`, the default `tenants/{code}.yaml` is used (fully backward-compatible).

### Enforcement policy

```yaml
spec:
  policies:
    # Enforce all conventions
    - name: enforce-paths
      type: path_convention
      phase: validate
      enforcement: deny

    # Enforce specific conventions only
    - name: advisory-landscape
      type: path_convention
      phase: validate
      enforcement: warn
      configuration:
        conventions: [landscape-registry]
```

### Inline convention (deploy-repo mode)

For repositories without a configuration model, declare the convention inline on the policy:

```yaml
policies:
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

### Validation rule types

| Rule shape                                                   | Meaning                                                                              |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| `{kind: yaml, expression: spec.zones[*].name}`               | Captured value must appear in a JMESPath query against the loaded ConfigurationModel |
| `{kind: path, expression: "customers/{tenant}/tenant.yaml"}` | File at this path (relative to workspace root) must exist on disk                    |

`spec.*` rules require `--deep` validation (configuration service must be available). File
existence rules work in both surface and deep mode.

Files that match the scope but not the pattern (e.g., shallower depth) are skipped — not a
violation. Files that match no scope are not checked by that convention.

## Checkov IaC Security Policy

Run [Checkov](https://www.checkov.io) against Terraform build artifacts during the `build` phase.
Requires Checkov to be installed (`pip install checkov`). Gracefully skipped when not available.

### Declaring the integration

```yaml
spec:
  integrations:
    - name: checkov
      type: checkov
      capabilities: [iac_security]
      required: false
      validation:
        command: checkov --version
        min_version: "2.0.0"
```

### Enabling the policy

```yaml
spec:
  policies:
    - name: terraform_security_baseline
      type: checkov
      phase: build
      enforcement: deny
      description: "Block builds with HIGH or CRITICAL Checkov findings"
      configuration:
        framework: terraform          # default: terraform
        severity_gate: high           # critical|high|medium|low (default: high)
        skip_checks:                  # CKV IDs to suppress (false positives, accepted risks)
          - CKV_AWS_1
          - CKV_AWS_20
        include_checks: []            # if set, run ONLY these checks (empty = run all)
        custom_checks_dir: ".strata/checkov/custom/"  # optional: custom rule directory
        timeout: 120                  # subprocess timeout in seconds (default: 120)
```

### How it works

1. After Terraform artifacts are generated by `strata build run`, the policy engine finds the
   `terraform/` subdirectory under `build_path` (falling back to `build_path` itself if no
   subdirectory exists).
2. Invokes: `checkov --directory <terraform_dir> --framework terraform --output json --compact`
3. Parses Checkov JSON output into `CheckovFinding` records with severity, resource, and file path.
4. Applies `severity_gate` — findings at or above the gate level become policy violations.

### Severity gate

| `severity_gate`  | Fails on                    |
| ---------------- | --------------------------- |
| `critical`       | CRITICAL only               |
| `high` (default) | HIGH, CRITICAL              |
| `medium`         | MEDIUM, HIGH, CRITICAL      |
| `low`            | LOW, MEDIUM, HIGH, CRITICAL |

### Graceful degradation

- Checkov not installed → policy skips (passes), warning logged
- No `.tf` files found in build path → policy skips
- Checkov subprocess fails → policy skips (non-fatal, never blocks build)

## OPA Policy

Evaluate [Open Policy Agent](https://www.openpolicyagent.org) Rego rules against the
deployment context. Supports two modes: HTTP REST to a running OPA server (fast), or
`opa eval` CLI as a stateless fallback (no server required).

strata does **not** manage the OPA server lifecycle — that is the operator's responsibility.

### Installation

```bash
brew install opa          # macOS
# or: https://www.openpolicyagent.org/docs/latest/#1-download-opa
```

### Declaring the policy

```yaml
spec:
  policies:
    - name: zone_enforcement
      type: opa
      phase: build
      enforcement: deny
      configuration:
        rule: "data.strata.zones.deny"      # OPA rule path to evaluate
        policy_dir: ".strata/policies/"     # .rego files directory (CLI mode)
        endpoint: "http://localhost:8181"   # OPA server URL (HTTP mode, optional)
        timeout: 30
```

### Writing OPA rules

Rules must return a **set of violation strings** named `deny`:

```rego
package strata.zones

deny contains msg if {
    resource := input.platform.spec.resources[_]
    not resource.properties.region in input.configuration.spec.allowed_regions
    msg := sprintf("Resource '%s' in disallowed region '%s'",
                   [resource.meta.name, resource.properties.region])
}
```

### OPA input document

strata sends a JSON document containing available context:

```
{
  "phase": "build",
  "platform": { ... },       // platform artifact (if available)
  "configuration": { ... },  // configuration model (if available)
  "deployment": { ... },     // deployment model (if available)
  "plan_data": { ... },      // terraform plan JSON (if available)
  "work_path": "/workspace",
  "build_path": "/workspace/.strata/build"
}
```

### Mode selection

| `endpoint` / `OPA_ENDPOINT`     | Behavior                              |
| ------------------------------- | ------------------------------------- |
| Set and server reachable        | HTTP mode: `POST /v1/data/{rule}`     |
| Set but server unreachable      | Falls back to CLI mode                |
| Not set                         | CLI mode: `opa eval --stdin-input`    |
| Not set and `opa` not installed | Policy skips (passes), warning logged |

### Graceful degradation

- OPA not installed and no server configured → policy skips (passes), warning logged
- `policy_dir` not found → policy skips
- Server unreachable → falls back to CLI mode
- Rule returns empty set or false → pass (no violations)

## Secret Stores

Secrets in `spec.secrets` are resolved at build time by the `strata build` command. The `store` field controls which backend is used. The following stores are supported:

| `store` value    | Resolver type | Integration required? | Notes                                      |
| ---------------- | ------------- | --------------------- | ------------------------------------------ |
| `constant`       | Built-in      | No                    | Literal value — avoid for real secrets     |
| `environment`    | Built-in      | No                    | Reads a named env var from the local shell |
| `github`         | Built-in      | No                    | Reads a GitHub Actions injected env var    |
| `azure-keyvault` | Integration   | Yes                   | Azure Key Vault secret                     |
| `bitwarden`      | Integration   | Yes                   | Bitwarden Secrets Manager item             |
| `vault`          | Integration   | Yes                   | HashiCorp Vault / OpenBao secret           |
| `infisical`      | Integration   | Yes                   | Infisical secret                           |

### `github` — GitHub Actions secrets

GitHub Actions secrets are injected into the runner's environment as plain environment variables before each job step executes. The `github` store type reads from those environment variables.

```yaml
spec:
  secrets:
    - key: db_password
      store: github
      value: DB_PASSWORD          # GitHub secret name (env var injected by Actions)
      description: "Database password from GitHub Secrets"
```

**How it works:** The `value` field is the environment variable name. GitHub Actions maps your repository secret `DB_PASSWORD` to the env var `DB_PASSWORD` when you reference it in the workflow's `env:` block. The resolver calls `os.environ.get("DB_PASSWORD")` at build time.

**Uppercase normalization:** GitHub uppercases all secret names at storage time. The resolver automatically uppercases `value` before the lookup — so `value: db_password` and `value: DB_PASSWORD` are equivalent.

**Local development:** Running `strata build` locally with `store: github` secrets emits a warning because `GITHUB_ACTIONS` is not set. Set the env vars manually for local testing:

```powershell
$env:DB_PASSWORD = "local-test-value"
```

**`version` field:** Not supported for `store: github`. GitHub Secrets are not versioned. Specifying `version` raises a validation error.

**Production policy:** If your configuration defines `security.allowed_secret_stores`, add `"github"` explicitly:

```yaml
spec:
  security:
    allowed_secret_stores:
      - github
      - azure-keyvault
```

---

## Deployment Artifacts

The `spec.deployment` section controls where deployment artifacts are persisted.

### Manifest Storage

Controls where the deployment manifest (full audit record) is written after every deploy run.
When absent, manifests are not written.

```yaml
spec:
  deployment:
    manifest:
      type: local                     # local | gitops
      path: ".strata/deployments"     # base path; appended: /{deployment}/{version}/{timestamp}.json
```

For GitOps storage:

```yaml
spec:
  deployment:
    manifest:
      type: gitops
      repository: state-repo          # must match a name in spec.remotes
      branch: manifests
      path: deployments
      tag: true                       # create git tag {deployment}/{version} after write
```

| Field        | Type   | Default               | Description                                   |
| ------------ | ------ | --------------------- | --------------------------------------------- |
| `type`       | `enum` | —                     | `local` or `gitops`                           |
| `path`       | `str`  | `.strata/deployments` | Base path for manifest files                  |
| `repository` | `str`  | `null`                | Repository name (required when `type=gitops`) |
| `branch`     | `str`  | `null`                | Target branch (required when `type=gitops`)   |
| `tag`        | `bool` | `true`                | Create a git tag after writing (gitops only)  |

### Terraform Output Artifact Storage

Controls whether Terraform output values are written to a durable artifact file after a successful
deploy. When absent, outputs are available to downstream stages within the same run but not persisted.

```yaml
spec:
  deployment:
    outputs:
      enabled: true                   # set to false to disable
      path: ".strata/outputs"         # base path; appended: /{deployment}/{version}/{stage}.json
      sensitive: redact               # redact | omit
```

Written to: `{work_path}/{path}/{deployment_name}/{version}/{stage_name}.json`

Artifact structure:

```json
{
  "deployment": "prod_deploy",
  "version": "2.0.0",
  "stage": "network",
  "written_at": "2026-06-15T10:00:00Z",
  "outputs": {
    "server_ip": "10.0.0.5",
    "db_password": "(sensitive)"
  }
}
```

| Field       | Type   | Default           | Description                                                                         |
| ----------- | ------ | ----------------- | ----------------------------------------------------------------------------------- |
| `enabled`   | `bool` | `true`            | Write the artifact. Set `false` to disable without removing config                  |
| `path`      | `str`  | `.strata/outputs` | Base path for output artifacts                                                      |
| `sensitive` | `enum` | `redact`          | `redact` — keep key, replace value with `"(sensitive)"`. `omit` — drop key entirely |

> Sensitive output handling applies to any Terraform output declared `sensitive = true`.
> Non-sensitive outputs are always stored as-is.
> Write failures are non-fatal and logged as warnings.

---

## Change Tracking

Declares the workspace's default external change/ticket tracker (Jira, Azure DevOps, ServiceNow,
GitHub, or an internal system), so `strata deploy run`/`deploy destroy` operators only need to
supply the volatile part — the change id and their justification — on the command line
(ADR-0074, Phase 1: capture and record only, not enforcement).

```yaml
spec:
  change_tracking:
    system: jira                                        # default for --change-system
    url_template: "https://jira.example.com/browse/{id}" # {id} substituted; no other placeholders
    id_pattern: "^[A-Z][A-Z0-9]+-[0-9]+$"                # standard regex the id must match
    classifications: [emergency, normal, standard]       # allowed values for --change-classification
```

| Field             | Type        | Default | Description                                                                                                                                                                                         |
| ----------------- | ----------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `system`          | `str`       | `null`  | Default tracker used when `--change-system` is omitted. Any string — not a fixed enum                                                                                                               |
| `url_template`    | `str`       | `null`  | Template for resolving a change record's URL from its id (`{id}` only)                                                                                                                              |
| `id_pattern`      | `str`       | `null`  | Standard regular expression the supplied `--change-id` must match                                                                                                                                   |
| `classifications` | `List[str]` | `null`  | Allowed values for `--change-classification`. An open list, not a fixed strata enum — each organization defines its own scheme (e.g. ITIL `emergency`/`normal`/`standard`). Unset accepts any value |

All fields are optional and everything here is optional to declare — nothing is required by
default. When an operator supplies `--change-id`, they must also supply `--reason`; `--change-system`
must be supplied or resolved from `system` above. `--change-classification` is always optional, even
when `classifications` is configured — it's only checked against the allowlist when both are present.

```bash
strata deploy run -f deploy/deploy-prd.yaml \
  --change-id OPS-1234 \
  --change-classification emergency \
  --reason "Restore checkout capacity after connection-pool exhaustion"
```

`--change-system`, `--change-title`, `--change-url`, and `--change-classification` are also available
(with `STRATA_CHANGE_*` environment-variable equivalents) to override or supply what configuration
doesn't. The resolved reference is recorded on the deployment manifest and deploy log — see
[deployment.md](deployment.md#change-reference).

---

## Integrations

`spec.integrations` declares external service integrations used by the platform — primarily
SIEM sinks for audit event forwarding. Each integration is referenced by name from
`spec.audit.sinks[].integration` in environment YAML.

```yaml
spec:
  integrations:
    - name: splunk_hec           # Referenced by audit sinks
      type: splunk
      enabled: true
      endpoints:
        address: "https://splunk.internal:8088"
      authentication:
        method: api_key
        api_key:
          api_key: "${SPLUNK_HEC_TOKEN}"    # Resolved from env at deploy time
      properties:
        index: strata
        source: strata-deploy
        sourcetype: _json
        channel: "guid-for-indexer-ack"    # optional HEC channel
```

### Supported SIEM types

#### `splunk` — Splunk HTTP Event Collector (HEC)

Forwards events via the HEC endpoint (`POST /services/collector`).

| Property     | Default  | Description                                        |
| ------------ | -------- | -------------------------------------------------- |
| `index`      | `main`   | Splunk index                                       |
| `source`     | `strata` | Event source label                                 |
| `sourcetype` | `_json`  | Sourcetype (use `_json` for structured data)       |
| `channel`    | —        | HEC channel GUID (enables indexer acknowledgement) |

```yaml
- name: splunk_hec
  type: splunk
  endpoints:
    address: "https://splunk.corp.example:8088"
  authentication:
    method: api_key
    api_key:
      api_key: "${SPLUNK_HEC_TOKEN}"
  properties:
    index: platform
    sourcetype: _json
```

#### `elk` — ELK / Logstash

Forwards events via TCP (Logstash JSON codec) or HTTP (Elasticsearch Bulk API).

| Property        | Default        | Description                                |
| --------------- | -------------- | ------------------------------------------ |
| `protocol`      | `tcp`          | `tcp` (Logstash) or `http` (Elasticsearch) |
| `index_pattern` | `strata-audit` | Elasticsearch index prefix                 |

```yaml
# TCP (Logstash JSON input)
- name: elk_logstash
  type: elk
  endpoints:
    address: "logstash.internal:5044"
  properties:
    protocol: tcp

# HTTP (Elasticsearch Bulk API)
- name: elk_es
  type: elk
  endpoints:
    address: "https://es.internal:9200"
  authentication:
    method: basic
    basic:
      username: strata
      password: "${ES_PASSWORD}"
  properties:
    protocol: http
    index_pattern: strata-prod
```

#### `otel` — OpenTelemetry (OTLP/HTTP)

Forwards events as OTLP Log Records to any OpenTelemetry-compatible backend
(Grafana, Datadog, Splunk OTel Collector, etc.).

| Property              | Default | Description                        |
| --------------------- | ------- | ---------------------------------- |
| `protocol`            | `http`  | `http` (OTLP/HTTP JSON)            |
| `resource_attributes` | `{}`    | Extra OTel resource attributes map |

```yaml
- name: otel_collector
  type: otel
  endpoints:
    address: "https://otel.internal:4318"
  properties:
    resource_attributes:
      service.name: strata
      deployment.environment: production
```

#### `sentinel` — Azure Sentinel (DCR Logs Ingestion API)

Forwards events via the Azure Monitor Logs Ingestion API (DCR-based).
Uses `DefaultAzureCredential` — managed identity, service principal, or Azure CLI.

| Property                  | Required | Description                                       |
| ------------------------- | -------- | ------------------------------------------------- |
| `data_collection_rule_id` | Yes      | Immutable DCR ID (e.g. `dcr-abc123`)              |
| `stream_name`             | Yes      | Custom stream name (e.g. `Custom-DeployAudit_CL`) |

```yaml
- name: azure_sentinel
  type: sentinel
  endpoints:
    address: "https://my-dce.westeurope-1.ingest.monitor.azure.com"
  properties:
    data_collection_rule_id: dcr-0abc1234567890def
    stream_name: Custom-StrataDeployAudit_CL
```

### Integration field reference

| Field               | Type   | Required | Description                                                 |
| ------------------- | ------ | -------- | ----------------------------------------------------------- |
| `name`              | string | Yes      | Unique name referenced by `audit.sinks[].integration`       |
| `type`              | enum   | Yes      | `splunk`, `elk`, `otel`, `sentinel`                         |
| `enabled`           | bool   | No       | Defaults to `true`. Set `false` to disable without removing |
| `endpoints.address` | string | Yes      | Target URL or `host:port`                                   |
| `authentication`    | object | No       | `method`: `api_key`, `basic`, `bearer`                      |
| `properties`        | map    | No       | Type-specific configuration (see per-type tables)           |

---

## Notes

- Built-in default in `src/STRATA_platform/data/configuration.yaml` always loads first
- Use numeric prefixes (00-, 10-, 20-) to control merge order
- Set `additional_regions: false` to restrict regions
- Regex patterns validate resource configurations
- See workspace.md, environment.md, deployment.md for usage