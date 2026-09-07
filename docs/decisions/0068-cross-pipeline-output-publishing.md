# Cross-pipeline output publishing — writing stage outputs into variable/secret stores

- Status: proposed
- Date: 2026-08-11
- Related: [ADR-0005](0005-secret-resolution-at-build-time.md) (secret resolution model — `var:`/`secret:` read side this ADR writes into), [ADR-0026](0026-resolved-model-cache.md) (resolved-value cache — staleness lever on the consuming side), [ADR-0058](0058-cross-deployment-dependency-gating.md) (cross-deployment dependency gating — the ordering half of this same problem), [ADR-0063 Gap 4](0063-gap4-output-passing.md) (`inputs_from` — same-deployment, same-invocation output passing this ADR does *not* replace), [ADR-0063 Gap 5](0063-gap5-output-capture.md) (`deployment-outputs.json` — the durable artifact this ADR publishes data out of), [ADR-0065](0065-strata-state-service.md) (state service — a candidate secondary channel, see Option C), [ADR-0067](0067-server-identity-authentication-authorization.md) (server identity/auth — gates the state-service channel)

## Remaining Work

- Not started — nothing in this ADR has been implemented yet.

## Context and Problem Statement

Strata already has a well-built mechanism for passing a Terraform (or other provisioner)
output from one stage to the next: `ResolvedValues.stage_outputs`, populated by
`TerraformDeployer.collect_outputs()` after `apply` and auto-injected into every
subsequent stage's subprocess environment (`TF_VAR_<key>` for Terraform, a bare `<key>`
env var for Ansible/Compose — see `inject_tf_vars`/`inject_compose_env`). This works well
for the case it was built for: stages within **one deployment file, in one
`strata deploy run` invocation**.

It breaks down completely for a scenario that shows up as soon as an organization splits
infrastructure lifecycle across **independent pipelines** rather than stages of one
pipeline — for example:

```
Pipeline A — bootstrap_customer.yaml   (runs once per tenant onboarding)
    provisions: tenant namespace, Key Vault secret scope, storage account
    outputs: namespace_name, keyvault_uri, storage_account_name, db_admin_password (sensitive)

Pipeline B — deploy_environment.yaml   (runs on every app release, independently, later)
    needs: namespace_name, keyvault_uri, storage_account_name, db_admin_password
```

Pipeline B may run hours, days, or weeks after pipeline A, from a different CI job,
possibly a different repository, with **no shared process memory and no shared
filesystem**. `ResolvedValues.stage_outputs` cannot help — it dies with the process that
created it. This is also exactly the three-layer bootstrap pattern already sketched (as a
design draft) in `docs/guides/at-scale.md` — global bootstrap, zone bootstrap, tenant
bootstrap, each a separate deployment file, each potentially run by a separate pipeline —
so this is not a hypothetical, it is the natural consequence of strata's own recommended
at-scale architecture.

This ADR was triggered by a narrower, concrete case (a `kind: dns` record needing a VM's
public IP produced by an earlier stage — see the DNS `output_key:` work) whose own design
notes flagged that the "same invocation" constraint would eventually need a real answer.
This ADR is that answer, generalized past DNS to any producer/consumer pair.

## Decision Drivers

- **The correct boundary between two independent pipelines is a network-reachable,
  access-controlled store — not a shared disk.** Different CI runners, possibly
  different repos/orgs, cannot be assumed to share a filesystem or artifact cache.
- **Don't invent a new storage abstraction.** Strata already has one:
  `VariableStoreType`/`SecretStoreType` + `StoreIntegration` (Vault, Consul, Azure App
  Config, Bitwarden, etc.), already read by `var:`/`secret:` everywhere in the platform.
  The read side is solved; only the write side (publishing a stage output into one of
  these stores) is missing.
