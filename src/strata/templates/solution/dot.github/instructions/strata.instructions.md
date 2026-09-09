---
description: Instructions for AI agents working with the strata CLI tool to manage infrastructure deployments
applyTo: '**'
---

# strata — Agent Operating Instructions

You are working with **strata**, a DevOps CLI tool for managing infrastructure-as-code deployments. This file tells you how to use the CLI effectively as an AI agent.

---

## Quick Start

```bash
# Always use JSON output for machine-readable responses
export STRATA_OUTPUT=json

# Point to workspace (or let auto-discovery find .strata/)
export STRATA_WORK_PATH=/path/to/workspace

# Validate before any deploy
strata validate <file> --output json

# Build artifacts
strata build run -f <deployment.yaml> --output json

# Deploy (with dry-run first)
strata deploy run -f <deployment.yaml> --dry-run --output json
```

---

## CLI Structure

Commands follow a flat `strata <group> <command>` pattern:

| Group | Key Commands | Purpose |
|-------|-------------|---------|
| `sln` | `init` `clean` `status` `export` | Solution lifecycle |
| `config` | `set` `unset` `list` | Manage workspace defaults (`.strata/cli.yaml`) |
| `validate` | — | Validate a YAML file against schema |
| `build` | `run` `plan` `clean` `sbom` | Build platform & Terraform artifacts; generate SBOM |
| `deploy` | `run` `destroy` `status` `history` `health` `lock` `drift` | Deploy infrastructure; manage state locks and drift |
| `repo` | `add` `remove` `list` `sync` `status` | Manage solution repositories |
| `profile` | `create` `remove` `list` `activate` `show` | Manage environment profiles |
| `ref` | `env` `config` `data` `secret` | Manage file references in profiles |
| `values` | `list` `get` | Inspect resolved deployment values |
| `guide` | `show` | Step-by-step workspace readiness checklist |
| `schema` | `list` `get` | Inspect YAML schemas |
| `tools` | `status` `check` | Verify external tool availability |
| `vars` | — | Variable resolution |
| `new` | `<kind>` | Scaffold a new YAML file from templates |
| `versions` | `add` `init` `lock` `export` `apply` `refresh` | Version-manifest/version-lock pinning (ADR-0011) |
| `workitem` | `list` `show` `approve` `reject` `cancel` `expire` `complete` | Approval-gate hand-off (ADR-0057) |
| `audit` | `list` `changes` `diff` `export` `resend` `status` | Deployment audit trail, journal status, SIEM forwarding |
| `promote` | `start` `rollback` `status` `matrix` `history` `log` | Version promotion across rings |
| `policy` | — | Inspect and evaluate deployment policies |
| `cost` | — | Cost estimation and visibility |
| `secret` | — | Generate and manage secret values |
| `manifest` | — | Query and export deployment manifests |
| `service` | — | Deploy/manage individual services (namespace/module) |
| `rollout` | — | Fleet-wide, multi-deployment rollouts |
| `cache` | — | Manage the resolved-model cache (ADR-0026) |
| `clean` | — | Clean solution-level artifacts |
| `serve` | — | Run/check the strata state-service server (ADR-0065) |
| `mcp` | — | Model Context Protocol server for AI tool integration |
| `version` | — | Show CLI version |
| `help` | — | Show help text |

---

## Standard Flags

Every command accepts these:

| Flag | Env Var | Default | Purpose |
|------|---------|---------|---------|
| `--work-path PATH` | `STRATA_WORK_PATH` | auto-detected | Workspace root |
| `--output FORMAT` | `STRATA_OUTPUT` | `console` | Output format: `console`, `text`, `json` |
| `--verbose` | `STRATA_VERBOSE` | off | Verbose output |
| `--quiet` | `STRATA_QUIET` | off | Suppress output |

**Priority:** explicit flag → env var → `.strata/cli.yaml` → built-in default.

---

## Exit Codes

| Code | Meaning | Agent Action |
|------|---------|--------------|
| `0` | Success | Proceed normally |
| `1` | System/execution failure | Read `messages` in JSON output for crash reason |
| `2` | Usage error (bad arguments) | Fix command syntax |
| `3` | Validation failure | Read `errors` array in JSON output for specifics |
| `4` | Deployment lock conflict | Another process holds the lock — check `strata deploy lock status -f <file>`; never force-remove a lock if a deploy may be running elsewhere |
| `5` | Hand-off required (approval gate) | A gate paused the deploy and created a `WorkItem` — use `strata workitem list`/`show <id>`, then `strata deploy run -f <file> --resume` once approved |

