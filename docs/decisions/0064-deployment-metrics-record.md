# Deployment metrics record — emit facts, aggregate downstream

- Status: proposed
- Date: 2026-08-06
- Related: ADR-0018 (deployment audit & traceability), ADR-0022 (SIEM integration), ADR-0031 (cost estimation), ADR-0009 (SBOM), ADR-0008 (drift detection), ADR-0032 (approval gates), ADR-0074 (deployment change reference)

## Remaining Work

- Not started — nothing in this ADR has been implemented yet.

## Context and Problem Statement

DORA metrics (DevOps Research and Assessment) were the entry point for this ADR:

1. **Deployment frequency** — how often we deploy
2. **Lead time for changes** — commit → deployment
3. **Change failure rate** — share of deployments causing degradation
4. **Mean time to recovery** — time to restore service

Strata is the single point through which every infrastructure deployment passes, so it observes the raw material for all four. But scoping this to DORA turned out to be too narrow. The same act of recording a deployment also feeds FinOps, supply-chain, governance, and reliability reporting — the data is identical, only the consumer differs.

So the problem is not "how does strata compute DORA metrics". It is: **strata observes a great deal about each deployment and currently throws almost all of it away.** What survives is scattered across `deployment-manifest.json`, the deploy-log, and `cost.json`, in shapes built for audit and reproducibility rather than for measurement.

## Decision Drivers

- **The data already exists** — `DeployLogModel`, `ManifestArtifactsModel`, redacted terraform plan JSON, cost and drift artifacts all exist; nothing new needs to be *observed*, only recorded in an aggregatable shape
- **Consumers differ, and we don't own them** — Grafana, Splunk, Datadog, and a future `strata` command all want different windows, groupings, and filters
- **Pipelines are ephemeral** — whatever we emit must be produced without reading prior state; a CI runner has no history
- **Precedent** — cost (ADR-0031) and audit (ADR-0018) already follow "write an artifact next to the build, optionally forward it outward"; this reuses that shape rather than inventing one

## The self-containment invariant

The central rule this ADR establishes, from which most of the design follows:

> **A deployment record may contain only facts observable from inside the deploying process.**

Anything requiring knowledge of *other* deployments belongs to the aggregation layer, not the record.

This is not a stylistic preference — it is forced by the execution environment. A CI runner starts from a fresh checkout with an empty `.strata/`. ADR-0018 already notes that the local deploy-log is readable "only on the machine (or checked-out repo) that produced the entry." Any field needing history therefore either silently returns null (making every pipeline deploy look like the first deploy ever) or forces a network round-trip onto the deploy hot path, and becomes machine-dependent either way.

The invariant is what rules out aggregate-at-deploy-time (Option A below), and it also rules out individually plausible-looking fields such as `time_since_previous_deploy_seconds`.

## Considered Options

**Option A — compute aggregate metrics during each deployment**
Each `deploy run` scans deployment history, computes frequency / failure rate / MTTR over a fixed window, and writes the result as a build artifact.

Rejected. Four independent defects:
- Aggregation windows and groupings are consumer decisions; baking one window into an artifact freezes a choice that isn't ours
- The value is stale the moment it is written, while looking authoritative inside an immutable artifact
- It puts an O(N)-and-growing history scan on the deploy hot path
- Two of the four DORA metrics are *uncomputable* at deploy time: MTTR needs a future recovery event, change failure rate needs a denominator that does not yet exist

**Option B — emit a self-contained fact record per deployment; aggregate downstream** *(chosen)*
Each deployment writes one record describing only itself. Consumers aggregate.

**Option C — forward to external platforms only**
No local artifact; push events to Datadog/Splunk and let them do everything.
Rejected as the *sole* mechanism — it makes measurement conditional on owning an observability platform, and leaves nothing in the build output. Retained as a delivery channel within Option B.

**Option D — centralised strata metrics service**
A backend ingesting records from many workspaces for fleet-wide dashboards.
Deferred, not rejected — it is a consumer of Option B's records, so it can be added later without redesign.

## Decision Outcome

Chosen: **Option B — emit facts, aggregate downstream.**

The deployment emits measurements about itself and nothing else. Every aggregate — including all four DORA metrics — is derived by a consumer that holds many records.

This inverts the phasing the DORA framing suggested. **Lead time moves into Phase A**, because it is the one DORA metric that is genuinely a per-change measurement: `deploy completed_at − commit authored_at`, both observable locally (git history is present in the checkout — the pipeline cloned it). `_write_deploy_log()` already collects `commit_sha` and `commit_author`; the authored timestamp is one `git log --format=%at` away. The other three are inherently aggregate and move out of strata's per-deployment scope entirely.