- **Sensitive and non-sensitive outputs must route to different backend classes.**
  Terraform's `sensitive` output flag already splits `stage_outputs` from
  `stage_outputs_sensitive` (ADR-0063 Gap 5) and `deployment-outputs.json` already never
  writes a sensitive value to disk (`sensitive_keys` lists keys, omits values). Any
  publishing mechanism must preserve that split rather than re-introduce a path where a
  generated password ends up in a config store.
- **Ordering is a separate concern from data.** ADR-0058 already establishes "did the
  upstream deployment succeed?" as its own mechanism (`spec.requires`). This ADR must not
  try to also solve ordering — it only concerns itself with "what values did upstream
  produce," assuming ADR-0058 (or an equivalent operator-side gate) already answered "is
  upstream done."
- **Fail loud, not silent.** `ValueController` already treats a missing/unreachable store
  as fatal (`resolved.store_unavailable_errors` overrides `strict` in both directions). A
  failed publish write must be held to the same bar — a downstream pipeline silently
  reading a stale or missing value is worse than an upfront failure.
- **Multi-tenant collisions are the default failure mode, not an edge case.**
  `docs/guides/at-scale.md` describes ~100 tenants sharing a landscape; a flat key
  namespace for published outputs collides on the first day two tenants bootstrap in
  parallel.

## Considered Options

### Option A — Status quo: manual extraction from `deployment-outputs.json`

Pipeline B's CI job downloads pipeline A's `deployment-outputs.json` (ADR-0063 Gap 5) as
a build artifact and `jq`-extracts the value it needs, passing it into pipeline B as a
CI variable.

- Pro: nothing to build; this already works today, and is explicitly documented as the
  supported consumption pattern for that artifact.
- Con: requires CI-specific artifact plumbing between two pipelines (works differently in
  every CI system), is invisible to strata (no validation that the reference is even
  correct until the downstream job fails), and never covers sensitive outputs at all
  (`deployment-outputs.json` deliberately never writes secret values to disk).

**Rejected as the only answer** — it is a real, working escape hatch and remains valid for
ad-hoc/low-frequency cases, but does not scale past a handful of pipeline pairs and has no
answer for secrets.

### Option B — Generalize the drafted `spec.inputs.from` (`docs/guides/at-scale.md`) to be genuinely remote

Extend the design-draft, unimplemented `spec.inputs.from` mechanism so that instead of
reading an upstream deployment's build artifact from local/shared disk, it fetches it over
a network call.

- Con: this reinvents a network-fetch protocol and an auth model from scratch, for a
  narrower need (read one JSON file) than a real KV store already solves.
- Con: `spec.inputs.from` was explicitly scoped (ADR-0058's Option 4 discussion) to
  build-time property injection for one deployment reading another's outputs — conflating
  it with general-purpose cross-pipeline value distribution overloads a still-unimplemented
  field with a second responsibility, the same objection ADR-0058 already raised and
  rejected for reusing that field as a gating mechanism.

**Rejected**, kept explicitly out of scope — if/when `spec.inputs.from` is built for its
original narrower purpose, it may reuse whatever "read a value" helper this ADR
introduces, but the YAML surfaces stay distinct.

### Option C — Route published outputs through the strata state service (ADR-0065)

The state service (ADR-0065) is a small, first-party HTTP+SQL event store, already
reachable from any pipeline via the existing `webhook` audit sink, already used to durably
forward `cost.recorded`/`drift.recorded`/`manifest.recorded`-style events. A new event type
(e.g. `deployment.outputs_published`) could carry a stage's non-sensitive outputs to the
same store, and a downstream pipeline could query them back out.

Genuinely useful as an **extra, complementary channel** — it is already durable, already
central, and the emit side (`AuditController.forward()`, sinks, redaction) is entirely
built. But it is not a good primary mechanism for this ADR's purpose today, for three
reasons:

- **No read API yet.** ADR-0065's own "Remaining Work" marks Phase 3 (a query/read API)
  as deferred. Today, "reading a value back" means a downstream pipeline running a raw
  SQL query directly against the state service's database (explicitly the supported
  pattern per ADR-0065 — "operators query the database directly with any SQL tooling") —
  workable, but heavier than a `var:`/`secret:` declaration, and it hands out direct DB
  access to every consuming pipeline rather than a scoped read.