**Always check exit code first.** Exit 3 means the file was processed but is invalid — inspect the errors array.

---

## Output Format

Always pass `--output json` (or set `STRATA_OUTPUT=json`). JSON responses use a standard envelope:

```json
{
  "success": true,
  "data": { ... },
  "errors": [],
  "messages": []
}
```

- `success` — boolean, check this first
- `data` — command-specific payload
- `errors` — array of validation/execution errors (populated when exit code = 3)
- `messages` — informational messages, warnings, or failure context

---

## Work Path Resolution

The CLI auto-discovers the workspace by walking up from CWD looking for a `.strata/` directory. Override with:

1. `--work-path /explicit/path` (highest priority)
2. `STRATA_WORK_PATH=/env/path` (environment variable)
3. Automatic upward walk from CWD

**Recommendation:** Set `STRATA_WORK_PATH` in your environment to avoid ambiguity.

---

## Validation Workflow

```bash
# Phase 1 — structural validation (schema check)
strata validate path/to/file.yaml --output json

# Phase 2 — deep validation (cross-reference checks, requires active profile)
strata validate path/to/file.yaml --deep --output json
```

**Important:**
- `validate` processes one file at a time
- Use `--deep` only when a profile is active (otherwise exit 1)
- Always validate before build/deploy

---

## Build Workflow

```bash
# Full build (generates Terraform artifacts)
strata build run -f deploy/deploy-prd.yaml --output json

# Dry-run (validate + plan without writing)
strata build run -f deploy/deploy-prd.yaml --dry-run --output json

# Show what would change (diff existing vs new artifacts)
strata build plan -f deploy/deploy-prd.yaml --output json

# Limit to a single stage
strata build plan -f deploy/deploy-prd.yaml --stage staging --output json

# Artifacts diff only (skip terraform plan)
strata build plan -f deploy/deploy-prd.yaml --artifacts-only --output json

# Clean build artifacts
strata build clean -f deploy/deploy-prd.yaml --output json
```

---

## SBOM Workflow

```bash
# Generate CycloneDX 1.6 SBOM (writes sbom.json next to platform.json)
strata build sbom -f deploy/deploy-prd.yaml --output json

# Human-readable inventory to stdout (images, charts, modules, app deps)
strata build sbom -f deploy/deploy-prd.yaml --report inventory

# Write inventory to a file
strata build sbom -f deploy/deploy-prd.yaml --report inventory --output-file inventory.txt

# Skip lockfile scanning (faster for large repos)
strata build sbom -f deploy/deploy-prd.yaml --no-deps

# Scan any directory without a strata workspace (no deploy file required)
strata build sbom --scan /path/to/repo --report inventory
strata build sbom --scan /path/to/repo --output json
```

**Built-in language support** (lockfile scanning): Python, Node.js, Go, .NET/C#, Java, Ruby, Rust, PHP.

**Extend with custom parsers** — two options:
1. Drop a `LockfileParser` subclass as `.strata/lockfile_parsers/my_parser.py` — auto-registered, no config needed
2. Declare in `.strata/collectors.yaml` with `type: lockfile_parser`

`strata guide` will prompt for this step once a successful build exists (Phase 8).

---

## Version Pinning & Locks

`strata versions` manages version-manifest (`kind: version`) and version-lock (`kind: version-lock`) files that pin image/chart/remote versions per ring (ADR-0011):

```bash
# Scaffold a starter version-manifest for a ring
strata versions init --ring prd --output json

# Sync a manifest against module/workspace targets discovered in the workspace
strata versions refresh -f versions/prd.yaml --output json

# Compute and write spec.hash (tamper-evident) — always do this after editing pins
strata versions lock -f versions/prd.yaml --output json

# Print the resolved flat pin state
strata versions export -f versions/prd.yaml --output json

# Convert a version-manifest into a version-lock file
strata versions apply -f versions/prd.yaml --output json
```

**Always run `strata versions lock` after editing pins** — a version file with no (or a stale) `spec.hash` fails an integrity check when deployed.

---

## Deploy Workflow

```bash
# Always dry-run first
strata deploy run -f deploy/deploy-prd.yaml --dry-run --output json

# Execute deploy (--force skips confirmation prompts)
strata deploy run -f deploy/deploy-prd.yaml --force --output json

# Supply a change reference (required if a change_reference_required policy is active — ADR-0074)
strata deploy run -f deploy/deploy-prd.yaml --change-id JIRA-1234 --reason "Scheduled release" --force --output json

# Limit to a specific stage
strata deploy run -f deploy/deploy-prd.yaml --stage networking --force --output json

# Check current state
strata deploy status -f deploy/deploy-prd.yaml --output json

# View deployment history
strata deploy history -f deploy/deploy-prd.yaml --output json

# Health check
strata deploy health -f deploy/deploy-prd.yaml --output json

# Destroy (requires --force)
strata deploy destroy -f deploy/deploy-prd.yaml --force --output json
```