| DORA metric           | Nature                  | Produced by                                                   |
| --------------------- | ----------------------- | ------------------------------------------------------------- |
| Lead time for changes | per-change measure      | **Phase A** — emitted directly                                |
| Deployment frequency  | count over a window     | consumer / Phase B — from record timestamps                   |
| Change failure rate   | ratio over a window     | consumer — join against an incident source, **not** `outcome` |
| Mean time to recovery | interval between events | consumer — incident source plus recovery linkage              |

### Phase A — the record ✅ proposed

Write `deployment-metrics.json` into the build output beside `deployment-manifest.json`, `deployment-outputs.json`, and `cost.json`, then hand the same payload to the existing `AuditController.forward_to_siem()` — the sinks, config, and best-effort semantics already exist (ADR-0022), so there is nothing new to build on the transport side.

That includes OpenTelemetry: `OtelSiemIntegration` (`type: otel`) already ships records as OTel **Log Records** over OTLP/HTTP JSON to `POST /v1/logs`, reaching Grafana, Datadog, Elastic, Splunk, and anything else behind an OTel Collector. No new export format is required for Phase A.

That the existing integration models this as a *log record* rather than a metric sample is the correct framing, not an accident of implementation. A deployment is a discrete event with attributes; Prometheus-style metrics describe continuously sampled series. `strata_deployment_duration_seconds` as a gauge would be a category error — it is not a quantity that exists between deployments. Counters and rates are derived from the event stream downstream, which is exactly the Phase B split.

Also append each record to a durable local series at `.strata/metrics/deployments.ndjson`. Build directories are cleaned; without an append-only series, Phase B would have no offline corpus to read and every consumer would be forced through an external stack.

#### Scope — `deploy` only, never `build`, never dry-run

Only `deploy run` and `deploy destroy` emit a record.

`build` does not, because a build is not a one-off step immediately preceding a deploy — it is a development-loop action, run repeatedly while iterating on configuration and while testing. Builds therefore outnumber deploys by a wide margin. Any consumer counting records naively would get a meaningless number, and every consumer would have to remember to filter on `action` — a burden that someone eventually forgets. On ingest-billed platforms it also means paying to ship dev-loop noise that carries no delivery signal.

The structural reason matters more than the volume one: a build changes nothing outside its own output directory. There are no gates, no approvals, no lock, no resources touched, no environment affected — most of the record would be absent by construction. This record exists to document a change to real infrastructure, and a build has not made one.

Dry-runs are excluded for the same reason, which also matches existing behaviour: `_finalize()` skips the deploy-log when `_dry_run` is set, and `_write_deployment_manifest()` returns early. A `dry_run` dimension would consequently be constant `false`, so it is omitted from the record entirely.

Build and developer-experience telemetry (build duration trends, build failure rate) is a legitimate concern, but a separate one — different consumers, different retention, and a local-versus-CI representativeness problem, since builds run on laptops too. If it is ever wanted it belongs in its own series, not blended into this one.

The record splits into **dimensions** (what you slice by) and **measures** (what you aggregate). Dimensions are cheap and multiply the value of every measure — `tenant` + `ring` + `wave` + `environment` turn one duration number into fleet analysis.

#### Measures worth emitting, by KPI family

| Family             | Fields                                                                                                   | Availability today                                                                                              |
| ------------------ | -------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Delivery (DORA)    | `outcome`, `duration_seconds`, `lead_time_seconds`, `action`                                             | ✅ present in `DeployLogModel`                                                                                   |
| Change size        | `resources_created` / `_updated` / `_deleted` / `_replaced`, `modules_touched`                           | ✅ count actions in the already-redacted `resource_changes`                                                      |
| Time decomposition | `lock_wait_seconds`, `gate_wait_seconds`, `plan_seconds`, `apply_seconds`, per-stage durations           | ⚠️ stage/step durations ✅; lock wait needs an attempt-start stamp; gate wait needs plumbing into the deploy path |
| Reliability        | `stages_total`, `stages_failed`, `failed_stage`, `failed_step`, `error_category`, `timed_out`, `resumed` | ✅ mostly; `error_category` is new                                                                               |
| Governance         | `gates_evaluated`, `gates_by_type`, `approver_count`, `policy_checks`, `policy_violations`, `force_used` | ✅ gate and policy models exist                                                                                  |
| FinOps             | `monthly_cost_total`, `monthly_cost_delta`, `currency`                                                   | ⚠️ `cost.json` exists but is only produced on `--dry-run` (ADR-0031)                                             |
| Supply chain       | `cve_critical/high/medium/low`, `sbom_component_count`, `images_deployed`                                | ⚠️ only when an audit/SBOM ran                                                                                   |
| Drift              | `drift_detected`, `drift_resources`, `drift_acknowledged`                                                | ✅ ADR-0008                                                                                                      |

