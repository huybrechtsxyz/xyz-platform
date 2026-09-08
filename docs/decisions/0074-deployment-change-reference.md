# Record a change reference and justification on every deployment

- Status: implemented — Phase 1 (capture and record) and Phase 2 (enforcement, both `deploy run` and `deploy destroy`) implemented; Phase 3 (verification) considered and deliberately not adopted as deploy-time work — see "What this explicitly does not claim"
- Date: 2026-09-08

## Context and Problem Statement

Strata records a great deal about *what* a deployment did, and nothing about *why* it happened.

Everything already captured is a fact observable from inside the deploying
process — actor, timestamps, commit, pull request, stages, policy results,
lock trail, outcome:

- `DeploymentManifestSpecModel`
  ([src/strata/models/deployment_manifest_model.py](../../src/strata/models/deployment_manifest_model.py#L225))
  — `deployment_name`, `workspace_name`, `environment`, `action`, `status`,
  `started_at`/`completed_at`, `deployed_by`, `artifacts`, `stages`,
  `policy_results`, `lock`, `audit_log`.
- `DeployLogModel`
  ([src/strata/models/deploy_log_model.py](../../src/strata/models/deploy_log_model.py#L64))
  — `execution_id`, `commit_sha`/`commit_message`/`commit_author`,
  `pull_request` (GitHub-only enrichment), `force`, `dry_run`, `success`,
  `stages`, plus a free-form `metadata: Dict[str, Any]`.
- Audit events (ADR-0066) forward the whole payload under `data.strata`, with
  `data.user.name` as actor and `data.labels` carrying execution/workspace/
  environment/deployment/tenant.

None of that answers the question an auditor actually asks about a production
change:

> Under whose authority, and against which approved change record, was this executed?

A change-management or compliance audit (ITIL change control, SOC 2 change
management, ISO 27001 A.12.1.2, PCI-DSS 6.5.x) is satisfied by a chain:

> change record → justification → authorization → execution → result

Strata owns the last two links and can prove them. The first two links live in
Jira, Azure DevOps, ServiceNow, GitHub Issues, Linear, or a bespoke internal
system — and today there is no field anywhere in a strata record that names
them. The correlation exists only in a human's head, or in a commit message,
which is unstructured, optional, and frequently absent for a
`deploy run` executed against an already-merged commit.

### Why the existing mechanisms do not close this

- **Pull-request enrichment** (`DeployLogPullRequestModel`,
  [src/strata/models/deploy_log_model.py](../../src/strata/models/deploy_log_model.py#L45))
  captures PR number/title/author/labels/`linked_issues` — but only for GitHub,
  only when the commit maps to a *merged* PR, and it evidences code review, not
  change authorization. A rollback, a re-run, a `destroy`, or a deploy of an
  unchanged commit has no PR at all.
- **Work items and gates** (ADR-0057, ADR-0059) are strata's *internal*
  orchestration of a hand-off: `WorkItem.id` identifies a pause created by
  strata, resolved by `strata workitem approve`. That proves an approval
  happened *inside strata*; it does not identify the external business change
  record the deployment implements, and gates only exist where a gate was
  configured and triggered.
- **`DeployLogModel.metadata`** can already hold anything, which is exactly the
  problem: unstructured, unvalidated, absent from the manifest and from the
  metrics record, and invisible to any consumer that does not already know the
  key names a particular team invented.

### Relationship to ADR-0064's rejected `--remediates`

[ADR-0064](0064-deployment-metrics-record.md) considered and rejected
`strata deploy run --remediates <execution_id>`, on the grounds that it "demands
discipline exactly when discipline is scarcest — under incident pressure, with
the prior `execution_id` needing to be looked up first," and that sparse,
biased data is worse than absent data because it still looks like data.

That reasoning stands for what it was about: deriving *recovery metrics* (MTTR,
change failure rate) from operator-supplied linkage. It does not transfer to
this ADR, for three reasons:

1. **Different consumer, different failure mode.** A metric silently skewed by
   sparse input is worse than no metric. An audit field that is *sometimes*
   present is still evidence where present, and its absence is itself an
   auditable finding rather than a corrupted average.
2. **Different burden.** `--remediates` required looking up a strata-internal
   `execution_id` from a prior run. A change reference is the ticket the
   operator already has open in another tab — it is the thing that caused them
   to run the command.
3. **Absence can be made non-optional.** Metrics cannot force operators to
   supply input; a deployment can refuse to proceed. That converts "sparse and
   biased" into "present or blocked", which is the property that makes the data
   trustworthy.

## Decision Drivers

- **Provider-neutral.** Jira, Azure DevOps, ServiceNow, GitHub, Linear, and
  internal systems must all be first-class. No field, enum, or code path may
  privilege one.
- **Structured, not free-text.** The reference must be machine-correlatable
  (`system` + `id`) so a SIEM query can answer "list production deployments with
  no change record" without parsing prose.
- **One implementation, not copies.** Per
  [Introducing a new convention](README.md#introducing-a-new-convention), the
  same model must be reused by the manifest, the deploy log, the audit event,
  and the metrics record — not re-declared per record type.
- **No new embedded string syntax.** ADR-0073 exists because compact embedded
  syntaxes accumulated independently. A `jira:PROJ-1234` mini-format would be
  exactly that mistake again.
- **Minimal per-invocation typing.** Whatever the operator must supply at 03:00
  during an incident is what determines whether this is used at all.
- **Enforcement is configuration, not code.** Production may require a change
  record; a developer sandbox must not. That distinction already has a home.
- **Evidence, not forgery-proof attestation.** Strata records what it was told
  and by whom. It cannot, in Phase 1, prove the referenced record exists or was
  approved — and must not imply that it can.

## Considered Options

### Option A — Free-form `metadata` convention (documented, not modelled)

Publish a documented key convention inside the existing
`DeployLogModel.metadata` dict, e.g.
`metadata["change_reference"] = {...}`.

- Good: zero schema change; ships immediately.
- Bad: no validation, no discoverability, no enforcement hook, absent from the
  manifest and metrics record, and every consumer must defensively handle a
  missing/misspelled key. Precisely the "second hand-rolled copy per call site"
  outcome ADR-0073 warns about.

### Option B — Per-provider integrations (a Jira integration, an ADO integration, …)

Model each tracker as an integration that fetches and validates the ticket.

- Good: strongest evidence — the reference can be verified to exist.
- Bad: enormous surface for the value delivered; requires credentials on the
  deploy hot path; blocks air-gapped and offline deploys; unusable for internal
  or unsupported trackers; couples "record the reference" (cheap, always
  possible) to "verify the reference" (expensive, sometimes impossible).

### Option C — Reuse the work-item/gate mechanism (ADR-0057)

Model the change record as a gate that must be resolved before deploying.

- Good: reuses shipped machinery; already produces audit events.
- Bad: category error. A gate is a *pause awaiting a decision*; a change
  reference is *metadata about an already-authorized change*. Forcing a pause on
  every deployment to record a ticket number would make the common path worse,
  and gates are per-deployment configuration, so the field would still be
  absent wherever no gate was declared.

### Option D — First-class `ChangeReferenceModel`, supplied at invocation, enforced by policy (recommended)

A single shared Pydantic model, populated from CLI flags or environment
variables, written into the manifest, the deploy log, and the forwarded audit
event; requirement expressed as a policy so it can be scoped per environment.
Verification against the live system is a separate, later, optional phase.

- Good: structured and validated; one implementation; provider-neutral;
  enforceable where it matters and absent where it does not; works offline.
- Bad: a new field on two record models and a new policy type; the captured
  `title` is a point-in-time snapshot that can go stale.

## Decision Outcome

Chosen: **Option D**, because it is the only option that produces
machine-correlatable audit evidence without putting an external system on the
deploy hot path, and because it separates *recording* the reference (always
possible) from *verifying* it (sometimes possible), instead of conflating them.

### The model

One new shared model, `ChangeReferenceModel`, in
`src/strata/models/change_reference_model.py`, imported by every record that
carries it:

```python
class ChangeReferenceModel(PlatformBaseModel):
    system: str                  # "jira" | "azure_devops" | "servicenow" | "github" | "linear" | any internal name
    id: str                      # "OPS-1234", "CHG0041234", "12345"
    reason: str                  # operator justification, free text
    classification: Optional[str] # e.g. "emergency" | "normal" | "standard" — open string, org-defined
    title: Optional[str]         # snapshot of the record's title at invocation time
    url: Optional[str]           # resolved from configuration's url_template, or supplied
    supplied_by: str             # resolved actor, same source as manifest.deployed_by
    supplied_at: str             # ISO-8601
```

`system` is an **open string, not an enum**. An enum would force a strata
release for every team with an internal tracker, and the value's purpose is
correlation by a downstream consumer that already knows its own systems.

`reason` is **free text and required** whenever a reference is supplied.

`classification` resolves Open Question 1: it is an **optional, open string**,
never a fixed strata enum. Instead of hard-coding ITIL's `emergency` / `normal`
/ `standard` (or any other scheme) into the model, the *allowed values* are
declared in configuration (`change_tracking.classifications`) and validated
against that allowlist only when both the flag and the allowlist are present.
An organization not using ITIL can declare its own scheme (or none at all) with
no strata code change. This keeps strata as the transport for a classification
the external record already carries, rather than a second, hard-coded source of
truth that drifts — the same reasoning that already keeps `system` open. It
remains optional even when an allowlist is configured, consistent with nothing
in Phase 1 being required by default.

`title` is captured for offline readability — so an audit review does not
require live access to a tracker that may have archived the record. It is
explicitly **evidence, not proof**: titles are editable after the fact. The
durable correlation key is `system` + `id`.

### How it is supplied

Configuration declares the workspace's tracker once, so the operator supplies
only the volatile part:

```yaml
# configuration.yaml
spec:
  change_tracking:
    system: jira
    url_template: "https://jira.example.com/browse/{id}"
    id_pattern: "^[A-Z][A-Z0-9]+-[0-9]+$"
    classifications: [emergency, normal, standard]  # org-defined; any list, or omit entirely
```

`id_pattern` is a **standard regular expression**, not a bespoke pattern
dialect — reuse over invention, per the convention rule. `url_template`
substitutes `{id}` only. `classifications` is likewise an open list — not a
fixed strata enum — validated against only when a classification is actually
supplied.

Invocation then requires two values:

```bash
strata deploy run -f deploy/deploy-prd.yaml \
  --change-id OPS-1234 \
  --reason "Restore checkout capacity after connection-pool exhaustion"
```

with `--change-system`, `--change-title`, `--change-url`, and
`--change-classification` available to override or supply what configuration
cannot. For CI, each flag has an environment-variable equivalent
(`STRATA_CHANGE_ID`, `STRATA_CHANGE_SYSTEM`, `STRATA_CHANGE_REASON`,
`STRATA_CHANGE_CLASSIFICATION`, …), following the existing flag/env
precedence.

**No compact `system:id` form is introduced.** ADR-0073 catalogued the cost of
exactly this kind of convenience syntax; `--change-system` plus `--change-id`
carries the same information with no parser, no escaping rules, and no second
place for the parsing logic to drift.

### Where it is recorded

The same model instance, serialized identically, into:

1. `DeploymentManifestSpecModel.change_reference` — the durable per-deployment
   artifact.
2. `DeployLogModel.change_reference` — a real field, not a `metadata` key,
   alongside `commit_sha` and `pull_request`.
3. The forwarded audit event, inside `data.strata` (no envelope change needed;
   ADR-0066 routing already carries the payload).
4. The ADR-0064 metrics record, as a **dimension** (`change_system`,
   `change_id`). `change_id` is unbounded and therefore **not** `label_safe` —
   it belongs in the record, never as a metric label. `change_classification`
   is a small, bounded set and would be a `label_safe` candidate too. Not yet
   promoted into that record: ADR-0064 itself is still `proposed`, so there is
   no metrics record to add a dimension to yet — this is a design note for
   whoever implements ADR-0064, not outstanding work under this ADR.

It is written **before provisioning begins and on every terminal outcome**,
including failures and rejections. A failed authorized change is exactly the
event an audit most wants to see; recording it only on success would omit it.

`dry_run` invocations do not require a change reference (nothing is changed),
consistent with ADR-0064's exclusion of dry-runs.

### How it is required (implemented 2026-09-08 for `deploy run` and `deploy destroy`)

Requirement is expressed as a **policy**, `change_reference_required`, not a
hard-coded rule — registered in the existing engine's `_builtin` registry
(`PolicyEngine._create()`,
[src/strata/validators/policies/policy_engine.py](../../src/strata/validators/policies/policy_engine.py)),
reusing its existing `deny` | `warn` | `audit` enforcement values with no new
enforcement mode:

```yaml
policies:
  - name: production-change-record-deploy
    type: change_reference_required
    phase: deploy_before
    enforcement: deny

  - name: production-change-record-destroy
    type: change_reference_required
    phase: destroy_before
    enforcement: deny
```

**Correction from the original design sketch above:** `PolicyModel` has no
`scope:` field — per-environment scoping isn't a property of an individual
policy, it comes from *which configuration file* declares the policy (the
same layered `configuration.spec.policies` mechanism every other policy
already uses). A production-only requirement is achieved by declaring the
policy only in the configuration layer(s) that apply to production, not by a
`scope:` key on the policy itself.

`phase: deploy_before` (for `deploy run`) and `phase: destroy_before` (for
`deploy destroy`) are **two new, independent phase values**, each evaluated
once per invocation before any stage executes — deliberately not `plan` or
`deploy` (the two phases stage-scoped policies like `cost_threshold` already
use), because this policy needs no plan/deployer/stage context, and
evaluating it per-stage would duplicate the same violation once per stage on
a multi-stage deployment. `PolicyContext` gained a `change_reference` field
for it. Implementation:
[src/strata/validators/policies/change_reference_required_policy.py](../../src/strata/validators/policies/change_reference_required_policy.py),
wired in via a single shared
`BaseDeployCommand._evaluate_preflight_policies(phase: str)` in
[src/strata/commands/deploy/base_deploy_command.py](../../src/strata/commands/deploy/base_deploy_command.py)
— one implementation, not a copy per command, per the
[Introducing a new convention](README.md#introducing-a-new-convention) rule.
`RunDeployCommand` calls it with `"deploy_before"`
([src/strata/commands/deploy/run_deploy_command.py](../../src/strata/commands/deploy/run_deploy_command.py));
`DestroyDeployCommand` calls it with `"destroy_before"`
([src/strata/commands/deploy/destroy_deploy_command.py](../../src/strata/commands/deploy/destroy_deploy_command.py)).
Both call it once from their own `_execute_provisioning()`, before the stage
loop begins (same fail-fast placement as the existing pre-flight provisioner
check) — but **skipped entirely for `--dry-run`**, per "nothing is changed"
above.

**`--force` does not bypass this**, verified in the implementation: `--force`
only affects confirmation prompts and approval gates elsewhere in the
command; nothing gates the policy evaluation behind it, for either command.

**Both `deploy run` and `deploy destroy` evaluate this policy — as two
independent phases, not one shared phase.** A policy declared for
`deploy_before` has no effect on `destroy_before` and vice versa, so a
workspace can require a reference for one action without the other, or set
different enforcement levels per action (e.g. `deny` on `destroy_before`
while only `warn` on `deploy_before`, since destroying infrastructure is
arguably the riskier action).

A `deny` result here behaves like every other `plan`/`deploy`/`deploy_before`/
`destroy_before` phase policy denial: it aborts the pipeline with **exit code
1** (system/execution error) — not exit code 3, which is reserved for
schema/cross-reference validation failures (ADR-0004). This matches existing
policy-denial behavior for `deploy run`, not something new introduced by this
policy.

### Policy coverage across the other `deploy` subcommands

`change_reference_required` (and, more generally, any policy gating whether a
deployment change is *authorized*) is only meaningful for subcommands that
actually mutate infrastructure. Auditing every `deploy` subcommand:

| Subcommand                            | Mutates infrastructure?                        | Evaluates policies today                |
| ------------------------------------- | ---------------------------------------------- | --------------------------------------- |
| `run`                                 | Yes                                            | Yes (`plan`, `deploy`, `deploy_before`) |
| `destroy`                             | Yes                                            | Yes (`destroy_before`)                  |
| `show`                                | No (read-only)                                 | No                                      |
| `plan` (display saved `.tfplan`)      | No (read-only)                                 | No                                      |
| `list`                                | No (read-only)                                 | No                                      |
| `history`                             | No (read-only)                                 | No                                      |
| `health`                              | No (runs checks, no apply)                     | No                                      |
| `status`                              | No (read-only)                                 | No                                      |
| `drift run`                           | No (detection only)                            | No                                      |
| `drift acknowledge`                   | No (marks drift accepted, applies nothing)     | No                                      |
| `output`                              | No (read-only)                                 | No                                      |
| `lock status` / `release` / `history` | No (lock bookkeeping, not a deployment change) | No                                      |

**Decision: only `run` and `destroy` are candidates for `deploy`-level policy
enforcement.** Extending policy evaluation to the read-only subcommands would
be the same category error Option C ("reuse the gate mechanism") was rejected
for above — there is no change to authorize, so nothing to gate. This isn't
new scope creep to defer; it's a scope boundary that doesn't need revisiting
later.

**Implementation notes for how `destroy` was wired in (2026-09-08):**

1. **Extracted the one-shot evaluator up to `BaseDeployCommand`,
   parameterized by phase name**, instead of copy-pasting a second version
   into `DestroyDeployCommand` — per the
   [Introducing a new convention](README.md#introducing-a-new-convention) rule
   ("one implementation, not copies"). `RunDeployCommand` no longer has its
   own `_evaluate_deploy_before_policies()` — both commands now call the
   shared `BaseDeployCommand._evaluate_preflight_policies(phase: str)`.
2. **`destroy` got its own phase, `destroy_before` — not a reuse of
   `deploy_before`.** Two distinct phase values so a workspace can express
   different strictness per action without any new mechanism.
3. **Call site:** `DestroyDeployCommand._execute_provisioning()`
   ([src/strata/commands/deploy/destroy_deploy_command.py](../../src/strata/commands/deploy/destroy_deploy_command.py))
   calls `self._evaluate_preflight_policies("destroy_before")` at the same
   fail-fast point `run` uses: right after stage/`--scope` filtering, before
   any stage executes — guarded by `if not self._dry_run`, identical
   semantics to `run`.
4. **No Phase 1 changes were needed.** `--change-id`/`--reason`/etc. and
   `BaseDeployCommand._resolve_change_reference()` already ran for `destroy`
   before this change (Phase 1 was never `run`-only) — only the *enforcement*
   wiring was missing.
5. **Not in scope, and not conflated with this:** `deploy drift
   acknowledge --reason` and `deploy lock release --force` are different
   concepts (drift-acceptance rationale; lock-ownership override) — neither
   was merged with `change_reference`, and neither has policy coverage under
   this ADR.

### What this explicitly does not claim

Strata records an **assertion made by an identified actor at a known time**.
It does not prove the referenced record exists, that it was approved, or that
its scope covers this deployment — and, per the decision below, it does not
try to at deploy time.

**Verification is parked, not scheduled, and reframed away from a deploy-time
feature (2026-09-08).** The original sketch ("Phase 3: pluggable lookup,
adds `verified`/`verified_at`/`status_at_deploy` to the same record") is
withdrawn, for two compounding reasons surfaced by working through it:

1. **A synchronous HTTP check at deploy time is unreliable in exactly the
   cases that matter.** Every mainstream tracker requires authentication;
   an anonymous request to a real ticket and to a typo'd one both typically
   redirect to the same login page, which itself returns `200`. A clean
   `404` — the only case that's real signal — is the rare case, not the
   common one. A feature that can't reliably tell "exists" from "doesn't"
   is worse than no feature, because it manufactures false confidence in
   the common (ambiguous) case.
2. **It doesn't need to be on the deploy hot path at all.** The same
   reasoning ADR-0064 already applied to metrics — *"emit facts; aggregate
   downstream. NOT aggregate-at-deploy-time"* — applies here. Strata's job
   is to emit `system`/`id`/`url`/`reason` faithfully, which Phase 1/2
   already do completely; whether that reference actually resolves is a
   reconciliation question, answerable at any later time, by a process that
   can afford a real authenticated API call instead of a hopeful anonymous
   `GET`.

**Where real verification belongs instead:** once [ADR-0065](0065-strata-state-service.md)'s
server matures past its current event-ingest scope, it is the natural home
for this — it already receives the deployment/manifest events carrying every
`change_reference`, across every workspace, over time. A reconciliation pass
run there (on its own schedule, with its own credentials, correlating
multiple deployments against the same ticket if useful) is a strictly better
design than a per-deploy-process, per-invocation, unauthenticated guess:
failures don't block anyone's deploy, and a real API call gives a real
answer instead of a login-page coin flip. Until the server is mature enough
for that, this stays **parked — not designed further, not scheduled** —
rather than left as a vague "Phase 3, someday" placeholder. See Remaining
Work.

### Consequences

- Good: a SIEM or audit query can answer "which production deployments had no
  change record?" and "show every deployment for CHG0041234" directly, without
  parsing prose or joining on a commit message.
- Good: provider-neutral — internal trackers work on day one with no strata
  change.
- Good: the recording path has no network dependency, so air-gapped and
  offline deployments are unaffected.
- Good: one shared model, so the manifest, deploy log, audit event, and metrics
  record cannot drift apart.
- Bad: two new fields on two record models, a new configuration block, and a
  new policy type — consumers of the manifest/deploy-log JSON see a schema
  addition (additive and optional, so existing consumers are unaffected).
- Bad: `title` and any later `status_at_deploy` are point-in-time snapshots and
  can misrepresent the record's current state. Mitigated by documenting them as
  evidence, and by keeping `system` + `id` as the authoritative correlation key
  for re-verification against the live system.
- Bad: an operator can supply a syntactically valid but meaningless id — this
  is accepted as a permanent limitation of deploy-time recording, not a gap
  Phase 3 will later close (see "What this explicitly does not claim" —
  verification is parked as a future server-side reconciliation capability,
  not a deploy-hot-path feature).
- Neutral: teams that enable the `deny` policy accept that a tracker outage
  which prevents obtaining a change id also blocks deploys. That is the
  intended trade-off of change control, and `warn` exists for teams that want
  the evidence without the gate.

## Open Questions

1. ~~**Change classification.**~~ **Resolved (2026-09-08).** Added as
   `ChangeReferenceModel.classification` — an optional, open string, validated
   against `configuration.spec.change_tracking.classifications` (an org-defined
   allowlist) only when both are present. Not a fixed strata enum: no ITIL
   assumption is baked in, and the field stays optional even when an allowlist
   is configured. See "The model" above.
2. ~~**Multiple references.**~~ **Resolved (2026-09-08).** A single deployment
   can legitimately implement more than one ticket, but Phase 1 supports only a
   single `change_reference` — deliberately, not as an oversight. Widening to a
   list (or a primary reference plus `related_ids`) is a breaking change for any
   consumer of the current shape, so it isn't done speculatively; revisit only
   if a real multi-ticket use case shows up. Until then, a deployment spanning
   multiple tickets picks the one that best represents the overall change (or
   the operator runs `deploy run` once per ticket, if the deployments are
   actually separable).
3. ~~**Relationship to `rollback_of`.**~~ **Resolved (2026-09-08).** A rollback
   deploy supplies its own `change_reference`, the same way every other deploy
   does — via `--change-id`/`--reason` (or the corresponding env vars) at
   invocation. There is no automatic inheritance from the deployment being
   rolled back, for the same reason ADR-0064 already gives for `rollback_of`
   itself: this is **command intent, supplied at invocation, never inferred**.
   A change_reference silently copied forward from a prior deployment would be
   an unverified guess about the operator's current intent, not evidence of it
   — the original ticket might not even cover a rollback (a rollback is
   frequently opened against a *new* incident ticket, not the one that
   authorized the change being reverted).

   The reference **can** be the same value as the original deployment's —
   nothing prevents an operator from passing the identical `--change-id` for
   both the forward deploy and its rollback, if one ticket genuinely covers
   both — but that's the operator's judgment call each time, not a strata
   default.

   **This deliberately does not answer** *how strata learns a given `deploy run`
   is a rollback at all* — that mechanism (`rollback_of` propagation from
   `strata promote rollback` into the subsequent `deploy run` invocation) is
   [ADR-0064](0064-deployment-metrics-record.md)'s own unresolved Open question 1,
   and stays there; ADR-0074 doesn't need it answered, because `change_reference`
   is captured independently of `rollback_of` either way — whichever mechanism
   ADR-0064 eventually picks, it has no bearing on how `change_reference` is
   supplied. The cross-reference note added to ADR-0064 has been updated to
   reflect that only ADR-0064's own linkage question remains open.
4. ~~**`destroy` semantics.**~~ **Resolved (2026-09-08).** `strata deploy destroy`
   is the highest-risk operation and arguably should require a change reference
   under a stricter default than `deploy run` — but this doesn't need a separate
   decision here, because enforcement is already a **policy** (Phase 2), and a
   policy can already be scoped to a specific action/phase rather than applying
   uniformly. A workspace can declare two `change_reference_required` policies
   — `enforcement: deny` for the phase `deploy run` evaluates, `enforcement: warn`
   (or a second `deny`) for whatever phase `deploy destroy` evaluates — with no
   new mechanism, the same way `production-change-record` vs. a looser dev-scoped
   policy already differ by which policies a given configuration declares.
   **Caveat surfaced by this resolution:** `DestroyDeployCommand` does not
   evaluate `spec.policies` at all today — only `RunDeployCommand._evaluate_phase_policies()`
   does, gated on `phase in {"plan", "deploy"}`. For a `destroy`-specific policy
   to be enforceable, Phase 2 must also wire `DestroyDeployCommand` into policy
   evaluation (and decide what phase value(s) it evaluates, e.g. a new `destroy`
   phase) — tracked as a Phase 2 remaining-work item, not a new open question.

   **Implemented (2026-09-08) for both `deploy run` and `deploy destroy`.**
   The evaluated phases are `deploy_before` and `destroy_before` respectively
   (see "How it is required" above) — two independent, once-per-invocation
   phases, not a shared one and not one of the existing per-stage
   `plan`/`deploy` phases. The caveat above was closed the same day: a shared
   `BaseDeployCommand._evaluate_preflight_policies(phase)` now backs both
   commands, so `DestroyDeployCommand` evaluates `spec.policies` too.
