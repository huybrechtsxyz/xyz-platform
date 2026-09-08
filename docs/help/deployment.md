# Deployment

Orchestrates workspace and environments into a deployable unit.

A deployment is a top-level strata YAML document (`kind: deployment`) that:
- **Defines the target infrastructure** via a `workspace` ref
- **Binds environments** for configuration (dev, staging, production)
- **Schedules deployment stages** and gate conditions
- **References policies, integrations, and audit rules** for governance

Think of a deployment as the "execute" file — it ties together the blueprint
(workspace) with the environment values and orchestration rules.

---

## Basic Structure

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: production
spec:
  workspace: @config/workspaces/my-workspace.yaml
  environments:
    primary: @config/environments/production.yaml
  stages:
    - name: infrastructure
      provisioner: terraform
      scope: all
    - name: verify
      type: manual_verification
  gates:
    - name: prod-approval
      type: approval
      when: always
      approvers:
        ops-team:
          type: github-team
          value: "org/ops-team"
```

---

## When to Use

- **Multi-stage orchestration** — separate infrastructure provisioning, configuration, and verification
- **Ring-based promotion** — deploy through dev → stg → prd progressively
- **Approval gates** — require sign-off before apply
- **Cost/security reviews** — enforce policy gates before deployment

---

## CLI Commands

```bash
strata build run -f deploy-prd.yaml              # build artifacts
strata build run -f deploy-prd.yaml --dry-run    # preview changes
strata deploy run -f deploy-prd.yaml --dry-run   # dry-run provisioning
strata deploy run -f deploy-prd.yaml --force     # apply infrastructure
strata deploy output -f deploy-prd.yaml          # check outputs (cached, or --refresh for live)
strata deploy status -f deploy-prd.yaml          # check live infra status

# Record an external change/ticket reference justifying the deploy (ADR-0074, optional)
strata deploy run -f deploy-prd.yaml --force \
  --change-id OPS-1234 --change-system jira --change-classification emergency \
  --reason "Restore checkout capacity after connection-pool exhaustion"
```

---

## See Also

- `workspace` — infrastructure blueprint
- `environments` — environment-specific values
- `gates` — approval and hand-off gates
- `configuration` — governance rules
