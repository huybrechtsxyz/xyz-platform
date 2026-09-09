---
agent: agent
description: "Scaffold a new strata Deployment YAML file"
---

Create a new `kind: deployment` YAML file.

Ask the user for:
1. **Deployment name** — the identifier for this deployment (e.g. `deploy-prd`, `deploy-stg`)
2. **Workspace file** — path to the workspace YAML (e.g. `@my-repo/stack/ws-platform.yaml`)
3. **Environment file(s)** — path(s) to the environment YAML(s) providing variables/secrets/features for this deployment (e.g. `@my-repo/envs/env-prd.yaml`)
4. **Namespaces** — list of namespace names and their file paths to include
5. **Stages** — ordered deployment stages with provisioner name (e.g. `platform_iac`, `platform_compose`, `platform_helm`) and any dependencies

Then generate the file at `deploy/<name>.yaml` with:
- `apiVersion: strata.huybrechts.xyz/v1` and `kind: deployment`
- `spec.workspace` with `name` and `file`
- `spec.environments` as a list of environment file references
- Each namespace as a `spec.namespaces` entry with `name` and `file`
- Each stage as a `spec.stages` entry with `name`, `provisioner`, and optional `depends_on`
- Do NOT use a `type` field on stages — it does not exist and will fail validation

After writing the file, validate it:
```bash
strata validate deploy/<name>.yaml
```

Then show the user what a dry-run build would produce:
```bash
strata build run -f deploy/<name>.yaml --dry-run
```

If either step fails, show the errors and fix them before finishing.
