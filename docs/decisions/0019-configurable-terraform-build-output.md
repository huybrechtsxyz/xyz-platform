# Configurable Terraform build output

- Status: completed
- Date: 2026-07-03

## Context and Problem Statement

Strata's Terraform builder emits a fixed set of `.auto.tfvars.json` files using
strata's own naming conventions (`workspace.auto.tfvars.json`,
`providers.auto.tfvars.json`, `topologies.auto.tfvars.json`, etc.).

When users bring **existing Terraform codebases** into strata — codebases that
already declare their own variable contracts — there is no mechanism to emit
variables in the shape those Terraform modules expect. Feature flags existed only
as documentation (`tf_required_features.json`) and were never emitted as actual
boolean values. Domain-specific properties (`aks_config`, `dns_config`, etc.)
were silently dropped by the builder with no output path.

Users need influence over *what files* the builder produces and *what data shape*
each file takes.

## Decision Drivers

- **Incremental adoption** — users bring strata to existing Terraform repositories
  without rewriting their variable contracts.
- **Offline-safe builds** — the builder must never require live secret or store
  fetches. Build artefacts are committed and reproducible.
- **Security** — secrets must never appear in build artefacts or source control.
- **Transparency** — no hidden DSL or implicit transforms; scripts are plain files
  under version control.
- **Backward compatibility** — workspaces without an `output` block continue to
  work unchanged.

## Considered Options

**Option A — Fixed format; users fork the builder.**
No changes to strata. Users override by subclassing `TerraformBuilder` or
patching the built output after `strata build run`. Fragile; breaks on upgrades.

**Option B — Template-based emission.**
Users write Jinja2 templates that receive `platform.json` as context. Requires
learning a strata-specific DSL. Debugging template errors is harder than
debugging a script. Templates cannot call helper functions or import libraries.

**Option C — Build output profile on each provisioner.**
A declarative `output` block sits on the provisioner entry in `workspace.yaml`.
It selects a `format` and an optional `emits[]` category list, plus an optional
`files[]` list that supports source mode (properties pass-through) and script
mode (user-provided Python/shell script reads `platform.json` and writes files).
A format-level `format: script` gives full control to one top-level script.

## Decision Outcome

**Option C.** The `output` block is the right abstraction because:

- It lives in `workspace.yaml` alongside the provisioner it controls — the same
  file, the same PR, the same review.
- `format: script` provides a complete escape hatch with zero strata magic: the
  user's script reads `platform.json` (a stable JSON artifact) and writes
  whatever Terraform needs.
- `emits[]` + source mode cover the common cases (feature flags, property
  pass-through) without requiring a script at all.
- Per-provisioner scope handles workspaces with multiple Terraform sources, each
  with its own variable contract.
- `format: strata` (default) preserves today's behaviour — no migration required
  for existing workspaces.

## Design Summary

### `output.format` modes

| Mode     | Behaviour                                       |
| -------- | ----------------------------------------------- |
| `strata` | Default. Emit all built-in files.               |
| `custom` | Emit only what `emits[]` and `files[]` specify. |
| `script` | One user-provided script owns all output.       |
| `none`   | Source files copied; no tfvars output.          |

### Two-phase emission (features and variables)

Build-time and deploy-time are separate tiers:

| Tier        | Source                                   | When                                         |
| ----------- | ---------------------------------------- | -------------------------------------------- |
| Build-time  | `constant` / `environment` store entries | `strata build run`                           |
| Deploy-time | All stores via `ResolvedValues`          | `strata deploy run`, before `terraform init` |

The `TerraformDeployer` calls `_write_deploy_time_vars()` before `terraform init`,
overwriting the build-time files with fully-resolved values from
`ValueController`. For workspaces where all entries use `store: constant`, both
tiers produce identical output and the deploy overwrite is a no-op.

### Security

Secrets are explicitly excluded from all emit categories. There is no `"secrets"`
in `EmitCategory`. Secrets are always injected as `TF_VAR_*` environment
variables at deploy time via the existing `inject_tf_vars` context manager.
`should_emit("secrets")` always returns `False` regardless of profile
configuration.

### Environment-level output overrides

An environment can append extra `output.files[]` entries via
`environment.spec.overrides.output_files[]`. Additive only — environments cannot
remove or replace workspace-level file definitions. Strata warns when an
environment override file name collides with a workspace-level definition.

### Backend config `${var:...}` / `${secret:...}` resolution

`_build_backend_config()` in `TerraformDeployer` resolves `${var:KEY}` and
`${secret:KEY}` expressions using `self.resolved_values` before passing config to
`terraform init -backend-config`. Unresolved expressions are left as-is so
backends that accept literal `${...}` strings (e.g. HCL-native backends) are not
broken.

> **Update (2026-09-08):** [ADR-0075](0075-unify-terraform-helm-value-expression-syntax.md)
> reverses the "left as-is" behavior described above — an unresolved
> `${var:}`/`${secret:}`/`${feature:}` expression now hard-fails the deploy
> step instead of silently passing through as literal text. Re-examined
> because no real Terraform backend provider field is meant to receive that
> literal placeholder text; the original rationale (avoid breaking backends
> that "accept" literal `${...}` strings) does not correspond to any actual
> backend behavior. See ADR-0075 for the full reasoning.

## Consequences

### Positive

- Existing Terraform repositories can be wrapped by strata without touching their
  variable contract.
- Feature flags can be emitted as actual boolean values (`flags.auto.tfvars.json`)
  instead of documentation only.
- Domain-specific properties pass through unchanged via `source: properties`.
- Users have a clear, auditable escape hatch (`format: script`) for complex
  variable composition.

### Negative / trade-offs

- `_save_terraform_vars` now iterates per provisioner instead of per path, which
  is a behaviour change. The bug where identical files were written to all
  provisioner paths is fixed as a side-effect.
- Build scripts introduce an external runtime dependency (Python/shell) at build
  time. The `STRATA_DRY_RUN=true` environment variable allows scripts to skip
  writes during dry runs, but strata cannot enforce this.
- Integration-backed features and variables are written by the deployer, not the
  builder. This means a `strata build run` without a subsequent deploy will not
  produce fully-resolved `flags.auto.tfvars.json` when Flagsmith/App Config is
  used. This is by design: the builder is intentionally offline.

## References

- Implementation: `src/strata/models/workspace_model.py` (`OutputProfileModel`,
  `OutputFileModel`, `OutputFileSourceModel`)
- Implementation: `src/strata/models/environment_model.py`
  (`EnvironmentOverridesModel.output_files`)
- Implementation: `src/strata/builders/terraform_builder.py`
  (`_planned_files`, `_save_terraform_vars`, `_build_feature_flags_vars`,
  `_build_flat_variables`, `_resolve_merged_properties`,
  `_build_custom_output_files`)
- Implementation: `src/strata/deployers/terraform_deployer.py`
  (`_write_deploy_time_vars`, `_resolve_backend_expr`)
- Tests: `tests/strata/models/test_output_profile_model.py`
- Tests: `tests/strata/builders/test_builders_output_profile.py`