- **It is an append-only event log, not a current-value store.** Consuming a published
  output means "find the most recent `deployment.outputs_published` event for this
  deployment/key," a different (and slower) access pattern than a KV `get`. Fine for
  audit/history; not the natural fit for "give me the current keyvault URI."
- **It never carries secrets.** Same redaction rule as `deployment-outputs.json` — a real
  secret store is still required for the sensitive half of the problem regardless of
  whether this channel exists.

**Adopted as a secondary, optional channel** (see Decision Outcome) — not the primary
mechanism, and re-evaluate its role once ADR-0065 Phase 3 ships a real read API.

### Option D — Publish stage outputs into existing variable/secret stores via `StoreIntegration.set_*` (RECOMMENDED)

Add a declarative `publish_outputs:` mapping on a deployment stage. After that stage's
`collect_outputs()` succeeds, strata writes each listed output into a real variable or
secret store using APIs that already exist and are already unused for this purpose —
`StoreIntegration.set_variable()` / `set_secret()` (every backend: Vault, Consul, Azure
App Config, Bitwarden, etc.). The downstream pipeline then just declares an ordinary
`var:`/`secret:` in its own `environment.yaml`, pointed at the same store and key —
**zero new resolution code anywhere else in strata**, because `var:`/`secret:` resolution
already exists and already speaks every one of these backends.

- Pro: reuses the read side (`var:`/`secret:`, `ValueController`, every store integration)
  completely unchanged.
- Pro: network-reachable and access-controlled by construction — this is what
  Vault/Consul/Azure App Config/Bitwarden are for; no new remote protocol invented.
- Pro: naturally preserves the sensitive/non-sensitive split — `secret:`-classed outputs
  can only be published via `set_secret` into a real secret store, never a config store.
- Pro: precedent already exists in the codebase for "strata writes into a store" —
  `ValueController._resolve_variable()`'s seed-on-missing path already calls
  `integration.set_variable(key, item.default)` when a declared variable's default needs
  writing on first use.
- Con: genuinely new surface area — a new stage-level model field, new deploy-orchestrator
  wiring (a write step after `collect_outputs()`), new validation (tenant/deployment key
  scoping, sensitivity routing), and new failure-mode handling.

**This is the winning option**, with Option C (state service) adopted as a secondary,
lower-priority channel for the same output data where an operator already runs the state
service and mainly wants audit/discovery rather than direct consumption — most consumers
are still expected to prefer a store (Option D), matching the pattern nearly every other
`var:`/`secret:` producer in strata already uses.

## Decision Outcome

**Option D (store-backed publishing)** as the primary mechanism, **Option C (state
service)** as an optional secondary/audit channel for the same data, explicitly paired
with **ADR-0058's `spec.requires`** for ordering (this ADR only ever addresses "what
values did upstream produce," never "is upstream done").

### Detailed design (sketch — not fully specified, subject to review during implementation)

```yaml
# bootstrap_customer.yaml — Pipeline A
stages:
  - name: infrastructure
    provisioner: terraform_azure
    publish_outputs:
      - key: namespace_name                # this stage's Terraform output name
        save_as: {variable: acme_namespace_name, store: azure_appconfig}
      - key: keyvault_uri
        save_as: {variable: acme_keyvault_uri, store: azure_appconfig}
      - key: db_admin_password
        save_as: {secret: acme_db_admin_password, store: azure_keyvault}   # sensitive → secret store only
```

```yaml
# deploy_environment.yaml — Pipeline B, a completely separate pipeline/invocation
spec:
  variables:
    - key: keyvault_uri
      store: azure_appconfig
      value: acme_keyvault_uri        # ordinary var: read — no new strata code needed here
  secrets:
    - key: db_admin_password
      store: azure_keyvault
      value: acme_db_admin_password
```