Two of these carry disproportionate value:

- **Change size.** DORA's own research ties small batches to high performance, yet batch size is rarely measured because extracting it is painful. `terraform_deployer.save_plan_json()` already produces redacted `resource_changes`, so counting actions is nearly free. `resources_deleted` alone is a risk signal worth alerting on.
- **Time decomposition.** "Deploys take 40 minutes" is unactionable; "34 of those 40 are gate wait" is a decision. Separating wait time from work time is likely the most useful output here for any non-DORA purpose.

#### Record shape

```json
{
  "apiVersion": "strata.huybrechts.xyz/v1",
  "kind": "deployment-metrics",
  "meta": {
    "execution_id": "01J8ZQ4M7X",
    "deployment": "haven",
    "workspace": "haven-prd",
    "environment": "production",
    "tenant": "acme",
    "version": "1.6.1",
    "strata_version": "1.6.1",
    "recorded_at": "2026-08-06T14:32:11Z"
  },
  "dimensions": {
    "action": "deploy",
    "rollback_of": null,
    "outcome": "success",
    "ring": "prod",
    "wave": 2,
    "actor": "vhuybrec",
    "trigger": "ci",
    "commit_sha": "a1b2c3d",
    "commit_authored_at": "2026-08-05T09:14:00Z",
    "provisioners": ["terraform", "ansible"]
  },
  "measures": {
    "duration_seconds": 412,
    "lead_time_seconds": 105131,
    "lock_wait_seconds": 12,
    "gate_wait_seconds": 1840,
    "stages_total": 3,
    "stages_failed": 0,
    "resources_created": 4,
    "resources_updated": 11,
    "resources_deleted": 0,
    "resources_replaced": 1,
    "policy_checks": 7,
    "policy_violations": 0,
    "gates_evaluated": 2,
    "approver_count": 2,
    "force_used": false,
    "error_category": null
  },
  "sections": {
    "cost": { "measured": false, "reason": "cost estimation not run for this deploy" },
    "supply_chain": { "measured": true, "cve_critical": 0, "cve_high": 2, "sbom_component_count": 431 },
    "drift": { "measured": true, "drift_detected": false, "drift_resources": 0 }
  },
  "stages": [
    { "name": "infrastructure", "provisioner": "terraform", "outcome": "success", "duration_seconds": 301 },
    { "name": "configuration", "provisioner": "ansible", "outcome": "success", "duration_seconds": 111 }
  ],
  "label_safe": ["environment", "outcome", "action", "ring", "tenant", "deployment"]
}
```

#### One record with explicit not-measured markers

Cost, CVE, and drift data are conditional — they may simply not have been produced for a given deploy. A plain `null` cannot distinguish *not measured* from *measured as zero*, and that ambiguity silently corrupts any average built on top of it. Rather than splitting the record into optional documents, conditional groups live under `sections` with an explicit `measured` flag. One schema, no ambiguity.

#### Cardinality discipline

When these records reach a dimensional system, dimensions become labels — and `execution_id`, `commit_sha`, and `actor` are unbounded. As Prometheus labels they cause a cardinality explosion. They are legitimate *record attributes* (fine for SIEM, event stores, and logs) but must never be promoted to metric labels. The `label_safe` array names the bounded subset explicitly so consumers do not have to guess.

Note that `OtelSiemIntegration` currently serialises the whole payload into the log record's `body.stringValue`, so fields are reachable only by backends that parse the JSON body (Loki, Datadog, and Elastic all do). Promoting the `label_safe` subset into OTel log-record *attributes* would make those dimensions filterable without body parsing — a small, optional refinement to an existing integration rather than a new format.

#### Secrets

Emit counts and key names, never values. Deployment outputs already track `sensitive_keys` precisely because they carry secrets. The less obvious vector is error text: a failing terraform run will happily print connection strings. Therefore raw `errors[]` is **not** emitted — instead `error_category`, a bounded enum (`auth`, `quota`, `timeout`, `conflict`, `policy`, `network`, `state_lock`, `unknown`). This also makes failures aggregatable, which free-text error strings never are. Anything plan-derived reuses the existing `_redact_sensitive_changes()`.