**Caution:** `deploy run` and `deploy destroy` are long-running operations. They may take minutes and produce no output until completion.

---

## Approval Gates & Work Items (ADR-0057)

A deployment with `spec.gates` in `enforce` mode pauses at the gate instead of failing outright — `deploy run` exits with code `5` ("hand-off required") and creates a `WorkItem` for a human to approve.

```bash
# See what's pending
strata workitem list --status pending --output json

# Inspect one work item
strata workitem show <item_id> --output json

# Approve, then resume the paused deploy
strata workitem approve <item_id> --note "Reviewed, looks good" --output json
strata deploy run -f deploy/deploy-prd.yaml --resume --force --output json

# Or reject it
strata workitem reject <item_id> --reason "Needs more testing" --output json
```

---

## Audit & Debugging

```bash
# Last execution only
strata audit list --last --output json

# Filter by level
strata audit list --level ERROR --output json

# Filter by execution ID
strata audit list --execution-id <id> --output json

# Last N minutes
strata audit list --minutes 10 --output json

# Check tool availability
strata tools status --output json
```

---

## File References & Cross-Repo Paths

YAML files use `@repo_name/relative/path.yaml` notation for cross-repository references:

```yaml
spec:
  source: "@haven/config/config.yaml"
```

The `@repo_name` prefix resolves via the solution's repository map. Repositories are managed with `strata repo add|remove|list`.

---

## YAML Document Structure

All platform YAML files follow Kubernetes-style structure:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: <kind>
meta:
  name: <name>
  annotations:
    description: "..."
  labels:
    version: "1.0.0"
spec:
  ...
```

Valid kinds: `deployment`, `workspace`, `configuration`, `diagram`, `environment`, `namespace`, `module`, `resource`, `provider`, `firewall`, `network`, `dns`, `tenant`.

---

## Provisioner Types

Workspace YAML files support two IaC provisioners under `spec.provisioners[]`:

| Type | Tool | When to use |
|-----------|------------------|-----------------------------------------------|
| `terraform` | Terraform CLI | Provision cloud infrastructure (VMs, networks) |
| `ansible` | ansible-playbook | Configure servers after they are provisioned |

### Terraform provisioner

```yaml
provisioners:
  - name: infra
    provisioner: terraform
    source:
      repository: haven
      source_path: terraform
    backend:
      type: terraform_cloud
      configuration:
        organization: myorg
        workspace: haven-prd
```

### Ansible provisioner

```yaml
provisioners:
  - name: configure
    provisioner: ansible
    source:
      repository: haven
      source_path: ansible
    configuration:
      playbook: site.yml               # default: site.yml
      inventory: inventory/hosts.yml   # auto-discovered if omitted
      ssh_private_key_secret: haven_ssh_key   # key name in resolved secrets (see below)
      extra_vars:
        env: production
```

### SSH key pattern — never put keys in YAML

Store the private key in the secret store and reference it by name. strata resolves it at deploy time, writes it to a `chmod 600` temp file for the duration of the subprocess call, then deletes it.

```yaml
spec:
  provisioners:
    - name: configure
      provisioner: ansible
      source:
        repository: haven
        source_path: ansible
      configuration:
        ssh_private_key_secret: haven_ssh_key   # name used to look up the key
  secrets:
    - key: haven_ssh_key
      source: bitwarden
      value: <bitwarden-item-id>   # item holds the full PEM content