Key rules to enforce during implementation:

1. **Tenant/deployment-scoped key naming is mandatory, not advisory.** A flat namespace
   collides across tenants on day one at the scale `docs/guides/at-scale.md` describes.
   Validation should require (or auto-derive) a prefix, not just document a convention.
2. **Sensitivity routing is enforced, not trusted.** An output Terraform marks `sensitive`
   must not be publishable via `save_as.variable` — only `save_as.secret`, into a
   `SecretStoreType`-backed store. Mirrors the split `deployment-outputs.json` already
   enforces.
3. **Write failure is fatal to the deploy**, matching `resolved.store_unavailable_errors`'
   existing always-fatal behavior — never a warning-and-continue.
4. **Idempotent overwrite, not append.** Re-running `bootstrap_customer` must overwrite the
   previously published value, not create a duplicate/versioned entry (that's what the
   state service / `deployment-outputs.json` are for, if history matters).
5. **Staleness on the consuming side** is already governed by ADR-0026's
   `resolved_values` cache (`--refresh-cache`) — confirm published keys aren't served from
   a stale cache entry without an explicit refresh.
6. **Provenance stays consistent with `deployment-outputs.json`.** Both are derived from
   the same `collect_outputs()` call; they should never disagree about what a stage
   produced.

**Generated vs. pass-through secrets — mechanically identical, governance is not.** A
sensitive output can be a value Terraform *generates itself* (e.g. `random_password`,
`random_id`, `tls_private_key` — created at `apply` time, stored only in Terraform state,
stable across reruns unless the resource is recreated) or a value that merely *passes
through* an already-existing input (`variable "x" { sensitive = true }` supplied by the
caller, re-exported via `output`). `collect_outputs()`/`publish_outputs` cannot distinguish
the two — both surface identically as `{"sensitive": true, "value": ...}` from `terraform
output -json`. For a **generated** secret, the promoted copy (secret-store or otherwise)
becomes the *only* durable record of that value outside Terraform state itself — there is
no other upstream source of truth to fall back on if the promoted copy is lost, unlike a
pass-through secret which always has an origin elsewhere. Worth keeping in mind for rule 4
(idempotent overwrite is safe either way, since a generated secret is stable across
reruns) and operational runbooks (losing a promoted *generated* secret means losing access
to it entirely, short of re-running `apply` against the same, un-tainted state).

**Important: for a value strata can generate itself, `publish_outputs` is usually the
wrong tool — prefer strata's existing generate-on-missing `secret:` mechanism instead.**
`SecretStoreModel.generate` (`{type, length}`) already makes `ValueController._resolve_secret()`
auto-generate and write a missing secret on first read (`strata secret put --generate` /
`strata secret rotate` do the same, ad hoc). For a value like `db_admin_password` that
strata itself is free to synthesize (not derived from anything Terraform provisions),
Pipeline A and Pipeline B can each simply declare the *same* `secret: {key: ..., store:
..., generate: {...}}` pointed at the same store/key — whichever pipeline runs first seeds
it, the other reads the already-seeded value — with **zero** `publish_outputs` involvement
and no Terraform `random_password` resource needed at all. `publish_outputs`/this ADR is
only the right tool for values that genuinely *cannot* be pre-declared this way — outputs
whose value is determined by what Terraform actually provisions and doesn't exist until
after `apply` (`keyvault_uri`, `namespace_name`, an assigned IP, a resource ID). The ADR's
own `db_admin_password` example (Context section) is illustrative shorthand, not a
recommendation to route strata-generatable secrets through this mechanism — a real
implementation should prefer the existing `generate:` spec for anything that qualifies.

#### Selectable output destination — store, server, or both (design only)