#### Deliberately excluded

- `time_since_previous_deploy_seconds` — violates the self-containment invariant; consumers derive gaps from `recorded_at` across records trivially
- Raw `errors[]` and `messages[]` — leak risk and unaggregatable
- Output *values* — counts and key names only
- Inline SBOM — counts plus a reference to `sbom.json` by digest

### Phase B — local aggregation ⏳ deferred

`strata metrics dora` (and a more general `strata metrics show`) reads the NDJSON series over a window and aggregates. This becomes a pure function over facts Phase A already wrote — no new collection, no new plumbing.

Deferred rather than dropped, because teams already running Grafana, Splunk, or Datadog can aggregate in their own stack and never need it. Phase A is what unblocks them; Phase B only serves the offline/no-platform case. Its cost is low once Phase A exists, so it should wait for a real request.

### Phase C — cross-workspace aggregation ⏳ future

A backend ingesting records from many workspaces for fleet-wide dashboards, time-series retention, and an API. Purely a consumer of Phase A records, so it can be built whenever enterprise demand justifies the operational cost — no redesign required.

### Recovery correlation and change failure rate

Two of the four DORA metrics — change failure rate and MTTR — need something the record cannot supply on its own. This section states what strata can and cannot know, and where the remaining truth has to come from.

#### `outcome` is not change failure rate

DORA defines change failure rate as the share of deployments that *cause a degradation in service requiring remediation*. `outcome` answers a different question, and the two diverge in both directions:

- A deploy that dies during `terraform apply` before anything reaches production is an **execution failure but not a change failure** — service was never degraded.
- A deploy that completes green while its new configuration takes the service down **is a change failure**, and strata records it as `outcome: success`.

So `outcome` yields a *deploy execution failure rate*. That is a genuinely useful reliability metric and is worth publishing — but it must be labelled as what it is. Presenting it as change failure rate produces a number that everyone misreads in the same direction.

#### Three separate questions

| Question                             | Can strata answer it?                                          |
| ------------------------------------ | -------------------------------------------------------------- |
| Which deployments failed to execute? | ✅ directly, via `outcome`                                      |
| Which changes degraded service?      | ❌ never — that is an incident signal from monitoring or humans |
| Which deployment restored service?   | ⚠️ only when told, or when it carries rollback intent           |

#### What Phase A does

**Surface reversal intent that already exists.** `PromotionRecordModel.rollback_of` — *"Name of the promotion-record this reverses (rollbacks only)"* — is populated by `strata promote rollback` from command intent. That is precisely the linkage an explicit remediation flag would add, already built and already invariant-safe, because it is supplied at invocation rather than derived from history. Where a deployment carries it, the record surfaces it as a `rollback_of` dimension; otherwise `null`.

**Leave change failure rate as a downstream join.** It is fundamentally an incident metric, and the truth lives in PagerDuty, ServiceNow, or Jira — never in strata. The intended architecture is that strata emits deployment events, the incident system emits incident events, and the consumer joins them on time and environment. This is how Google's Four Keys works, and it is why the join belongs downstream rather than inside the record.

#### Rejected for now — an explicit `--remediates` flag

`strata deploy run --remediates <execution_id>` would be unambiguous, and would capture fix-forward, where the remediating deploy is itself `outcome: success`. It is not adopted because it demands discipline exactly when discipline is scarcest — under incident pressure, with the prior `execution_id` needing to be looked up first.

The predictable result is data that is sparse *and* biased: you cannot distinguish "we had two incidents" from "we had two incidents calm enough for someone to annotate." Sparse-and-biased is worse than absent, because it still looks like data. Revisit only on a concrete request from a team willing to use it consistently.

#### Consumer-side inference — usable, with a caveat attached

Pairing a `failed` deployment with the next `success` for the same deployment and environment is what most DORA tooling does, needs nothing from Phase A, and works retroactively over records already emitted.

But it inflates MTTR through false pairing — a failed deploy on Monday followed by an unrelated feature deploy on Thursday reads as a three-day recovery — and it is entirely blind to degradation caused by deploys that succeeded. It is a reasonable default for teams with no incident source, provided the caveat travels with the number.

#### `is_rollback`

The same rule applies: acceptable only as command intent (the operator invoked a rollback, or promotion resolution says so), never inferred by comparing against currently-deployed state. With `rollback_of` surfaced, a separate boolean is redundant — its presence is the signal.

## Implementation sketch (Phase A)

```
src/strata/
├── models/
│   └── deployment_metrics_model.py   # Pydantic model for the record
└── services/
    └── deployment_metrics_service.py # Assemble record from runtime state; write artifact + NDJSON
```

