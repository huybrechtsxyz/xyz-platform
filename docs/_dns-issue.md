# kind: dns — Ansible builder drops var:/secret: record values; add output_key: support for Terraform-output-sourced values (e.g. dynamic IPs)

**Labels (suggested):** `bug`, `enhancement`, `models`, `dns`

## Summary

Three related gaps in the `kind: dns` resource found while wiring up DNS management for a downstream project (Ansible + INWX):

1. **Bug**: `AnsibleBuilder._build_dns_vars()` silently drops the value of any DNS record sourced from `var:`/`secret:`, unlike `TerraformBuilder._build_dns_vars()` which resolves both.
2. **Enhancement**: `DnsRecordModel` has no way to source a record's value from a previous deployment stage's Terraform output (e.g. a VM's public IP) — even though `outputs.tf`-style generated code explicitly anticipates this ("`DNS A records point here`"), and the underlying plumbing (`ResolvedValues.stage_outputs`, `HealthCheckModel.output_key`) already exists elsewhere in strata for exactly this pattern.
3. **Future enhancement (related, not yet implemented)**: There is no generic way to *promote* a deployment stage's output into a durable `variable:`/`secret:`/`feature:` — today `ResolvedValues.stage_outputs` only lives in memory for the duration of a single `strata deploy run` invocation. See "3. Future enhancement" below.

## 1. Bug: Ansible DNS vars silently lose `var:`/`secret:` record values — **FIXED**

> Resolved in `src/strata/builders/ansible_builder.py`: `var:` records are now resolved via a
> new `variable_refs`/`_collect_environment_variables()` mechanism mirroring
> `TerraformBuilder`. `secret:` records are bucketed into a new `strata_dns_secrets.yml`
> (`strata_dns_secret_records`) file — mirroring Terraform's `dns_secret_records.auto.tfvars.json`
> — instead of being silently omitted. See `docs/config/dns.md` for the updated Ansible output
> contract.

**Where:** `src/strata/builders/ansible_builder.py::_build_dns_vars()`

```python
record_data: Dict[str, Any] = {
    "name": record.name,
    "type": record.type.value,
    "ttl": record.ttl,
    "priority": record.priority,
}
if record.value is not None:
    record_data["value"] = record.value
records.append(record_data)
```

If a `DnsRecordModel` uses `var:` or `secret:` instead of a literal `value:`, the emitted `strata_dns_zones` var (and the generated `dns.yml`) simply has **no `value` key at all** for that record — no error, no warning, no null placeholder for `secret:` the way Terraform gets.

**Compare with `TerraformBuilder._build_dns_vars()`**, which does the right thing:
- `value:` → passed through.
- `var:` → resolved immediately via `self.variable_refs.get(record.var, {})` (with a build-message warning if unresolved).
- `secret:` → bucketed separately into `dns_secret_records` (keyed by `{name}_{type}`, carrying `secret_key`) for later `TF_VAR_*` injection at apply time — never silently dropped.

**Impact:** Any consumer of the Ansible DNS vars (e.g. an `inwx.collection.dns`-based playbook) has no way to know a record was supposed to have a value — it just isn't there. We currently detect this defensively in our own playbook (`item.value is not defined`) and skip with a warning, but that's a workaround, not a fix.

**Suggested fix:** Mirror `TerraformBuilder`'s resolution for `var:` (trivial — `self.variable_refs` should already be available to `AnsibleBuilder` the same way). For `secret:`, since Ansible doesn't have an equivalent of Terraform's automatic `TF_VAR_*` injection today, the fix likely needs one of:
- a parallel "secret records" bucket (like `dns_secret_records`) emitted alongside `strata_dns_zones`, resolved by the Ansible deployer via `resolved_values.secrets` before invoking `ansible-playbook`, or
- documenting that `secret:`-sourced DNS records are Terraform-only until Ansible gets an equivalent secret-injection mechanism.

Either is fine — the current silent-drop behavior is the actual bug; at minimum it should be resolved-or-explicitly-unsupported, not silently missing.

## 2. Enhancement: `output_key:` source for DNS records (Terraform-output-driven values) — **IMPLEMENTED**

**Motivation:** The most common real-world DNS use case — pointing an `A`/`AAAA` record at a VM's public IP — doesn't fit any of the three existing `DnsRecordModel` sources:
- `value:` is static; breaks silently if the VM is ever recreated with a new IP.
- `var:`/`secret:` are also resolved at build time from `environment.yaml`-declared values, not from infrastructure that only exists after `terraform apply`.

**Existing precedent in the codebase** (found while investigating — this would not be a new concept, just extending an existing pattern to a new model):
- `HealthCheckModel.output_key` (`deployment_model.py`) — already lets a health check reference "a Terraform output key whose value provides the URL/host:port target," resolved after that stage's apply.
- `ResolvedValues.stage_outputs` (`utils/resolved_values.py`) — non-sensitive outputs from **all preceding deployment stages** are already collected and auto-injected as verbatim env vars + `TF_VAR_*` into every subsequent stage.
- `AnsibleDeployer` already consumes `resolved_values.stage_outputs` for one purpose (dynamic inventory IP via `ip_output_key`, default `server_ip`, when a stage has `topology:` set) — proving the plumbing works end-to-end for at least one case today.

**Key timing constraint that shaped the implementation:** `TerraformBuilder`/`AnsibleBuilder` run at **build time** (`strata build run`), which happens before any stage has applied — `ResolvedValues.stage_outputs` doesn't exist yet at that point. So `output_key:` records can never have their `value` resolved and baked into `dns.auto.tfvars.json` / `strata_dns.yml` the way `value:`/`var:` records can. This is exactly the same constraint `secret:` records already have (also never resolved into the tfvars file), which is why `output_key:` reuses the identical bucketing strategy instead of inventing a new one.

**Implemented design — mirrors the `secret:` bucketing pattern exactly:**
- `DnsRecordModel.output_key: Optional[str]` — 4th mutually-exclusive source alongside `value`/`var`/`secret` (`validate_exactly_one_source` updated). Unlike `var:`/`secret:`, `output_key:` is **not** subject to the `spec.references` declaration check — it names a cross-stage provisioner output, not an environment-declared value.
- `TerraformBuilder._build_dns_vars()`: `output_key:` records emit `"value": null` in `dns.auto.tfvars.json` and their coordinates (`{name}_{type}` → `output_key`) are bucketed into a new `dns_output_records` map, written to `dns_output_records.auto.tfvars.json` (parallel to `dns_secret_records.auto.tfvars.json`).
- `AnsibleBuilder._build_dns_vars()`: same bucketing into `strata_dns_output_records`, written to its own `strata_dns_outputs.yml` (parallel to `strata_dns_secrets.yml`).
- **No deployer/orchestrator changes were needed.** Every stage output already gets auto-injected into every subsequent stage's subprocess environment — `TF_VAR_<output_key>` for Terraform (`inject_tf_vars`) and a bare `<output_key>` env var for Ansible/Compose (`inject_compose_env`) — so the consuming Terraform DNS module / Ansible playbook reads the value directly (`var.hearth_public_ip` / `lookup('env', 'hearth_public_ip')`) using strata's generated `dns_output_records` file purely as a coordinate lookup (which record needs which output), exactly how `dns_secret_records` already works for secrets.

**Consequence:** this only works within a **single `strata deploy run` invocation** where the DNS stage runs after (and depends on) the stage that produced the output — `stage_outputs` is never persisted across separate CLI invocations. Closing that gap durably is exactly what point 3 below is for.

**Example YAML:**
```yaml
records:
  - name: "@"
    type: A
    output_key: hearth_public_ip   # resolved from the infrastructure_hearth stage's Terraform output
```

## 3. Future enhancement: promote a deployment stage's output to a durable `variable:`/`secret:`/`feature:`

**Not implemented yet.** Now tracked formally as [ADR-0068 — Cross-pipeline output
publishing](decisions/0068-cross-pipeline-output-publishing.md). Originally recorded as the
natural follow-up to point 2's same-invocation limitation; expanded into the real-world
scenario that actually forces the issue: **two independent pipelines** (e.g.
`bootstrap_customer` and `deploy_environment`) that need to share data across pipeline
boundaries, not just across stages in one `strata deploy run`. The analysis below is kept
for context; ADR-0068 is the source of truth for the decision and design going forward.

### The scenario

Two separate `kind: deployment` files, run by two separate pipelines, on their own schedules:

```
Pipeline A: bootstrap_customer.yaml   (runs once per tenant onboarding)
    provisions: tenant namespace, Key Vault secret scope, storage account, DNS zone delegation
    outputs: namespace_name, keyvault_uri, storage_account_name, db_admin_password (sensitive)

Pipeline B: deploy_environment.yaml   (runs on every app release, independently, later)
    needs: namespace_name, keyvault_uri, storage_account_name, db_admin_password
```

Unlike point 2 (a `dns` stage depending on an earlier stage *in the same deploy run*), pipeline B
may run hours, days, or weeks after pipeline A, from a different CI job, possibly a different
repo, with **no shared process memory and no shared filesystem**. `ResolvedValues.stage_outputs`
(in-memory, single-invocation) cannot help at all here — this is the actual gap.

### What strata already has for this (researched, not assumed)

| Mechanism                                                                                                             | Status                                              | Solves                                                                                                                                          | Cross-machine safe?                                                                                                                                               |
| --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ResolvedValues.stage_outputs` (in-memory)                                                                            | Implemented                                         | Cross-*stage*, same invocation only                                                                                                             | No — dies with the process                                                                                                                                        |
| `deployment-outputs.json` (ADR-0063 Gap 5)                                                                            | **Implemented**                                     | Durable, redacted-for-secrets, per-deployment output artifact                                                                                   | Only if both pipelines share the same disk/artifact store — today consumed manually via `jq`/a script, **not** wired into strata's own variable/secret resolution |
| `strata deploy status` (`terraform show -json` against the live backend)                                              | **Implemented**                                     | Reads a *different* deployment's outputs live, no shared disk needed                                                                            | Yes — this is literally the point of a remote Terraform backend, but Terraform-only and needs the caller to have backend read credentials                         |
| `spec.inputs.from` (`docs/guides/at-scale.md`)                                                                        | 🚧 Design draft, unimplemented                       | Build-time injection of an upstream deployment's outputs as this deployment's properties                                                        | Only if the upstream `platform.json`/outputs file is reachable on disk — doesn't solve genuinely remote pipelines by itself                                       |
| `spec.requires` (ADR-0058: cross-deployment dependency gating)                                                        | 🚧 Proposed, unimplemented                           | **Ordering only** — "has the upstream deployment succeeded yet?", via the gitops manifest's `spec.status` or `strata deploy status` as fallback | Yes (by design — that's the whole point of ADR-0058)                                                                                                              |
| `StoreIntegration.get_variable/set_variable`, `get_secret/set_secret` (Vault, Consul, Azure App Config, Bitwarden, …) | **Implemented**, write side unused for this purpose | Network-reachable, access-controlled, already-secret-aware KV store                                                                             | **Yes** — this is what these backends are for                                                                                                                     |

Two things are already implemented but not connected to each other: `deployment-outputs.json`
gives pipeline A a durable output record, and every store integration already has `set_variable`/
`set_secret`. Nothing today writes the former into the latter.

### Why store-based promotion (not file-based) is the right fix for *this* scenario

`spec.inputs.from` (design draft) and `deployment-outputs.json` are disk-artifact based — they
require pipeline B to have filesystem or shared-artifact access to pipeline A's build output,
which is exactly the coupling independent pipelines are trying to avoid (different CI runners,
possibly different repos/orgs). A senior-DevOps read of this: **the correct boundary between two
independent pipelines is a network-reachable, access-controlled store — not a shared disk.**
That's precisely what Vault/Consul/Azure App Config/Bitwarden already are, and strata already
speaks all of them on the *read* side for `var:`/`secret:`. Point 3 is only the missing *write*
side.

This also cleanly separates the two concerns ADR-0058 already identified as distinct:
- **"Is upstream done?"** → `spec.requires` (ADR-0058) — ordering/gating, not data.
- **"What values does upstream produce?"** → point 3 (this section) — data, not ordering.
A real pipeline pair needs both: pipeline B should refuse to run at all if `bootstrap_customer`
hasn't succeeded (ADR-0058), *and* separately needs the actual `keyvault_uri` value (point 3).

### Proposed design

Add a declarative output-promotion block to `DeploymentStageModel` (or per-provisioner):

```yaml
# bootstrap_customer.yaml
stages:
  - name: infrastructure
    provisioner: terraform_azure
    promote_outputs:
      - key: namespace_name           # this stage's Terraform output name
        save_as: {variable: acme_namespace_name, store: azure_appconfig}
      - key: keyvault_uri
        save_as: {variable: acme_keyvault_uri, store: azure_appconfig}
      - key: db_admin_password
        save_as: {secret: acme_db_admin_password, store: azure_keyvault}   # sensitive → secret store, never appconfig
```

```yaml
# deploy_environment.yaml — a completely separate pipeline, run independently
spec:
  variables:
    - key: keyvault_uri
      store: azure_appconfig
      value: acme_keyvault_uri        # ordinary var: read — no strata code change needed here
  secrets:
    - key: db_admin_password
      store: azure_keyvault
      value: acme_db_admin_password
```

**Key design points, from a DevOps-hardening angle:**
- **Tenant/deployment-scoped key naming is mandatory, not optional.** With ~100 tenants (see
  `docs/guides/at-scale.md`), a flat key namespace collides immediately. Keys should be
  namespaced by convention (e.g. `{tenant}/{key}` or a required `prefix:` on `promote_outputs`),
  and this needs validation, not just documentation, or two tenants' bootstrap pipelines will
  silently clobber each other's values.
- **Sensitive vs non-sensitive must route to different backend classes.** Terraform's `sensitive`
  output flag (already used by `collect_outputs()`/`stage_outputs_sensitive`) should force
  `save_as.secret` (a real secret store) and forbid `save_as.variable` (a config store) — mirrors
  the split `deployment-outputs.json` already enforces (`sensitive_keys` never carries values).
- **Fail loud, not silent, on write failure.** Mirrors the existing rule that
  `resolved.store_unavailable_errors` is always fatal regardless of `strict` — a failed promotion
  write should fail the deploy, not warn and continue, since a downstream pipeline silently
  reading a stale/missing value is worse than an upfront failure.
- **Idempotency / staleness:** re-running `bootstrap_customer` should overwrite, not duplicate,
  the promoted key. Downstream consumption already has a staleness lever via the ADR-0026
  `resolved_values` cache (`--refresh-cache`) — worth confirming promoted keys aren't served from
  a stale cache entry on the consuming side.
- **Ordering is explicitly out of scope here** — pair with ADR-0058's `spec.requires` so pipeline
  B never even attempts to resolve a key that pipeline A hasn't written yet. Without that pairing,
  a missing key still fails safely today (`ValueController` treats "key not found" as a hard
  error, never a silent None), but the error would be confusing without the gating context.
- **Audit/provenance:** `deployment-outputs.json` already answers "what did pipeline A produce and
  when" for humans/registries. Point 3's promoted store values are the machine-consumable mirror
  of the same data — the two should stay consistent (same source: `collect_outputs()`), not
  become two divergent sources of truth.

Once implemented, point 2's `output_key:` becomes an optional same-invocation shortcut rather than
the only way to consume a stage output — cross-pipeline consumers just declare an ordinary
`var:`/`secret:`, identical to any other environment-declared value, with zero new resolution code
required anywhere else in strata (DNS, provider config, resource config, …).

## Environment
- strata version: 1.6.1
- Found via: `E:\SourcesXYZ\strata\src\strata\{models,builders,utils}\...` (source inspection), while wiring an Ansible + `inwx.collection` DNS-apply playbook for a downstream project.