The two channels from the Decision Outcome (Option D's stores, Option C's state service)
are exposed through the *same* `publish_outputs[].save_as` declaration, generalized from a
single mapping to a **list** of one or more targets per output key — an operator picks a
store, the server, or both, per key, rather than one ADR-wide policy. The two channels
answer different questions ("what is the value *now*?" vs. "what values has this stage
*ever* produced?") and neither subsumes the other (see Option C's cons above), so this is
additive to the store-only shape above, not a replacement.

```yaml
stages:
  - name: infrastructure
    provisioner: terraform_azure
    publish_outputs:
      - key: namespace_name
        save_as:
          - { variable: acme_namespace_name, store: azure_appconfig } # current value — var: reads this
          - { server: true }                                          # + history record on the state service
      - key: keyvault_uri
        save_as:
          - { variable: acme_keyvault_uri, store: azure_appconfig }
      - key: db_admin_password
        save_as:
          - { secret: acme_db_admin_password, store: azure_keyvault } # sensitive → secret store only
          # {server: true} NOT permitted here — see rule 7 below
```

**Store target (`{variable: ..., store: ...}` / `{secret: ..., store: ...}`)** — unchanged
from the design above (rules 1-6): current-value semantics, overwritten idempotently on
every publishing run, read back via an ordinary `var:`/`secret:` declaration.

**Server target (`{server: true}`)** — routes through the same event-forwarding path
already built for `cost.recorded`/`drift.recorded`/`manifest.recorded` (ADR-0065 Phase 2):
a new `output.published` event type (category `"event"`, default on), emitted via the
existing `AuditController.forward()`/`_build_envelope()` machinery — no new transport, no
new auth model, reusing ADR-0067's server identity/authentication wholesale.

- **Identity**: `execution_id` = the deploying command's own `self._execution_id` (the
  same choice `manifest.recorded` made, not a fresh UUID) — a retry of the *same* deploy
  invocation converges on the same row; a genuinely new run of the same stage legitimately
  creates a *new* row. `record_type = f"output_published:{stage_name}:{key}"` so multiple
  published keys from one run don't collide on the idempotent `(execution_id, record_type)`
  primary key.
- **Payload**: `{stage, key, value, provisioner, published_at}` plus the standard
  `deployment`/`workspace`/`environment` top-level fields `_build_envelope()` needs —
  mirrors `manifest.recorded`'s "payload verbatim, promote identity fields to top level"
  shape.
- **History, not current-value — by default, but a "latest" projection can close most of
  that gap without breaking ADR-0065's projection invariant.** Reading back "the current
  keyvault URI" as raw event history means "find the most recent `output.published` row
  for this deployment/key," a slower access pattern than a KV `get`. But ADR-0065's
  invariant — *"the state service is a queryable projection, never the source of
  truth"* — doesn't forbid a materialized "latest value" index; it only forbids the state
  service becoming anyone's sole/authoritative copy. A small `published_output_latest`
  table, **upserted** (not appended) from the same `output.published` events at ingest
  time, keyed by `(deployment, stage, key) → {value, published_at, execution_id}`, is
  itself just a derived projection of the append-only `events` table — fully rebuildable
  by replaying it in order (`TRUNCATE` + replay), same discipline ADR-0026 already applies
  to `cache.db`. This turns "give me the current keyvault URI" back into an indexed
  lookup, and — being narrowly scoped to one table/one query shape — doesn't need to wait
  for the full, still-deferred, generic Phase 3 read API; it could ship as a single
  purpose-built endpoint (e.g. `GET /v1/outputs/latest?deployment=...&key=...`) alongside
  the event-forwarding work itself.
  - **Still never wired into `var:`/`secret:` resolution.** Consuming this projection stays
    a deliberate, separate query (a CLI command or direct API call) — never a substitute
    for declaring a store-backed `var:`/`secret:`. Making it resolvable inline would
    reintroduce exactly the hard dependency Option D was chosen to avoid (Option D's whole
    "zero new resolution code" pro rests on the store, not the server, being load-bearing).
  - **Durability gap if `{server: true}` is the *only* target for a key.** Per ADR-0065's
    invariant, forwarding a record without a durable store elsewhere "inverts the invariant
    rather than satisfying it" (the same trap drift/cost history fell into pre-Phase-1). A
    non-sensitive key published *only* via `{server: true}` still has a fallback — the same
    `collect_outputs()` call also produces `deployment-outputs.json` (rule 6), which is
    durable *if and only if* the deployment's own git-push is configured (ADR-0018-style).
    Without that configured, a server-only key's *only* surviving copy is the state
    service's projection, which is exactly the anti-pattern this invariant warns against.
    Worth a validation warning (not necessarily a hard error) when `{server: true}` is
    declared with no accompanying store target and no manifest git-push configured.