```

The default secret name is `ssh_private_key`. Override it via `configuration.ssh_private_key_secret`.

---

## Workspace State

```
.strata/                  # State directory
├── solution.json           # Solution registry (repos, profiles)
├── cli.yaml                # Workspace defaults
├── logging.yaml            # Logging configuration
├── collectors.yaml         # (optional) SBOM collector/parser plugins via YAML
├── lockfile_parsers/       # (optional) drop .py files here — auto-registered
└── sbom-ignore.yaml        # (optional) ignore rules for dependency scanning
```

- `solution.json` — managed by the CLI, do not edit manually
- `cli.yaml` — user preferences, manage via `strata config set|unset|list`
- `collectors.yaml` — declare custom `BaseSbomCollector` or `LockfileParser` plugins; see `strata sln init` starter templates in `.strata/plugins/`
- `lockfile_parsers/` — zero-config drop folder: any `.py` file placed here is auto-imported and its `LockfileParser` subclasses registered; no YAML entry needed
- `sbom-ignore.yaml` — paths and filenames to exclude from `DependencyFileCollector`

---

## Environment Variables Injected During Execution

These are available in lifecycle scripts during build/deploy:

| Variable | Content |
|----------|---------|
| `STRATA_PHASE` | Current lifecycle phase (e.g., `deploy_provision_before`) |
| `STRATA_WORKSPACE_PATH` | Path to workspace root |
| `STRATA_CONFIG_PATH` | Path to configuration files |
| `STRATA_BUILD_PATH` | Path to build artifacts |
| `STRATA_OBJECT_PATH` | Path to objects directory |

---

## Agent Best Practices

1. **Always use `--output json`** — parse structured responses, never scrape console output.
2. **Check exit code first** — `3` means validation errors (read `errors`); `1` means system failure (read `messages`).
3. **Set `STRATA_WORK_PATH`** — eliminates ambiguity about which workspace you're targeting.
4. **Validate before deploy** — `strata validate` is safe and read-only. Run it first.
5. **Use `--dry-run`** — available on `build run`, `build clean`, `deploy run`, `deploy destroy`.
6. **Use `--force` for automation** — skips interactive confirmation prompts.
7. **Use `strata audit list --last --output json`** — inspect what the last command actually did.
8. **Use `strata tools status`** — verify required tools (terraform, ansible, git, docker) are available before operations.
9. **Long operations produce no streaming output** — `deploy run` and `build run` may take minutes. Set appropriate timeouts.
10. **Profile must be active for deep validation** — activate with `strata profile activate <name>` before `validate --deep`.
11. **Never put SSH private keys in YAML** — use `configuration.ssh_private_key_secret` to reference a key stored in the secret store. strata handles the temp file lifecycle.
12. **Exit code 4 means a lock conflict, not a real failure** — check `strata deploy lock status -f <file>` before retrying; never force-remove a lock if another deploy may genuinely be running.
13. **Exit code 5 means a gate is waiting on a human** — don't treat it as an error. Use `strata workitem list --status pending` to see what's blocking, then `--resume` after approval.
14. **Always `strata versions lock` after editing a version-manifest's pins** — an unlocked or stale-hash version file fails integrity checks at deploy time.

---

## Common Workflows

### Initial Setup
```bash
strata init --output json
strata repo add --name haven --path ../xyz-configuration --output json
strata profile add --name prd --output json
strata profile activate prd --output json
```

### Validate → Build → Deploy
```bash
strata validate deploy/deploy-prd.yaml --output json
strata build run -f deploy/deploy-prd.yaml --output json
strata deploy run -f deploy/deploy-prd.yaml --dry-run --output json
strata deploy run -f deploy/deploy-prd.yaml --force --output json
```

### Terraform + Ansible (provision then configure)
```bash
# Stage 1: provision infrastructure with Terraform
strata deploy run -f deploy/deploy-prd.yaml --stage infrastructure --force --output json

# Stage 2: configure servers with Ansible
# SSH key resolved from secret store; no key file on disk before/after
strata deploy run -f deploy/deploy-prd.yaml --stage configuration --force --output json
```

### Version Pinning Before a Release
```bash
strata versions refresh -f versions/prd.yaml --output json   # discover new/stale targets
strata versions lock -f versions/prd.yaml --output json      # write spec.hash
strata deploy run -f deploy/deploy-prd.yaml --dry-run --output json
```

### Approval Gate Hand-Off
```bash
strata deploy run -f deploy/deploy-prd.yaml --force --output json   # exits 5, creates a WorkItem
strata workitem list --status pending --output json
strata workitem approve <item_id> --output json
strata deploy run -f deploy/deploy-prd.yaml --resume --force --output json
```

### Generate SBOM / Platform Inventory
```bash
# CycloneDX SBOM for supply-chain / compliance tooling
strata build sbom -f deploy/deploy-prd.yaml --output json

# Human-readable overview for onboarding
strata build sbom -f deploy/deploy-prd.yaml --report inventory

# Run guide to see if SBOM step is next
strata guide -f deploy/deploy-prd.yaml
```

### Troubleshooting a Failed Deploy
```bash
strata audit list --last --level ERROR --output json
strata deploy status -f deploy/deploy-prd.yaml --output json
strata tools status --output json
```