Wiring follows the path `deployment-outputs.json` already takes:

- `BaseDeployCommand._write_deployment_metrics()` — assemble and persist, best-effort, mirroring `_write_combined_outputs_artifact()`
- Called from `RunDeployCommand._execute()` alongside the manifest and outputs writes
- Forwarded via the existing `AuditController.forward_to_siem()` call already made in `_write_deploy_log()`

Sources for the data, all already present:

| Field group            | Source                                                               |
| ---------------------- | -------------------------------------------------------------------- |
| identity, git, timing  | `DeployLogModel` assembly in `_write_deploy_log()`                   |
| stage outcomes         | `self._stage_results` (`ManifestStageModel`)                         |
| resource change counts | `terraform_deployer.save_plan_json()` → redacted `resource_changes`  |
| policy results         | `self._policy_results` (`ManifestPolicyResultModel`)                 |
| gates                  | `gate_model.DeploymentGateModel`                                     |
| lock                   | `ManifestLockModel.acquired_at` (+ new attempt-start stamp for wait) |
| cost / drift / SBOM    | `cost.json`, drift history, `sbom.json` when present                 |

Failures to write metrics must never affect the deployment exit code — same best-effort contract as the deploy-log (ADR-0018, decision #2).

## Consequences

### Good

- **Deterministic and machine-independent** — no reads of prior state, no network on the deploy path; the same deployment produces the same record anywhere
- **Serves many KPIs, not just DORA** — FinOps, supply chain, governance, reliability, and fleet reporting all read the same record
- **No platform lock-in** — teams with Grafana/Splunk/Datadog aggregate there and never need Phase B; teams without one wait for Phase B
- **Cheap** — nothing new is observed; the data already exists and is merely recorded in an aggregatable shape
- **Reuses existing transport** — SIEM sinks, redaction, and best-effort semantics all come from ADR-0018/0022

### Neutral

- **No aggregates in Phase A** — a single record answers nothing on its own; value appears once many records are collected
- **Deployment-level only** — says nothing about application SLIs (latency, error rate); complements rather than replaces application monitoring
- **Series retention** — Phase B's usefulness depends on `.strata/metrics/deployments.ndjson` being retained and, in ephemeral CI, shipped somewhere durable

### Risk

- **Schema churn** — new KPI questions will want new fields
  - Mitigation: additive-only changes; the `sections` mechanism absorbs conditional groups without restructuring
- **Cardinality misuse** — a consumer promotes `commit_sha` to a Prometheus label and detonates their TSDB
  - Mitigation: `label_safe` ships in the record; document the hazard where the artifact is described
- **Secret leakage through new fields** — the pressure to include error detail will recur
  - Mitigation: `error_category` enum is the sanctioned channel; raw error text stays in the deploy-log, which already has an access-controlled path
- **Cost/CVE data frequently absent** — `cost.json` today is produced only on `--dry-run`
  - Mitigation: `sections[].measured` makes absence explicit rather than indistinguishable from zero

## Open questions

1. How a deployment learns that it is executing a rollback. `promote rollback` records `rollback_of` and produces a branch or PR, but the subsequent `deploy run` is a separate invocation, so the linkage is not automatic today. Options: propagate it through the promotion record that the deploy already resolves, or accept it as an explicit flag.
   - **Note (2026-09-08, updated):** [ADR-0074](0074-deployment-change-reference.md) (deployment change reference) had the mirror-image question — whether a rollback deploy should require its own `change_reference`, or inherit the original deployment's — and has since resolved it (its Open Question 3): a rollback always supplies its own `change_reference` explicitly at invocation, with no automatic inheritance, independent of however this question (`rollback_of` propagation) gets answered. So this remains solely this ADR's open question now; ADR-0074 does not block on it and there is no propagate-together requirement after all — the two fields are populated through entirely separate paths (`rollback_of` from promotion-resolution state, `change_reference` from `--change-id`/`--reason` at invocation), so whichever mechanism this question settles on has no bearing on `change_reference`.
2. Whether to promote the `label_safe` dimensions into OTel log-record attributes instead of leaving them inside the JSON body. (The broader export-format question is settled: OTLP is already supported via `OtelSiemIntegration`; a Prometheus exposition format is explicitly *not* pursued — it is pull-based, so an exiting CLI has nothing to scrape, and the Pushgateway workaround retains stale values indefinitely and drops timestamps.)
3. Retention and rotation policy for `.strata/metrics/deployments.ndjson`