**Is "never the source of truth" permanent, or just true of what's built today? (design
only)** Genuinely just the latter — it is not a universal law that a history/audit service
can't also be authoritative. Terraform Cloud/Enterprise (`app.terraform.io`) is a working
counterexample already: the *same* product is both the authoritative, locked, versioned
state backend **and** run history/policy/audit — one system, two roles, not a
contradiction. ADR-0065's invariant holds specifically because of one concrete fact about
what is actually built today: `src/strata/server/db/store.py` exposes exactly one function,
`insert_event()` — there is no `UPDATE`/upsert capability in the database layer at all, by
deliberate design (insert-only grants limit credential blast radius, per ADR-0065's own
stated reasoning). That is *why* it can't be a current-value store today, not a claim that
it structurally never could be. Even the "latest projection" idea above already requires
upsert semantics that don't exist yet — building that is real, new work, not free.

The honest long-term shape, if/when that update capability is ever built with proper
locking/consistency: don't keep `{server: true}` as a permanently-special, best-effort side
channel — instead let the state service register as a genuine `StoreIntegration`
(`get_variable`/`set_variable`, the same interface Vault/Consul/Azure App Config already
implement). At that point `publish_outputs`'s `{server: true}` target disappears entirely;
a workspace just declares `{variable: x, store: state_service}` like any other backend, and
Option C collapses into Option D completely — no more asymmetric fatal/best-effort split
(rule 8), no separate event semantics to reason about for this purpose. That is explicitly
**out of scope for this ADR** (it depends on state-service capability this ADR doesn't
build), but it is the right target to design toward rather than assuming the server/store
split is permanent.

New validation rules for the server target:

7. **Sensitivity gate applies identically to the server target.** An output the
   provisioner marks `sensitive` may not use `{server: true}` — same enforcement as rule 2,
   for the same reason (state-service events never carry secret values, matching
   `deployment-outputs.json`).
8. **`{server: true}` is best-effort, not fatal, if no state service is configured or
   unreachable** — mirrors `AuditController.forward()`'s existing best-effort contract for
   every other event type (manifest/cost/drift forwarding failures are caught and logged,
   never raised). A published-output history record is a nice-to-have audit trail, not a
   value dependency worth blocking a deploy on. This is the one deliberate asymmetry versus
   the store target: store-write failure is fatal (rule 3), server-write failure is not —
   only the store target is ever a real, load-bearing dependency for a downstream pipeline.
9. **`save_as` must list at least one target.** An empty list is a validation error —
   silently publishing nothing is indistinguishable from a typo.

#### Map/object-valued outputs (design only)

Terraform (and other provisioners) can produce a `map`/`object`-typed output, not just a
scalar (e.g. `output "tags" { value = { env = "prd", team = "platform" } }`).
`collect_outputs()` already returns whatever JSON value Terraform emits verbatim —
`non_sensitive[key]`/`sensitive[key]` can already be a `dict`, not just a `str`.
`publish_outputs[].key` therefore already supports naming a map-typed output as-is — no new
YAML surface is needed to select "the whole map" versus "one field of it"; that's simply the
difference between naming the map output itself (`key: tags`) versus a Terraform output that
already extracts a single field (`output "team" { value = local.tags.team }`, a separate
scalar `key: team`). Per-target round-trip fidelity differs, though, and needs explicit rules:

10. **Variable-store target already round-trips maps natively.** Every `set_variable()`
    signature in the codebase is typed `value: Any` (Vault, Consul, etcd, Azure App Config,
    Flagsmith), and `ValueController._resolve_variable()` returns `Optional[Any]` on the read
    side — a map-typed output published via `{variable: ..., store: ...}` survives as a real
    dict for consumers already, with no new coercion code required.
11. **Secret-store target does not — every `set_secret()` is typed `value: str`.** A
    map-typed *sensitive* output must be JSON-serialized before the write (`json.dumps`), and
    comes back through `secret:` as a JSON **string**, not a native dict — unlike the
    variable path. Consuming YAML that needs individual fields out of a published map-typed
    secret must parse it itself (e.g. Jinja2 `| fromjson`); this ADR does not attempt a
    `secret:`-side auto-coercion. Document this asymmetry explicitly, or operators will
    wrongly assume both targets round-trip maps identically.
12. **Server target already handles maps natively, no special-casing needed.**
    `manifest.recorded`'s own producer already keeps its full nested payload verbatim (ADR-0065
    Phase 2 precedent); `output.published`'s payload does the same, so a map-typed value
    published via `{server: true}` reaches the state service as real nested JSON, not a
    stringified blob.
13. **Sensitivity is whole-output, not per-field — mixed-sensitivity maps have no clean
    answer.** Terraform's `sensitive` flag (`descriptor.get("sensitive", False)` in
    `collect_outputs()`) is set on the *entire output block*, propagated automatically from
    any sensitive input variable, sensitive provider-schema attribute (e.g.
    `azurerm_key_vault_secret.value`, `random_password.result`), sensitive module output, or
    explicit `sensitive()` call anywhere in the expression that produced the value. A map
    output with even one field derived from a sensitive source is marked sensitive *in full*
    — there is no way to see "only `db_admin_password` inside this map is secret, the other 4
    keys aren't." Rule 2's routing gate therefore forces the **whole map** through
    `save_as.secret` (and rule 11's JSON-string round-trip), even for sibling fields that
    aren't actually secret. This ADR does not attempt per-field un-mixing — operators who
    need mixed sensitivity within a logical group should split it into separate Terraform
    outputs at the source (one sensitive, one not), not rely on `publish_outputs` to do the
    splitting for them.

Consequences specific to the dual-channel design: lets operators choose fidelity per key
(store for values a downstream `var:`/`secret:` depends on, server for values worth
auditing, both for values that are both) without changing the store-only behavior at all
when no state service is configured (server-target declarations simply never fire, per
rule 8) — but the fatal/best-effort asymmetry between the two targets (rule 3 vs. rule 8)
needs to be documented clearly, or operators may wrongly assume a `{server: true}` failure
blocks the deploy the same way a store failure does.

### Consequences

- Good: cross-pipeline data sharing becomes a normal, declared `var:`/`secret:` — the same
  mental model operators already use everywhere else in strata, not a bespoke mechanism
  per consumer.
- Good: composes with point 2's DNS `output_key:` (same-invocation shortcut) and ADR-0058's
  `spec.requires` (ordering) without overlapping either.
- Good: the state service (ADR-0065) gets a plausible, additive use case (audit/discovery
  of published outputs) without becoming a hard dependency for anyone who doesn't already
  run it.
- Bad: real new surface area — a new model field, deploy-orchestrator write step, and
  validation rules, none of which exist today.
- Bad: introduces a second place (alongside `deployment-outputs.json`) that records "what
  did this stage output" — must be kept consistent by construction (same call site), not
  by convention, or the two will drift.
- Neutral: does not remove Option A (manual `deployment-outputs.json` extraction) — it
  remains a valid low-frequency escape hatch; this ADR only stops it being the *only*
  answer.
