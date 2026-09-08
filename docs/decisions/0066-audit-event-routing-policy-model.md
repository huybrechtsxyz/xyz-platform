# Audit event model — three classes, one policy, one configuration location

- Status: completed
- Date: 2026-08-06
- Related: ADR-0018 (deployment audit & traceability), ADR-0022 (SIEM integration), ADR-0064 (deployment metrics record), ADR-0065 (strata state service), ADR-0005 (secret resolution at build time), ADR-0004 (exit code convention)
- Out of scope: server-side user identity/AuthN/AuthZ (OIDC/OAuth2 login, session and token issuance, authorization model) is covered by [ADR-0067](0067-server-identity-authentication-authorization.md), needed before ADR-0065 Phase 3 (control plane) can proceed — see "Out of scope, on purpose" under "What `actor` resolves from" below

## Context and Problem Statement

Strata has three audit subsystems. None of them knows the other two exist.

| Subsystem                                 | Records                      | Configured by                                    | Reaches sinks              |
| ----------------------------------------- | ---------------------------- | ------------------------------------------------ | -------------------------- |
| `logger/audit.py` → `.strata/audit.log`   | every CLI command invocation | `logging.yaml` → `audit:`, or hardcoded defaults | ❌                          |
| `AuditController` → `.strata/deploy-log/` | deployment outcomes          | `spec.audit`                                     | ✅ — `deploy_audit` only    |
| `AuditPolicyModel.events`                 | *declares* 8 event types     | `spec.audit.policy`                              | ❌ — never read by anything |

The third row is the sharpest statement of the problem: strata ships a model that enumerates what it can audit, and nothing consumes it.

### What the live audit log actually contains

Measured on this repository's `.strata/audit.log` — 4,967,022 bytes, **18,853 entries**, 2 Jul → 7 Aug 2026. That is 99.4% of the 5 MB rotation threshold.

| Count | Action                    | Share | What it actually is                                                                                                |
| ----: | ------------------------- | ----: | ------------------------------------------------------------------------------------------------------------------ |
| 9,718 | `command.workitem_list`   | 51.5% | VS Code extension `setInterval` poll (`workItemsViewProvider.ts`). **2 distinct targets** across all 9,718 entries |
| 3,722 | `command.secret_generate` | 19.7% | The test suite — `target` is pytest's argv                                                                         |
| 2,326 | `command.secret_mask`     | 12.3% | The test suite                                                                                                     |
| 2,143 | `command.schema_get`      | 11.4% | Editor schema resolution                                                                                           |
|   333 | `command.schema_list`     |  1.8% | Editor                                                                                                             |
|   233 | `command.tools_status`    |  1.2% |                                                                                                                    |

The top four are **95%** of the file. Human-intent actions, in their entirety: 34 `validate`, 5 `new`, 5 `cache_warm`, 5 `env_info`, 3 `env_doctor`, 2 `console`, 1 `workitem_approve`, 1 `policy_check`, 1 `solution_profile_add`.

**Zero `command.deploy_run`. Zero `command.build_run`.** Outcomes: 18,837 success, 16 failure.

An audit trail that is 95% polling and test noise, and contains no deployments, is not an audit trail. But the conclusion is *not* that command auditing is worthless — see problem 5.

### Three classes, not one flat list

`AuditPolicyModel` models its eight event types as a flat `Dict[str, bool]`. They are not homogeneous. They fall into three classes with different volumes, different value, different retention, and different natural destinations:

| Class            | Answers                       | Event types (see naming decision below)                                                  | Volume           | Natural home                          |
| ---------------- | ----------------------------- | ---------------------------------------------------------------------------------------- | ---------------- | ------------------------------------- |
| **Invocation**   | *who* ran what, when          | `command.executed`                                                                       | high             | journal — local, rotating, disposable |
| **Outcome**      | *what did that run do*        | `deployment.completed`, `build.completed`, `validation.completed`, `deployment.measured` | one per run      | deploy-log + sinks — archived         |
| **Domain event** | *what happened to the system* | `policy.violated`, `secret.accessed`, `lock.acquired`/`lock.released`, `drift.detected`  | rare, high value | sinks — long retention                |

Two things follow that a flat list hides. **Domain events are not always tied to an invocation** — scheduled drift detection, a lock expiring, a gate approved out-of-band have no `command.*` parent, so they cannot be modelled as "the outcome of a command". And **`deployment.completed` is genuinely both** an outcome and a state-change record, which makes it the natural join point between the classes.

The problems below are behavioural, and there are eleven of them.

**1. `policy.events` is dead configuration.** `AuditPolicyModel` defaults eight event types — `deploy_audit`, `cli_action`, `policy_violation`, `secret_access`, `lock_event`, `validation_result`, `drift_alert`, `build_event`. Nothing under `src/` ever reads the field. An operator who sets `secret_access: true` gets silence, and an operator who sets `deploy_audit: false` still gets deploy audit events. Configuration that looks authoritative and does nothing is worse than no configuration at all, because it manufactures false confidence in exactly the subsystem whose job is to be trustworthy.

**2. Only one event type can ever reach a sink.** `AuditController.forward_to_siem()` takes a `DeployLogModel` and hardcodes the string literal `"deploy_audit"` — both for the per-sink filter test and for the `send_event()` call. Seven of the eight declared event types have no producer wired to the sink path. The signature is shaped around one payload type rather than around events.

**3. The invocation producer ignores its own stated criterion.** `logger/audit.py`'s module docstring defines auditable actions as "user actions with observable side-effects". `BaseCommand._after_execute` then emits `command.{OPERATION}` for **every** command, including `workitem list`, `schema get`, and `tools status` — none of which have any side effect. Applying the documented rule would remove ~95% of the volume without losing a single meaningful entry.

**4. The test suite writes to the real audit log.** `tests/**/conftest.py` contains no reference to `audit`, `configure_audit_log`, or `shutdown_audit`. Tests run in-process, `BaseCommand` configures the journal, and `redact_argv(sys.argv[1:])` captures *pytest's* command line. Roughly 6,000 entries — a third of the file — are test artifacts with `target: "-q --cov=src/strata --cov-report=term-missing"`. `audit.py`'s docstring claims the opposite: "Silent no-op when the audit logger has not been configured (e.g. in tests…)".

**5. There is no actor anywhere in the outcome record.** `DeployLogModel` has no `user` or `actor` field. It has `commit_author` — the git HEAD author, i.e. who *wrote the code*, not who ran the deploy. Those differ constantly: CI runs, someone else's PR, a rollback executed by on-call. So "who deployed to production last Tuesday" is answerable **only** from the invocation stream, which is precisely the stream currently drowned in polling noise. A direct consequence: `AuditController._format_cef()` reads `data.get("user", "")` from a `DeployLogModel` dump, so **every CEF event sent to a SOC carries an empty `src=`**.

**6. The correlation key exists and is never used.** `BaseCommand` emits `audit(..., detail={"execution_id": self._execution_id})` and `RunDeployCommand` builds `DeployLogModel(execution_id=self._execution_id)` — the same value, in the same command instance. Invocation and outcome are already joinable and nothing joins them.

**7. `strata audit resend` silently drops integration-backed sinks.** `ResendAuditCommand` guards on `audit_config.sinks` being non-empty, then constructs `AuditController(work_path=...)` with **no** `siem_sinks` argument. A configuration whose sinks are all integration-backed passes the guard, forwards nothing, and reports `sent=N, failed=0`. The one command whose entire purpose is recovering from a delivery outage is the one that cannot deliver to the destinations most likely to have had one.

**8. Sink resolution and filtering are implemented four times, with divergent semantics.** Built-in sinks are filtered inside the controller; integration sinks are resolved and filtered in `RunDeployCommand._resolve_siem_sinks()`; work-item events reuse that resolver but send under a different event name; and `strata audit export --siem <name>` bypasses `spec.audit.sinks` altogether, scanning `.strata/*.yaml` for an integration by name. The semantics have already drifted: `_forward_workitem_event()` documents that "sinks filtered to `["deploy_audit"]` also receive workitem events". A filter that admits events it does not name is not a filter.

**9. Secret-bearing sink fields are passed to the wire verbatim.** `_send_webhook()` seeds `{"Content-Type": "application/json"}`, does `req_headers.update(headers)`, and hands the result to `urllib`. Nothing touches the values in between — no env expansion, no template render, no secret resolution. Whatever is typed in YAML is what goes on the wire, so an operator has no option today but to commit the raw token.

This is not an isolated slip. Strata currently runs **three** unreconciled credential conventions, and the audit sink belongs to a fourth category — no convention at all:

| Convention              | Syntax in YAML                 | Resolver                                               | Where it works                                                                                                                                  |
| ----------------------- | ------------------------------ | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| A — env-var *name*      | `api_key: BWS_ACCESS_TOKEN`    | `_get_env_var()` / `os.getenv()`                       | bitwarden, vault, consul, infisical, etcd, azure keyvault/appconfig, flagsmith                                                                  |
| B — Jinja env reference | `{{ env.VAULT_ADDR }}`         | `BaseIntegration._resolve_env_vars()`                  | **only** `endpoints.address`, and only in the 7 integrations that call it                                                                       |
| C — value expression    | `${secret:tf_token}`           | `resolve_expr_string()` (shared, `resolved_values.py`) | `workspace.spec.provisioners[].backend.configuration` and Helm module values ([ADR-0075](0075-unify-terraform-helm-value-expression-syntax.md)) |
| — none                  | `Authorization: Bearer abc123` | verbatim                                               | `AuditSinkModel.headers`                                                                                                                        |

Convention A is the documented contract: `auth_models.py` opens with "All fields are key references resolved at runtime", and every field description reads "Key reference for …". Convention C is the only one that reaches the **secret store** (via `ResolvedValues`) rather than the process environment.

Three call sites then violate the contract they document:

- `SiemBaseIntegration._build_auth_headers()` carries the docstring "All fields … are *env-var name references* … Each field must be resolved via `_get_env_var` before use", and its very next branch does `key = auth.api_key.api_key` with the comment "use values directly". The `oauth2` branch honours the contract; the `api_key` branch — the one every SIEM integration actually uses — does not.
- `SplunkSiemIntegration`'s module docstring advertises `authentication.api_key.api_key: <HEC token> (supports {{ env.SPLUNK_HEC_TOKEN }})`. `_get_hec_token()` returns the raw field and never calls `_resolve_env_vars`. That documented feature does not exist.
- `IntegrationEndpointsSpecModel.address` says "supports env var substitution: `${VAR_NAME:default}`", but the implementation is convention B (`{{ env.VAR }}`) — and the SIEM integrations read `endpoints.address` directly in `_build_url()` without calling the resolver, so for SIEM *neither* syntax works.

So the audit `webhook` sink is the sharpest case, not a special one. Every SIEM integration — the other half of this very subsystem — takes its token literally from YAML too. ADR-0065 makes it urgent rather than theoretical: its recommended configuration puts an ingest token in `headers` and writes it as `"Bearer ${STRATA_INGEST_TOKEN}"`, which today would transmit those characters literally, earn a 401, and have the resulting failure swallowed at `logger.debug`.

**10. The transports are weak for an audit channel.** `syslog` is UDP-only with no TCP or TLS option, so records travel in cleartext and delivery is unacknowledged by construction; oversized payloads are silently truncated at 65000 bytes. `url` is not scheme-validated, so `http://` is accepted. For a channel whose value proposition is tamper-evidence, unauthenticated cleartext fire-and-forget is the wrong default.

**11. Configuration lives in three places, and the documentation points at the wrong one.** The journal is configured by `logging.yaml` → `audit:` (commented out in both the shipped template and every real workspace, so the effective source is `configure_audit_log()`'s function defaults). Policy and sinks are in `spec.audit`. Environment-level `spec.audit` is merged by `EnvironmentService` into a field nothing reads. Meanwhile `help/audit.md` is headed "Configured Under `spec.audit`" and never mentions `logging.yaml`. An operator asking "is my audit trail working?" has no single place to look and no command to ask.

Also: failures are invisible — every sink error is swallowed at `logger.debug`, so a completely broken pipeline is indistinguishable from a working one at default verbosity — and the `stdout` sink writes raw JSON straight to `sys.stdout`, breaking the `{success, data, errors, messages}` envelope under `--output json` (ADR-0004).

### Why this needs deciding now

Two in-flight ADRs both route through this code. ADR-0064 hands its deployment metrics record to `forward_to_siem()`, and ADR-0065 makes the `webhook` sink the primary transport to a first-party ingest service — including a token in `headers` from problem 9, and it already calls out the `debug`-swallowing as a required fix. Both inherit every defect above. Fixing this once, before two new producers are attached, is cheaper than fixing it three times afterwards.

The volume evidence sharpens the urgency: if `cli_action` were wired to sinks today, **95% of SIEM ingest would be VS Code polling and pytest runs**. On an ingest-billed platform that is a real invoice for zero signal.

## Decision Drivers

- **Audit configuration must be honest** — a declared knob either works or does not exist; there is no acceptable third state for the subsystem that exists to be trusted
- **One place to look** — an operator configuring audit should not need to know which of three files owns which half of the answer
- **One routing decision, one implementation** — "does this event go to this sink" must be computed in exactly one place
- **Volume discipline by default** — the default configuration must not ship machine chatter to a billed destination
- **Every record must carry an actor and a correlation key** — an audit trail that cannot answer "who" is not one
- **Best-effort delivery, but never silent** — audit failures must not fail a deploy (ADR-0018) and must not be invisible either
- **Credentials belong in the secret store** — no exceptions for audit sinks, and no fourth syntax for saying so (ADR-0005)
- **Audit configuration is not the audited party's to change** — whoever edits a deployment must not be able to edit what that deployment records about them
- **The journal must survive broken configuration** — if platform config fails to load, that is exactly the moment an audit entry matters most

## Considered Options

**Option A — delete `policy.events`**

Honest shrink: remove the dead model, document per-sink `events` as the only filter, and be done.

Rejected, though it is not unreasonable. It resolves problem 1 by amputation and does nothing for the other ten. It also removes the only global off switch: with per-sink filters alone, disabling an event class across a fleet means editing every sink in every workspace. The event-type list is itself the documentation of what strata can audit, and that list survives only if something consumes it.

**Option B — make the policy model authoritative behind a single routing function, with three event classes and one configuration location** *(chosen)*

`policy.events` becomes the global gate with class-aware defaults, `sink.events` becomes a per-destination subset, both are evaluated in exactly one function that all producers call, and `spec.audit` becomes the single place all three subsystems are configured from.

**Option C — expression-based policy (OPA/CEL-style predicates per sink)**

Replace boolean maps with predicates: `event.type == "deploy_audit" && event.environment == "production"`.

Rejected for now, as scope rather than as direction. Strata already has a policy engine (ADR-0006) and a Checkov/OPA integration path (ADR-0050/0051), so if event routing ever needs real expressions it should reuse that machinery rather than grow a second dialect inside `AuditSinkModel`. Nothing in the observed use cases needs predicates; they need the boolean map that already exists to actually be read. Adding an expression language on top of a filter that does not work would be building the second storey first.

**Option D — route everything, filter at the destination**

Send all events to all sinks and let Splunk/ELK/the ingest service filter.

Rejected, and the measured data is the argument: 95% of the invocation stream is polling and test noise. Shipping it to be filtered downstream maximises egress cost on ingest-billed platforms, sends `secret_access` events to destinations an operator may deliberately have excluded, and gives up the local control `policy.events` was introduced to provide. Filtering is cheapest closest to the source.

**Option E — keep the journal in `logging.yaml`, policy and sinks in `spec.audit`**

The status quo split, made explicit and documented rather than unified.

Rejected. It is defensible on layering grounds — file paths and rotation are operational, event policy is governance — but it fails the discoverability driver, and the evidence says discoverability is the binding constraint: the `audit:` block has been present and commented-out since it was written, in the shipped template *and* in every real workspace, which is what "nobody finds it" looks like in practice. The layering concern is real but is better solved by precedence (below) than by separation.

## Decision Outcome

Chosen: **Option B — three event classes, one policy, one configuration location.**

### This is a clean break

`spec.audit` is **redefined, not extended**. There is no compatibility shim, no auto-translation, no `strata audit migrate`, and no deprecation cycle. Every existing sink declaration, every `policy.events` key, and `forward_to_siem()` itself are replaced outright in one release.

The reasoning is that a shim buys very little here and costs a great deal. `extra="forbid"` means any change to these models is a hard break regardless, so a shim would not spare anyone a config edit — it would only delay it, while committing the codebase to carrying two shapes of sink, two sets of event names, and two credential conventions through every subsequent change. The audit subsystem is exactly where that ambiguity is most expensive.

**What is not waived is diagnostics.** Backwards compatibility means accepting old input; a good error means explaining the new input. The second is not the first, and it costs nothing. Old-shape configuration is detected and rejected at exit code 3 with the replacement spelled out verbatim:

```
spec.audit.sinks[0]: 'type: webhook' is no longer supported — sinks are now
references to spec.integrations[]. Replace with:

  integrations:
    - name: my-webhook
      type: webhook
      capabilities: [audit]
      endpoints:
        address: <the url that was here>

  audit:
    sinks:
      - name: my-webhook
        integration: my-webhook
```

The same applies to renamed `policy.events` keys: `deploy_audit` is not silently mapped, it is rejected with "use `deployment.completed`".

This decision is what makes the rest of the ADR simple. Several mechanisms below — the layered credential resolver, the `udp` syslog default, the `forward_to_siem` wrapper — existed only to avoid breaking something, and are dropped.

### `spec.audit` covers everything

All three subsystems are configured from one block. This is the change that makes the rest usable rather than merely correct.

```
spec.audit
├── policy      → which event types are active          (governance)
├── journal     → local record: path, rotation, retention (was logging.yaml → audit:)
├── sinks       → outward destinations                   (routing)
├── structure / deploy_log_path / repository             (existing, unchanged)
```

**The bootstrap problem is solved by precedence, not by separation.** The journal must work before configuration is loaded — `sln init` runs against an empty directory, `config set` has no platform config, and if configuration YAML is broken that is exactly when an audit entry matters most. So the journal opens in two phases:

1. **Phase 0 — bootstrap.** `BaseCommand` opens the journal with built-in defaults (`.strata/audit.log`, size rotation, 5 MB × 3) before any configuration is read. This is what happens today at `base_command.py:296`; it stays.
2. **Phase 1 — reconfigure.** Once `ConfigurationService` has loaded, if `spec.audit.journal` differs from the active configuration, the journal is reopened. `configure_audit_log()` already removes existing handlers before adding new ones, so reconfiguration is supported without change.

Entries written between the two phases land in the default file. That window is a few milliseconds of pre-config-load work, so this is a non-issue in practice — and the alternative (no journal until config loads) loses precisely the entries that matter when config is broken.

Precedence, matching strata's existing convention of *explicit → env → workspace file → built-in default*:

```
spec.audit.journal          (shared, committed — the normal place)
  ↑ overridden by
logging.yaml → audit:       (machine-local escape hatch: absolute paths on a
                             production host, per-developer overrides)
  ↑ falls back to
configure_audit_log()       (built-in defaults; bootstrap and broken-config paths)
```

`logging.yaml` survives as an override rather than the primary, which keeps the one legitimate use case — a production host needing an absolute path outside the workspace — without making it the place everyone has to discover first.

**`strata audit status`** closes the loop: one command printing the effective resolved picture — journal path, rotation, where that setting came from, which event types the gate admits, which sinks are live, and the last delivery outcome per sink. It is the "is my audit trail actually working?" command that does not exist today, and it is where silent sink failures become visible.

### The routing contract

A single function decides delivery, and it is the only place that decides:

```
AuditController.forward(event_type: str, payload: dict) -> None
```

Every admitted event is written to the **journal** first, then fanned out to **sinks**. Routing to a given sink is admitted when **all** of:

1. `policy.events` admits `event_type` — its resolved `enabled` flag is true (or, for an unlisted type, the class default). Unknown event types default to *not* audited, so a producer added in a later version cannot start emitting to production sinks without an explicit opt-in.
2. `sink.enabled` is true.
3. `sink.events` is `None` (meaning "everything the gate admits") **or** contains `event_type` exactly. The `_forward_workitem_event` leniency of problem 8 is removed — a named filter is exact.

The gate is evaluated *before* per-sink iteration, so turning an event class off stops egress everywhere in one edit.

#### Why two filters, and how they stay consistent

A reasonable objection: if each sink already declares the events it wants, why does a global gate exist at all?

Because **the journal is not a sink**. `forward()` writes to the journal before any sink is consulted, and the journal has no `events` list — so something must decide whether an event is recorded at all. Delete the gate and there are three consequences:

1. The journal takes everything, which is exactly the 18,853-entry state measured above.
2. A workspace with **zero sinks** — the common case, since most users have no SIEM — has no filtering whatsoever.
3. Producer cost cannot be avoided. ADR-0064's metrics record reads plan JSON, `cost.json`, and SBOM data to assemble itself; a gate lets the producer skip that work. Sink-only filtering makes "does anything want this?" a derived union of every sink's list — computable, but implicit and easy to get wrong.

The two mechanisms answer different questions and neither subsumes the other:

|                  | Question                        | Scope                                      |
| ---------------- | ------------------------------- | ------------------------------------------ |
| `policy.events`  | is this audited *at all*        | journal, and a precondition for every sink |
| `sinks[].events` | does *this destination* want it | per-sink routing                           |

This is the same two-tier shape as stdlib logging, which strata's own `logging.yaml` already uses — `loggers.strata.level` gates, `handlers.console.level` filters per destination. Operators have already seen the pattern in that file.

The cost is a real footgun: an operator adds `lock.acquired` to a sink filter, nothing arrives, and the cause is a gate they forgot. That is made **unrepresentable** rather than merely diagnosable — a sink naming an event the gate has disabled is a validation error at exit code 3:

```
sink 'splunk' filters on 'lock.acquired', but spec.audit.policy.events.lock.acquired is false.
Either enable the event type or remove it from the sink filter.
```

The alternative — making the gate implicit, where a sink naming an event auto-enables it — is rejected: it removes the fleet-wide off switch and reopens the question of what the journal records.

Sinks are resolved *inside* the controller from `AuditConfigModel` plus the integration registry — one path, since every sink is now an integration reference. `_resolve_siem_sinks()` moves out of `RunDeployCommand`, which deletes the duplication of problem 8 and fixes problem 7 as a side effect rather than as a separate patch: `resend` gets its sinks because it no longer has to remember to pass them.

`forward_to_siem(payload, audit_config)` is deleted, not wrapped. Its four call sites move to `forward()` in the same change.

### Event types become a closed enum with class-aware defaults

Keys are validated against a declared set, so a typo (`policy.violations` for `policy.violated`) is a validation error at exit code 3 rather than a knob that silently never fires. Defaults are set per class, not uniformly:

| Class      | Event type             | Default     | Why                                                 |
| ---------- | ---------------------- | ----------- | --------------------------------------------------- |
| Invocation | `command.executed`     | **`false`** | 95% of measured volume is polling and test runs     |
| Outcome    | `deployment.completed` | `true`      | The primary record; already live                    |
| Outcome    | `deployment.measured`  | `true`      | ADR-0064                                            |
| Outcome    | `build.completed`      | `false`     | ADR-0064 excludes builds for the same volume reason |
| Outcome    | `validation.completed` | `false`     | High frequency, low forensic value                  |
| Domain     | `policy.violated`      | `true`      | Rare, high value; `event.kind: alert`               |
| Domain     | `secret.accessed`      | `true`      | Rare, high value                                    |
| Domain     | `lock.acquired`        | `false`     | Operational noise unless investigating contention   |
| Domain     | `lock.released`        | `false`     | Operational noise unless investigating contention   |
| Domain     | `drift.detected`       | `true`      | Rare, high value; `event.kind: alert`               |

`command.executed` defaulting off is the single most consequential line in this ADR. It is set by measurement, not taste.

#### Policy values are a `bool` or an object

`policy.events` maps each event type to `Union[bool, AuditEventPolicyModel]`. A bare bool is shorthand for `{enabled: <bool>}`, so the common case stays a one-liner and every example in this ADR remains valid exactly as written:

```python
class AuditEventPolicyModel(PlatformBaseModel):
    enabled: bool = True
    # reserved — none of these are read yet, and are rejected until a producer does:
    # severity: Optional[str] = None        # override event.kind / SIEM alert routing
    # sample: Optional[int] = None          # audit 1 in N (high-volume classes)
    # retention_days: Optional[int] = None  # hint carried to sinks that honour it

# value accepted for each key in policy.events
AuditEventPolicy = Union[bool, AuditEventPolicyModel]
```

A Pydantic `field_validator(mode="before")` normalises the shorthand — `True` becomes `AuditEventPolicyModel(enabled=True)` — so every consumer reads one shape (`policy.events[event_type].enabled`) regardless of how it was written. The gate in `forward()` never branches on the value type.

This is deliberately more than the observed use cases need — a plain `Dict[str, bool]` carries every scenario measured today. It is bought because the clean-break constraint makes the map's *shape* expensive to revisit: once operators have written `policy.events`, widening a value from `bool` to an object is a hard break under `extra="forbid"`, whereas adding an optional field to `AuditEventPolicyModel` is not. The union is the one shape that keeps today's booleans working and leaves per-event severity, sampling, and retention addable without a second break. The reserved fields stay commented out — so `extra="forbid"` rejects them until a producer exists to read them — which keeps the surface honest rather than aspirational.

### The invocation producer is restricted to mutating commands

`command.executed` becomes useful only if the producer honours the criterion `logger/audit.py` already documents — "user actions with observable side-effects". `BaseCommand` therefore emits `command.executed` only for mutating operations. Read-only commands (`*_list`, `*_show`, `*_status`, `schema_*`, `tools_status`, `env_info`) do not produce audit entries at all.

This is what makes the class useful rather than merely quieter: with the poller and the test suite removed, what remains *is* the record of who did what.

The test-suite leak (problem 4) is closed in the same change — `configure_audit_log()` no-ops when `PYTEST_CURRENT_TEST` is set, which also makes `audit.py`'s docstring true.

### Every event carries an actor and a correlation key

- `actor` is added to `DeployLogModel` and to the journal entry envelope. `commit_author` stays as what it actually is — who wrote the code — and stops being mistaken for who ran the deploy. This also makes CEF `src=` non-empty for the first time (problem 5).
- `execution_id` becomes the **contractual** correlation key across journal, deploy-log, metrics record (ADR-0064), and every sink payload. It is already the same value on both sides (problem 6); this makes it documented and tested rather than incidental.

With both in place, the three classes compose: the invocation record supplies *who*, the outcome record supplies *what changed*, and they join on `execution_id`.

### The envelope and event names follow CloudEvents + ECS

Rather than invent an envelope and a naming scheme, both are taken from the two standards that already cover this exactly:

- **CloudEvents 1.0 (CNCF)** for the envelope and the `type` string. It is the standard for describing events across transports, its HTTP binding maps directly onto the `webhook` integration, and it is what any ingest service (ADR-0065) would expect to receive.
- **Elastic Common Schema (ECS)** for the fields inside `data`. Splunk, Elastic, and Sentinel already map ECS field names, so emitting them means field extraction is a no-op rather than a per-deployment project.

Neither is speculative for this codebase: strata already ships an `otel` SIEM integration, and OTel's own semantic conventions use the same lowercase dot-namespaced style.

#### Envelope — CloudEvents structured mode, JSON

```json
{
  "specversion": "1.0",
  "type": "xyz.huybrechts.strata.deployment.completed",
  "source": "/strata/acme-platform/haven-prd",
  "id": "01J8ZQ4M7XKQ2R3T4V5W6X7Y8Z",
  "time": "2026-08-07T09:12:58.041Z",
  "datacontenttype": "application/json",
  "subject": "haven",
  "data": {
    "event": {
      "kind": "event",
      "category": ["configuration"],
      "action": "deployment-completed",
      "outcome": "success",
      "duration": 412000000000
    },
    "user":   { "name": "vhuybrec" },
    "labels": {
      "execution_id": "63f43461-12cd-44c9-a902-77cade548ddd",
      "workspace": "haven-prd",
      "environment": "production",
      "deployment": "haven",
      "tenant": "acme"
    },
    "strata": { }
  }
}
```

CloudEvents supplies identity and timing (`id`, `time`, `source`, `subject`), so those are not duplicated inside `data`. ECS supplies `event.kind` / `event.category` / `event.action` / `event.outcome`, `user.name` for the actor, and `labels` for the correlation dimensions. Everything strata-specific lives under `data.strata` — an explicitly namespaced bag, which is what ECS prescribes for custom fields.

`event.kind` is doing real work here: SIEMs route `alert` differently from `event` and `metric`. Policy violations and drift become alerts *by schema*, without anyone writing a correlation rule.

#### Type names — reverse-DNS, dotted, past-tense verb last

CloudEvents says `type` SHOULD be reverse-DNS prefixed. The existing `apiVersion: strata.huybrechts.xyz/v1` gives the prefix `xyz.huybrechts.strata`.

```
xyz.huybrechts.strata.<domain>.<past-tense-verb>
```

Past tense is deliberate and is the convention across CloudEvents, ECS, and OTel: an audit record describes something that **has happened**, never an intent or a command.

Configuration uses the short form; the fully-qualified string appears only on the wire. That keeps `policy.events` readable without giving up the standard.

| Current              | Config key                        | CloudEvents `type` (wire)      | `event.kind` | `event.category` |
| -------------------- | --------------------------------- | ------------------------------ | ------------ | ---------------- |
| `cli_action`         | `command.executed`                | `…strata.command.executed`     | `event`      | `process`        |
| `deploy_audit`       | `deployment.completed`            | `…strata.deployment.completed` | `event`      | `configuration`  |
| `deployment_metrics` | `deployment.measured`             | `…strata.deployment.measured`  | `metric`     | —                |
| `build_event`        | `build.completed`                 | `…strata.build.completed`      | `event`      | `package`        |
| `validation_result`  | `validation.completed`            | `…strata.validation.completed` | `event`      | `configuration`  |
| `policy_violation`   | `policy.violated`                 | `…strata.policy.violated`      | **`alert`**  | `configuration`  |
| `secret_access`      | `secret.accessed`                 | `…strata.secret.accessed`      | `event`      | `iam`            |
| `drift_alert`        | `drift.detected`                  | `…strata.drift.detected`       | **`alert`**  | `configuration`  |
| `lock_event`         | `lock.acquired` / `lock.released` | `…strata.lock.acquired` …      | `event`      | `process`        |

Note the last row: past-tense naming forces `lock_event` to split into the states that actually occur. That is an improvement the old naming was hiding.

#### What this costs, and the ADR-0064 reconciliation

CloudEvents adds envelope keys that are terse and not self-explanatory (`specversion`, `datacontenttype`), and a SIEM that does not speak CloudEvents needs one field extraction to reach `data`. That is a real cost, accepted because the alternative is a bespoke envelope that needs a field extraction *and* has no specification behind it.

ADR-0064's metrics record becomes the `data.strata` payload of a `deployment.measured` event. Its `meta.recorded_at` and `meta.execution_id` are then duplicates of CloudEvents `time` and ECS `labels.execution_id`, so ADR-0064 should drop them from its own `meta` block rather than carry both. Its `dimensions` map onto `labels`, and its `label_safe` array remains useful and unchanged.

### Secret-bearing fields resolve through the store

Convention C — `${secret:KEY}` / `${var:KEY}` — becomes the standard for credential-bearing fields on the audit path, because it is the only one of the three existing conventions that reaches the secret store rather than the process environment. Requiring an operator to stage every SIEM token as an env var on every CI runner is precisely the coupling ADR-0005 exists to remove.

The resolver already exists but is private to the Terraform deployer:

```python
# TerraformDeployer._resolve_backend_expr — to be promoted to a shared util
re.sub(r"\$\{(var|secret):([^}]+)\}", replace, value)   # resolved against ResolvedValues
```

So this is a **promotion, not a new mechanism**: lift it into `strata/utils/`, then apply it to credential-bearing sink and integration fields.

Credential-bearing fields **require** a reference. A literal value in `authentication.*` or any field matching a credential-shaped name is a validation error at exit code 3, not a warning — there is no compatibility case to preserve, and a secret committed to YAML is the failure this ADR exists to prevent. ADR-0065's example must be corrected accordingly: `"Bearer ${STRATA_INGEST_TOKEN}"` is convention-B-shaped syntax that no resolver implements for that field.

### The SIEM auth contradiction is settled the same way

The `api_key` branch of `SiemBaseIntegration._build_auth_headers()` is brought in line with its own docstring: the field is resolved rather than used raw. With no compatibility to preserve, there is no layering — SIEM authentication fields accept `${secret:KEY}` or `${var:KEY}` and nothing else. A bare identifier is no longer quietly interpreted as an env-var name; it is a validation error naming the reference form.

That removes an entire class of ambiguity: today a value like `SPLUNK_TOKEN` could plausibly be a token or the name of a variable holding one, and no rule distinguishes them.

Two false docstrings are corrected: Splunk's claimed `{{ env.SPLUNK_HEC_TOKEN }}` support either becomes real via the layered resolver or is deleted, and `IntegrationEndpointsSpecModel.address` stops advertising `${VAR_NAME:default}` for a field that implements `{{ env.VAR }}`.

Converging credential conventions across the *whole* codebase is explicitly **out of scope** — it touches a dozen integrations and deserves its own ADR. This one fixes the audit and SIEM paths and establishes the direction.

### All sinks are integration references

**Decided: the full migration lands in one release.** A phased approach — removing `ndjson`/`stdout` now and `webhook`/`syslog` later — would break `spec.audit.sinks` twice. Since `extra="forbid"` makes any change to that model a hard break, it is taken once.

The governing principle: **a sink is a connection to another system.** Anything that is not one stops being a sink, and anything that is one is an integration like any other.

| Built-in type | Fate                                 | Why                                                                                                                                                               |
| ------------- | ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ndjson`      | Removed — becomes the **journal**    | A local file append is not a connection to another system, and it duplicated `logger/audit.py`, which already had rotation and gitignore handling the sink lacked |
| `stdout`      | Removed                              | Corrupts the `--output json` envelope (ADR-0004); the journal plus `--verbose` covers every real use                                                              |
| `syslog`      | Removed — becomes an **integration** | A genuine remote transport, so it belongs with the other remote transports                                                                                        |
| `webhook`     | Removed — becomes an **integration** | A genuine remote transport, and ADR-0065's primary channel                                                                                                        |

`AuditSinkModel` reduces to routing and nothing else:

```python
class AuditSinkModel(PlatformBaseModel):
    name: PlatformName            # routing identity
    integration: PlatformName     # → spec.integrations[].name  (required, only target)
    enabled: bool = True
    events: Optional[List[AuditEventType]] = None
```

No `type`, `path`, `url`, `headers`, `address`, `format`. Both `validate_sink_target()` and `validate_type_specific_fields()` — roughly fifty lines of combination-checking — are deleted outright. Problem 9 stops being fixed and starts being **impossible**: there is no field on a sink that can hold a credential.

#### Two new integration types

`webhook` and `syslog` join `splunk` / `elk` / `otel` / `sentinel` in `IntegrationFactory._BUILTIN_CLASS_MAP`, both implementing `ISiemSink` via `SiemBaseIntegration`. They inherit `_post_json`'s retry and backoff, which the built-in webhook sink never had, and `_build_auth_headers`, which is where credentials now live.

Transport configuration moves to the fields that already exist for it:

| Was (sink field)                               | Now (integration field)                  |
| ---------------------------------------------- | ---------------------------------------- |
| `url` / `address`                              | `endpoints.address`                      |
| `headers.Authorization`                        | `authentication` (`api_key` or `oauth2`) |
| `headers.*` (non-secret, e.g. `X-Scope-OrgID`) | `properties.headers`                     |
| `format: cef`                                  | `properties.format`                      |
| `transport: tcp+tls`                           | `properties.transport`                   |

Non-secret routing headers — Loki/Mimir's `X-Scope-OrgID`, custom source headers — are a real need, so they survive as `properties.headers` on the *integration*. Credentials go through `authentication` and nowhere else. Sinks still have no headers.

Hardening is inherited or applied in the new classes rather than bolted onto the sink model:

- `syslog` gains `properties.transport` with **`tcp` as the default** — the `udp` default existed only for compatibility, and unacknowledged cleartext is the wrong default for an audit channel. `udp` and `tcp+tls` remain selectable. Truncation is logged rather than silent.
- `webhook` rejects non-`https` `endpoints.address` unless `properties.allow_insecure: true` is set — keeping localhost testing possible while making plaintext audit egress deliberate.
- Delivery failures are logged at `warning`, not `debug`, and a per-run count of failed deliveries is surfaced in the command's `messages[]`.

### Integration instance identity

Sinks-as-references only works if the same integration type can be declared more than once — two Splunk indexes, a webhook to the ingest service and another to an internal bus. Today it cannot, silently.

`BaseIntegration.__new__` is a per-class singleton keyed by `_get_instance_key_static`, whose base implementation returns the literal `"default"`. Combined with `__init__`'s early return on `_initialized`:

| Case                                                         | Today                                                                      |
| ------------------------------------------------------------ | -------------------------------------------------------------------------- |
| Two `elk` / `otel` / `sentinel` declarations                 | **One object.** The second config is silently discarded                    |
| Two `splunk` with different `endpoints.address`              | Two objects — `SplunkSiemIntegration` is the only class overriding the key |
| Two `splunk` with the same address, different index or token | **One object.** First wins                                                 |
| Two integrations with the same `name`                        | Legal — there is no `check_unique_names` on `spec.integrations`            |

Two changes, both folded into this ADR because the sink migration cannot ship without them:

1. The base `_get_instance_key_static` returns `config.name` instead of `"default"`. A declaration's name *is* its identity, which is also what `sinks[].integration` refers to.
2. `check_unique_names` is applied to `spec.integrations`, so duplicate names fail validation at exit code 3 rather than resolving last-wins through `{m.name: m for m in …}`.

This changes behaviour for **every** integration, not only SIEM: two Vault or Bitwarden declarations become two instances where they previously collapsed into one. That is a bug fix — the current behaviour discards configuration without saying so — but it is a behaviour change, and it is called out in the risks below.

`SplunkSiemIntegration._get_instance_key_static` is deleted, since the base implementation now does the right thing.

### Audit is configured only in configuration documents

`spec.audit` exists on the **configuration** document and nowhere else. `EnvironmentModel.spec.audit` is **removed** — not wired through.

The reason is separation of duties, not tidiness. If audit configuration were overridable at environment level, anyone able to edit an environment file could set `secret.accessed: false` or `sinks: []` for the environment they are about to deploy to. An audit control that the audited party can switch off is not a control, and a reviewer could no longer answer "was production auditing on?" from one file — they would have to check every environment document that might override it. This is the property ISO 27001 A.12.4.2 and PCI DSS 10.5 are both getting at: log configuration and log content must not be alterable by the subject of the log.

The field is currently merged last-wins by `EnvironmentService` into a resolved model that nothing reads — dead plumbing on top of dead configuration — so removing it costs nothing today.

**Per-environment differences are expressed by profiles instead.** Configuration is merged from multiple documents in `.strata/`, and profiles select which set is active. A development profile can reference a configuration document whose `spec.audit` has no sinks; a production profile references one that ships to the SIEM. The differentiation is identical — it simply happens at the configuration layer, inside the same trust boundary, rather than by letting a lower-privilege file override a higher-privilege one.

`strata audit status` reports which configuration documents contributed to the effective audit block, so the answer to "what is auditing right now, and where did that come from?" is one command rather than an archaeology exercise.

#### What this does and does not buy

Being honest about the limit: anyone who can run the CLI can edit the configuration document too. Restricting audit to configuration documents is **defence in depth, not tamper-proofing** — it removes the easy and easily-overlooked path, and it makes the audit posture reviewable in one place. Genuine tamper-resistance requires the record to leave the machine into an append-only store, which is ADR-0065's job, not this one's.

The model docstrings, which say "environment YAML" while the code reads configuration YAML, are corrected to match.

### Worked example — one `spec.audit` covering all three classes

`.strata/configuration.yaml`:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: acme-platform

spec:
  # ── Integrations: every outward connection, declared once, referenced by name ──
  integrations:
    - name: splunk-prod
      type: splunk
      capabilities: [audit]
      endpoints:
        address: https://splunk.acme.internal:8088
      authentication:
        method: api_key
        api_key:
          api_key: "${secret:splunk_hec_token}"
      properties:
        index: infra_audit
        source: strata

    # Formerly `type: webhook` on the sink — ADR-0065's ingest service
    - name: strata-ingest
      type: webhook
      capabilities: [audit]
      endpoints:
        address: https://ingest.acme.internal/v1/events
      authentication:
        method: api_key
        api_key:
          api_key: "${secret:ingest_token}"
          header_name: Authorization
      properties:
        headers:                      # non-secret routing headers only
          X-Scope-OrgID: acme

    # Formerly `type: syslog` on the sink
    - name: soc-collector
      type: syslog
      capabilities: [audit]
      enabled: false                  # kept for incident use; costs nothing while off
      endpoints:
        address: siem-collector.acme.internal:6514
      properties:
        transport: tcp+tls
        format: cef

  audit:
    # ── 1. Journal — the local record (was logging.yaml → audit:) ───────────
    journal:
      path: .strata/audit.log
      rotation: size            # size | daily
      max_bytes: 5242880
      backup_count: 3

    # ── 2. Policy — the global gate, by class ──────────────────────────────
    policy:
      events:
        # Invocation — who ran what. Off by default: 95% of measured volume
        # was VS Code polling and pytest runs. Enable once the producer is
        # restricted to mutating commands.
        command.executed: false

        # Outcome — what a run did
        deployment.completed: true
        deployment.measured: true    # ADR-0064
        build.completed: false
        validation.completed: false

        # Domain events — what happened to the system
        policy.violated: true
        secret.accessed: true
        drift.detected: true
        lock.acquired: false
        lock.released: false

    # ── 3. Sinks — routing only. No transport fields exist on this model. ──
    sinks:
      - name: splunk
        integration: splunk-prod
        events:                     # omit for "everything the gate admits"
          - deployment.completed
          - policy.violated
          - secret.accessed

      - name: ingest
        integration: strata-ingest
        events:
          - deployment.completed
          - deployment.measured

      - name: soc
        integration: soc-collector
        enabled: false

    # ── Existing deploy-log settings, unchanged ────────────────────────────
    structure: by-date
    deploy_log_path: .strata/deploy-log
```

Reading the routing for a `secret.accessed` event: the gate admits it (`true`), so it is written to the journal, then offered to each sink — `splunk` is enabled and names it, so it is delivered. A `build.completed` is gated off, so no sink is consulted and nothing is written anywhere.

A development workspace differs by activating a profile that references a different configuration document — not by overriding from an environment file:

```bash
strata profile activate development
```

where that profile's configuration set contains an `spec.audit` with `sinks: []` and the same `policy` block. Development keeps the full local journal and ships nothing outward, and the difference is visible in a configuration document rather than buried in an environment override.

## Implementation plan

```
src/strata/
├── utils/
│   └── value_expr.py                  # promoted from TerraformDeployer._resolve_backend_expr
├── models/
│   ├── audit_config_model.py          # AuditEventType enum; AuditEventPolicyModel + Union shorthand;
│   │                                   # class-aware defaults;
│   │                                   # AuditJournalModel; AuditSinkModel → references only
│   └── configuration_model.py         # check_unique_names on spec.integrations
├── logger/
│   └── audit.py                       # event envelope; no-op under PYTEST_CURRENT_TEST
├── controllers/
│   └── audit_controller.py            # forward(event_type, payload) — journal then sinks;
│                                       # sink resolution moves here; _send_webhook/_send_syslog deleted
├── integrations/
│   ├── base_integration.py            # _get_instance_key_static → config.name
│   └── siem/
│       ├── base_siem_integration.py   # _build_auth_headers honours its own docstring
│       ├── webhook_siem_integration.py    # NEW — was AuditController._send_webhook
│       └── syslog_siem_integration.py     # NEW — was AuditController._send_syslog + _format_cef
└── commands/
    ├── base_command.py                # cli_action only for mutating ops; phase-1 journal reconfigure
    ├── deploy/run_deploy_command.py   # _resolve_siem_sinks / _forward_workitem_event deleted
    └── audit/                          # + status; resend and export route through forward()
```

Ordering. Steps 0–1 are non-breaking and can ship immediately; steps 2–6 are the single breaking release and land together — deliberately not started until steps 0–1 are verified (tests, lint, mypy) and the breaking release is explicitly confirmed, since steps 2–6 rewrite `spec.audit` for every existing workspace with no compatibility shim.

**0. Integration instance identity.** ✅ Done. `_get_instance_key_static` → `config.name`; `check_unique_names` on `spec.integrations`; deleted Splunk's now-redundant override. Non-breaking on its own (it makes previously-discarded declarations work), and everything else depends on it. Verified: full suite 5441 passed, ruff/mypy clean, plus new regression tests (`test_integrations_base.py`, `test_models_configuration.py`) covering distinct-instance-per-name and duplicate-name rejection.

**1. Stop the bleeding.** ✅ Done. `configure_audit_log` no-ops under `PYTEST_CURRENT_TEST`; `BaseCommand._is_audit_mutating_operation()` restricts `command.{OPERATION}` to mutating ops (excludes `*_list`/`*_show`/`*_status`/`schema_*`); sink delivery failures logged at `warning` instead of `debug`. No model change, removes ~95% of journal volume immediately (matches the measured `workitem_list`/`schema_get`/`schema_list`/`tools_status` volume). Fixes problems 3, 4. Verified: full suite 5454 passed, ruff/mypy/lint-imports clean, new regression tests (`test_audit.py`, `test_commands_base.py`).

**2. The two new integration classes.** ✅ Done. `WebhookSiemIntegration` and `SyslogSiemIntegration` (`integrations/siem/`), lifting the logic out of `AuditController._send_webhook` / `_send_syslog` / `_format_cef` and gaining `_post_json`'s retry via `SiemBaseIntegration`. Registered in `IntegrationFactory._BUILTIN_CLASS_MAP` as `webhook` / `syslog`. Hardening applied as designed: webhook rejects non-`https://` addresses unless `properties.allow_insecure: true`; syslog defaults to `tcp` (not `udp`), supports `tcp+tls`, and truncates oversized UDP datagrams with a logged warning instead of silently. `AuditController` itself is untouched for now — these classes exist and are independently usable/testable, but nothing routes to them yet (that's step 3, when `AuditSinkModel` becomes reference-only and `forward()` resolves sinks through the integration registry). Verified: full suite 5481 passed, ruff/mypy/lint-imports clean, 27 new tests (`test_webhook_siem_integration.py`, `test_syslog_siem_integration.py`). Found and fixed a latent host:port parsing bug in the process (an address with no `:port` suffix crashed instead of falling back to the default port) — present in the original `AuditController._send_syslog` too.

**3. `AuditSinkModel` reduces to references.** ✅ Done. `AuditSinkModel` now carries only `name`, `integration` (required), `enabled`, `events` — `type`/`path`/`address`/`url`/`headers`/`format` and both validators (`validate_sink_target`, `validate_type_specific_fields`) deleted, along with the `BUILTIN_SINK_TYPES` constant. `AuditController.forward_to_siem()` replaced by `forward(event_type, payload, audit_config=None)` — writes to the journal (`logger.audit.audit()`), then resolves sinks via `IntegrationService` (not caller-side pre-resolution) and calls `send_event()` on each. `_send_webhook`/`_send_syslog`/`_format_cef` deleted from `AuditController` (now live on the step-2 integration classes). `resend()` now calls `forward()` directly — this is what makes `resend` reach integration-backed sinks for the first time (problem 7), with no code change to `resend_audit_command.py` itself. `RunDeployCommand._resolve_siem_sinks()` deleted entirely; `_forward_workitem_event()` now calls `AuditController(...).forward(...)` instead of duplicating resolution (problem 8). Fixes problems 2, 7, 8, 9, 10.
  - **Known gap, originally deferred to step 6, closed in a post-step-6 follow-up (see below):** `strata audit export --siem <name>`'s `_forward_to_siem()` did its own independent `IntegrationFactory.create()` + `isinstance(..., ISiemSink)` resolution, bypassing `spec.audit.sinks` by design (it targeted an arbitrary named integration, not a configured sink) — this was problem 8's 4th divergent implementation.
  - `docs/help/audit.md` still shows the old sink shape (`type: ndjson`/`webhook`, `path:`, `headers:`) — left as-is since step 6 explicitly owns "help/audit.md corrected" and steps 4-5 change the event-type names shown in the same examples, so fixing it now would mean rewriting it twice.
  - Verified: full suite 5445 passed, ruff/mypy/lint-imports clean. Rewrote `test_controllers_audit_layer4.py`'s `TestForwardToSiem`/`TestFormatCef`/`TestResend` (→ `TestForward`/`TestResend`), `test_models_audit_config.py`'s `TestAuditSinkModel`, and deleted `test_commands_audit_siem.py`'s `TestAuditSinkModelFormat`/`TestAuditSinkModelUnknownType` (tested removed behavior; format/transport coverage now lives in the step-2 integration test files). Discovered mid-rewrite that a bare `MagicMock()` does not satisfy Python's `runtime_checkable` Protocol `isinstance()` check even with matching attributes — tests needing an `ISiemSink` now construct a real lightweight `WebhookSiemIntegration` instead, matching the existing convention already used in `test_commands_audit_siem.py`.

**4. Policy becomes real.** ✅ Done. Closed enum of 12 event types in `AUDIT_EVENT_DEFAULTS` (the ADR's original 8 plus `workitem.created`/`workitem.resumed` — see decision below), class-aware defaults, `AuditEventPolicyModel` (`enabled: bool`, reserved `severity`/`sample`/`retention_days` commented out) with `Union[bool, AuditEventPolicyModel]` shorthand normalized via a `field_validator(mode="before")`. `AuditPolicyModel.is_enabled(event_type)` is the one gate resolution function; `AuditController.forward()` now consults it *before* the journal write or any sink fan-out — an event type the gate disables reaches nothing. Added `AuditConfigModel.validate_sink_filters_against_gate()`: a sink naming an event type the gate has disabled, or one outside the closed set, is a validation error at exit code 3. All `forward()` call sites renamed from the old `"deploy_audit"` string to the canonical `"deployment.completed"` (`run_deploy_command.py`, `AuditController.resend()`, and `export_audit_command.py`'s separate bypass path, for naming consistency even though it isn't gated). Fixes problem 1.
  - **Decision made with the user**: work-item events (`workitem.created`, `workitem.resumed` — the only two work-item event names actually SIEM-forwarded today, via `RunDeployCommand._forward_workitem_event()`) are not part of the ADR's original 8 declared types, and the gate's "unknown event types default to not audited" rule would have silently disabled work-item SIEM forwarding the moment this step landed. Resolved by adding both to the closed enum now (Outcome class, default `true`) rather than letting them fall through ungated or silently break — flagged explicitly rather than guessed, since it's a real behavior-affecting decision the ADR itself doesn't address.
  - `AuditPolicyModel.is_enabled()` still treats a genuinely unrecognised event type (outside the 12) as **not gated off** — the closed-set validation only rejects unknown keys where they're explicitly configured (`policy.events`, `sink.events`); it does not retroactively block a runtime `event_type` string this model doesn't know about. This keeps today's behavior for any future producer not yet covered by this ADR, rather than silently blackholing it.
  - Real Pydantic v2 pitfall hit and fixed: a `field_validator(mode="before")` does **not** run against a field's `default_factory` output unless `validate_default=True` is set — so `AuditPolicyModel()` (no explicit `events=`) was returning raw `bool` values instead of normalized `AuditEventPolicyModel` instances. Fixed by having `default_factory` itself construct fully-normalized objects directly, rather than relying on the validator to run over the default.
  - Verified: full suite 5455 passed, ruff/mypy/lint-imports clean. Added dedicated tests for `AuditPolicyModel` (defaults, override-merging, unknown-key rejection, `is_enabled()` for both shapes and for out-of-set types), `AuditConfigModel`'s gate/filter consistency validator (4 cases), and `AuditController.forward()`'s gate behavior (blocks disabled, admits enabled).

**5. Identity and envelope.** ✅ Done. New `AuditController._build_envelope(event_type, payload)` wraps every `forward()`-routed payload in a CloudEvents 1.0 envelope with ECS fields under `data`, applied identically to the journal write and every sink send — callers (deploy, work items, `resend`) are completely unaware of the envelope shape; they still just pass the flat dict they already built (a `DeployLogModel` dump, a work-item dict), and `forward()` does the wrapping centrally (one place, per this ADR's own recurring principle). `data.user.name` comes from `resolve_actor()` (`controllers/actor_controller.py`, ADR-0066/ADR-0067); `data.labels.execution_id` carries the correlation key (falls back to a fresh UUID if the payload has none); `data.strata` holds the original, unmodified payload dict verbatim. `_EVENT_TYPE_METADATA` maps each of the 12 closed-enum event types to ECS `event.kind`/`event.category` per the ADR's type-name table (`workitem.created`/`resumed` classified alongside `deployment.completed`). Fixes problems 5, 6.
  - `SyslogSiemIntegration._format_cef()` updated to read the new envelope shape (`data.event.outcome`, `data.user.name`, `data.labels.deployment`/`execution_id`, top-level `time`/`type`) instead of the old flat fields — CEF's Signature ID is now the fully-qualified CloudEvents `type` string instead of a hardcoded `"deploy_audit"` literal, since sinks now handle any event type generically.
  - Every field extraction in `_build_envelope` is `.get()`-based and optional — a payload missing `workspace`/`deployment`/`success`/`duration_seconds` (e.g. a work-item dict, or a future producer) still gets a valid envelope, just with those specific fields omitted rather than raising.
  - Real testing gotcha hit and fixed: `resolve_actor()` runs for *every* `forward()` call now (populating `data.user.name`), which transitively calls the full cloud-CLI/CI-env/OS-login precedence chain — slow and non-deterministic in tests (returns the real OS username unless mocked). Fixed by adding an autouse fixture in `TestForward` mocking `resolve_actor()`.
  - Verified: full suite 5458 passed, ruff/mypy/lint-imports clean. Updated `TestForward`'s journal/sink assertions to check envelope structure (`type`, `data.strata`, `data.user`) instead of exact payload equality, since `id`/`time` are freshly generated per call. Rewrote `test_syslog_siem_integration.py`'s `TestFormatCef` to construct envelope-shaped fixtures.
  - **Not yet done, explicitly flagged**: `base_command.py`'s CLI-invocation journal write (`command.{OPERATION}`) still calls `logger.audit.audit()` directly rather than `AuditController.forward()`, so `command.executed` still bypasses both the policy gate (step 4) and the envelope (step 5). Wiring it in requires `base_command.py` to have a loaded `AuditConfigModel` at the point `_after_execute()` runs, which needs its own design pass (bootstrap timing, per step 6) — deferred there rather than bolted on here.

Step 5 is where ADR-0065's schema contract is realised; the shape itself is already settled by the CloudEvents + ECS decision.

**6. One configuration location.** ✅ Done. `AuditJournalModel` (`path`, `rotation`, `max_bytes`, `backup_count`, `date_suffix`) added at `AuditConfigModel.journal`. Two-phase bootstrap in `BaseCommand`: Phase 0 (unchanged, in `_initialize()`/`_initialize_without_solution()`, before configuration loads) opens the journal with built-in defaults so early failures still produce a record; new Phase 1 (`_apply_audit_journal_config()`, called right after `_load_config_sources()`) reconfigures the journal from `spec.audit.journal` once `ConfigurationService` has the merged model. `logger/audit.py`'s `configure_audit_log()` now records which layer supplied the current configuration (`get_audit_log_source()`: `"bootstrap"` / `"logging_yaml"` / `"spec_audit"`, plus `get_configured_audit_log_path()`), so Phase 1 can tell a bootstrap default apart from a `.strata/logging.yaml`-sourced config and skip reconfiguring when `logging.yaml` already claimed it — preserving the documented precedence `spec.audit.journal` < `logging.yaml` < built-in default. `EnvironmentModel.spec.audit` and the corresponding `EnvironmentService.merge_envfiles()` last-wins merge deleted outright (confirmed dead: every real read of audit config goes through `ConfigurationModel.spec.audit`, never the environment-level field). New `strata audit status` command (`commands/audit/status_audit_command.py`) reports the effective journal path/rotation/source, every event type in the policy gate and whether it's admitted, and every configured sink with its enabled state and whether its referenced integration actually exists. Old-shape detection added: `AuditSinkModel.reject_legacy_shape()` (a `mode="before"` validator) detects any of the pre-ADR-0066 transport fields (`type`/`path`/`address`/`url`/`headers`/`format`) and raises the exact replacement YAML verbatim, matching the ADR's own example; `AuditPolicyModel.validate_known_event_types()` now recognizes the 8 legacy event names (`LEGACY_EVENT_TYPE_RENAMES`) and raises "was renamed — use X" instead of the generic unknown-key list. `docs/help/audit.md` fully rewritten for the new shape (`spec.audit.journal`, integration-referenced sinks, the 12 new event-type names, the CloudEvents/ECS envelope, `strata audit status`). Fixes problem 11, and closes known gap 2 above.
  - Deliberately scoped down from the original plan: `strata audit status` does not report "last delivery outcome per sink" — that needs persisted delivery-history tracking, a materially bigger feature on its own, and was left as an explicit follow-up rather than folded in here.
  - **Known gap 3 — closed in a later post-step-6 follow-up (see below)**: at the time this step landed, `command.executed` still called `logger.audit.audit()` directly in `BaseCommand._finalize()`, not `AuditController.forward()`. This step built the bootstrap-timing infrastructure gap 3's fix depended on (`AuditConfigModel` reliably loaded via `ConfigurationService` by Phase 1's point in the lifecycle) but did not perform the wiring itself.
  - Verified: full suite 5489 passed, ruff/mypy/lint-imports clean. New tests: `TestAuditJournalModel` + legacy-shape/legacy-rename tests in `test_models_audit_config.py`; `TestAuditLogProvenance` in `test_audit.py`; `TestApplyAuditJournalConfig` in `test_commands_base.py` (bootstrap-vs-logging_yaml precedence, non-fatal failure handling); new `test_commands_audit_status.py` (10 tests: journal resolution across all three sources, policy gate reporting, sink reporting including missing-integration detection, CLI wiring).

**Post-step-6 follow-up: gap 1 closed.** ✅ Done. Decided with the user: `--siem <name>` now **requires** `<name>` to match an *enabled* `spec.audit.sinks[].integration` (product decision resolved in favour of consistency over today's "any declared integration by name" convenience), and routes through `AuditController._build_envelope()` plus `AuditConfigModel.policy.is_enabled()` before sending — closing both the sink-membership bypass and the deeper, previously-undocumented issue found while fixing it: `_forward_to_siem()` was also sending **raw, un-enveloped** payloads via `send_batch()`, so the same SIEM could receive two different wire shapes for `deployment.completed` depending on whether the record arrived via `export --siem` or via `resend`/`deploy run`. A disabled gate or a non-matching sink event filter is now a deliberate skip (`return True`, no error), not a failure — matching `forward()`'s own "policy working as intended" semantics. `_find_integration_model()` (the old `.strata/*.yaml`-scanning resolver) is now dead code and was deleted outright, since nothing calls it any more.
  - **sbom-ignore-rules side-channel brought into sync, not removed** (decided with the user: keep it, but make it predictable): it now reuses the *same* already-resolved integration instance (no redundant `_find_integration_model()` re-scan + second `IntegrationFactory.create()`), wraps its payload in the same CloudEvents 1.0 + ECS envelope (`type: xyz.huybrechts.strata.sbom_ignore_rules`) as the deploy-log batch, and a failure now surfaces into `self._errors` and fails the command, instead of being silently best-effort. It stays outside the policy gate by design — it isn't a deploy audit event, so `AuditPolicyModel.is_enabled()`'s "unrecognised type is never gated off" rule applies to it as-is, not a special case.
  - **Found and fixed a genuine, pre-existing bug while doing this**: the guard for whether to forward sbom-ignore rules at all was `if ignore_evidence:` where `ignore_evidence = ignore_cfg.model_dump(exclude_none=True)`. Every field on `SbomIgnoreConfigModel` is a list defaulting to `[]` (never `None`), so `exclude_none=True` never actually excludes anything — the dump is *always* a non-empty dict of empty lists, so this check was always truthy. In practice this meant **every** `--siem` export forwarded an sbom-ignore-rules batch, even on a workspace with no `.strata/sbom-ignore.yaml` at all and zero declared rules. Fixed by checking whether any of the four rule lists actually contains an entry, not whether the dump itself is non-empty.
  - Verified: full suite 5496 passed, ruff/mypy/lint-imports clean. `test_integration_not_found_returns_false` rewritten (the method it exercised no longer exists) to cover the real current not-found path — a sink declared but its integration missing from `IntegrationService`. New `TestSbomIgnoreRulesForwarding` class (5 tests): no forward when no rules declared, single resolved-instance reuse verified via `get_integration.assert_called_once_with(...)`, envelope shape assertion, and forward-failure-fails-the-command.

**Post-step-6 follow-up: gap 3 closed.** ✅ Done. `BaseCommand._finalize()`'s CLI-invocation audit entry (the call site is `_finalize()`, not `_after_execute()` as originally stated above — corrected here) now routes through a new `_forward_command_executed_audit_event()` helper calling `AuditController.forward("command.executed", payload, audit_config=...)`, instead of calling `logger.audit.audit()` directly. Four concrete unknowns were identified and resolved before implementing:
  - **Exception safety, resolved.** `execute()` wraps phases 1–4 in their own `try/except` but calls `self._finalize(...)` completely unguarded — and `forward()`'s envelope-building/journal write (unlike its per-sink loop) has no internal `try/except` either. The new call is wrapped at its `_finalize()` call site; an exception there is logged at `debug` and swallowed, never propagated, matching the "audit must never fail a command" guarantee the raw `audit()` function used to provide for free.
  - **Event-type/action naming, resolved.** The closed enum has one type, `command.executed` (mapped from legacy `cli_action`) — not `command.{OPERATION}` per operation, which was only ever the *local journal's* action string. `self.OPERATION` now travels inside the forwarded payload (`payload["operation"]`) and lands in `data.strata.operation` in the envelope; `data.event.action` is `"command-executed"` for every operation (mechanically derived by `_build_envelope` from the event type), so the per-operation distinction moves from the top-level `action` field to `data.strata`, not lost.
  - **`resolve_actor()` cost, resolved.** Added a per-process memoization cache (`actor_controller._actor_cache` / `reset_actor_cache()`) — `resolve_actor()` no longer re-runs the cloud-CLI/CI-env/OS-login chain (which can shell out, e.g. `az account show`) on every call within the same process. Matters more now that a single command execution can call `forward()` twice (once for `command.executed`, once for its own domain event, e.g. `deploy run`'s `deployment.completed`).
  - **New sink traffic once the gate is enabled, accepted as-is.** Not a new problem introduced here — it's the same behaviour every other gated event type already has (e.g. `build.completed`, also default-disabled). No special-casing added.
  - **Real, deliberate behaviour change surfaced by this fix** (see also Consequences → Risk): `forward()`'s gate blocks the journal write too, not just sinks — this has been true for every other event type since step 4, but `command.executed` was never subject to it until now. Since `command.executed` defaults to disabled, **CLI invocations no longer write a local journal entry by default** — previously this was unconditional (for mutating operations). An operator who wants the previous "every mutating command leaves a local trace" behaviour back must explicitly set `spec.audit.policy.events.command.executed: true` (which, with no sinks referencing it, produces a local-journal-only record — sink fan-out stays empty when `spec.audit.sinks` doesn't reference it).
  - Verified: full suite 5503 passed, ruff/mypy/lint-imports clean. New tests: `TestResolveActorCaching` (2 tests) in `test_controllers_actor.py`, plus an autouse cache-reset fixture added there (mirroring the existing env-var reset fixture) since the existing precedence tests call the *real* `resolve_actor()` repeatedly with different expected results per test. New `TestForwardCommandExecutedAuditEvent` (5 tests) in `test_commands_base.py`: gate-disabled-by-default no-ops before `resolve_actor()` is ever reached, gate-enabled forwards a properly-typed envelope with `operation` inside `data.strata`, the helper itself does *not* swallow exceptions (confirming `_finalize()`'s wrapping is what provides safety, not the helper), `_finalize()` itself swallows a forwarding exception without raising, and a `ConfigurationService` resolution failure falls back to `forward()`'s own defaults.

**Post-implementation code review fixes.** ✅ Done. A full read-through of `audit_controller.py` (the core routing logic) surfaced two issues, both fixed:
  - **Stale module docstring.** Still read "PR enrichment and SIEM forwarding are stubs in this phase (activated later)" — both are fully implemented (`enrich_with_pr_data()`, `forward()`). Corrected.
  - **CloudEvents `(source, id)` uniqueness violation, fixed.** `_build_envelope()` set the CloudEvents `id` field to `execution_id` — but `execution_id` is a *correlation* key deliberately shared across every event from one command run, while CloudEvents requires `(source, id)` together to identify one specific event. Concretely: `RunDeployCommand._forward_workitem_event()` (a gate created mid-deploy) and the same run's `deployment.completed` forward share `execution_id`, `workspace`, and `deployment` — identical `execution_id` plus identical computed `source` meant two genuinely different events collided on `(source, id)`, which a CloudEvents-conformant consumer's dedup logic could interpret as the same event twice (dropping the second). Fixed by generating a fresh UUID for `id` on every `_build_envelope()` call, independent of `execution_id`, which continues to live only in `labels.execution_id` for correlation. Verified nothing read `envelope["id"]` expecting it to equal `execution_id` (`_format_cef()` already reads `labels.execution_id`, not `data["id"]`).
  - Verified: full suite 5504 passed, ruff/mypy/lint-imports clean. New test `test_id_is_independent_of_execution_id` in `test_controllers_audit_layer4.py`: two envelopes built from payloads sharing `execution_id` and `source` get different `id` values, and neither `id` equals the shared `execution_id`.

**Two more producers wired: `lock.acquired`/`lock.released` and `drift.detected`.** ✅ Done. Of the five event types with no producer (Consequences → Good: "the four unwired event types"), two had a clean, single, unambiguous call site with no layering conflict — wired in now rather than left as further plumbing debt:
  - **`lock.acquired` / `lock.released`** — `BaseDeployCommand._acquire_lock()` / `_release_lock()` (shared by `RunDeployCommand`/`DestroyDeployCommand`) each gained a call to a new `_forward_lock_audit_event(event_type, handle, deploy_name)` helper right after their existing success path, resolving `AuditConfigModel` via `ConfigurationService.get_instance()` the same way `base_command.py`'s `command.executed` forwarding does. Both default to disabled, so this is a no-op unless `spec.audit.policy.events.lock.acquired` / `.lock.released` is explicitly turned on.
  - **`drift.detected`** — `DriftDeployCommand._run_drift_detection()` calls a new `_forward_drift_audit_event(report)` helper whenever `report.has_drift` is true (including in `--baseline` mode — drift *was* detected in this run, independent of whether it is then acknowledged), using `DriftReport.to_dict()` as the payload plus `execution_id`. Defaults to enabled (Domain class), so this reaches configured sinks by default the first time this ships.
  - **Two genuinely remain unwired at this point**: `policy.violated`'s producing logic lives in `validators/`, which sits *below* `controllers/` in ADR-0003's chain, so `PolicyEngine` cannot call `AuditController.forward()` directly without a layering violation — the trigger has to move to each of its several command-layer call sites via a shared helper (addressed in the very next entry below). `secret.accessed` turns out not to have the same obstacle at all — see its own entry further below, which corrects this. `deployment.measured` remains unwired because ADR-0064's metrics record itself is not yet implemented — there is no producer to wire.
  - Verified: full suite 5513 passed, ruff/mypy/lint-imports clean. New tests: 4 added to `TestLockingWiring` in `test_commands_deploy.py` (forwards when gate enabled, no journal write when gate disabled by default, forwarding failure doesn't raise); new `test_commands_deploy_drift.py` (5 tests: forwards when enabled, no journal write when disabled, forward/config-resolution failures don't raise, `_run_drift_detection()` only forwards when `has_drift` is true).

**`policy.violated` wired too.** ✅ Done. Confirmed the layering read above precisely: `strata.controllers` sits *above* `strata.validators` in ADR-0003's `layers` contract (`commands → controllers → builders|deployers → validators → services → integrations → models → utils`, each layer importable only by the ones above it) — so while `PolicyEngine` (`validators/`) genuinely cannot import `AuditController` (`controllers/`), the reverse is fine: `AuditController` *can* import `PolicyResult` from `validators/`. This made a proper, centralised fix possible rather than a scattered one:
  - New `AuditController.forward_policy_violation(result: PolicyResult, execution_id=None, deployment=None, workspace=None, audit_config=None)` — builds the `policy.violated` payload (`policy_name`, `policy_type`, `enforcement`, `violations`, `success=result.passed`) and calls `forward()`, resolving `AuditConfigModel` via `ConfigurationService.get_instance()` when not given. All payload-shaping and config-resolution logic lives here, in the one place — not duplicated at each call site.
  - New `BaseCommand._forward_policy_violation_audit_event(result)` — a thin, shared wrapper every policy-evaluating command already inherits, resolving `deployment` from `self._deployment_service` when present and passing `self._execution_id`. Never raises.
  - Wired into all four real call sites (`policy_engine.py`'s own docstring usage example was not a real call site — there are four, not five): `run_validate_command.py._evaluate_validate_policies()`, `run_build_command.py` (its `build`-phase policy loop), `run_deploy_command.py` (its per-stage policy loop), and `check_policy_command.py._run_execution()`. Each already looped over `PolicyEngine.evaluate()` results with near-identical code (checking `result.passed`, branching on `enforcement`) — one line added per site, calling the shared helper whenever `not result.passed`, for *any* enforcement level (`deny`/`warn`/`audit`), not just `deny` — a policy was violated regardless of what action strata itself takes about it; `enforcement` travels in the payload so a consumer can distinguish severity.
  - **Real, deliberate behaviour change**: `policy.violated` defaults to **enabled** (Domain class). Since this event type was previously *never produced anywhere* (problem 1's "dead configuration"), every policy denial/warning across `validate`/`build`/`deploy`/`check_policy` now writes a local journal entry (and reaches any configured sink) by default, the first time this ships — this is the gate finally doing what its class-aware default always intended, not a new risk introduced here.
  - Verified: full suite 5523 passed, ruff/mypy/lint-imports clean (including `lint-imports` specifically, confirming the `controllers` → `validators` import direction is allowed). New tests: `TestForwardPolicyViolation` (4 tests) in `test_controllers_audit_layer4.py`; `TestForwardPolicyViolationAuditEvent` (4 tests) in `test_commands_base.py`; two new tests in `test_commands_policy_check.py` confirming a failed *warn*-enforcement result still forwards (not just `deny`) and a passed result does not.
  - `secret.accessed` remains unwired — not for a layering reason after all (see below), but as a deliberate decision.

**`secret.accessed` — deliberately not wired.** The earlier layering claim above (grouping `secret.accessed` with `policy.violated` as blocked by ADR-0003) was itself corrected on inspection: the actual secret-resolution logic lives in `ValueController.resolve_values()`/`_resolve_secret()`, and `ValueController` is in `controllers/` — a **peer** of `AuditController`, not a layer below it. There is no layering obstacle here at all; `ValueController` could call `AuditController.forward()` directly, from one hook point (`resolve_values()`'s secrets loop), which is structurally simpler than `policy.violated`'s four call sites. Discussed with the user and decided **not to wire it, for reasons independent of feasibility**:
  - **The store already does this, and does it better.** HashiCorp Vault's audit backend, Azure Key Vault diagnostic logs, and Bitwarden's own access logs already record who/when/from-where for every read, typically more rigorously (immutable by design, in some cases) than anything `AuditController.forward()` could add. Strata is not the security boundary for these systems; duplicating their own audit trail adds little.
  - **The only unique value is correlation** — joining "which secrets did *this specific deploy* touch" to `execution_id`, which the store's own log can't know about. Real, but narrow: nothing today asks this question, and no other part of ADR-0066 depends on it.
  - **The volume risk is concrete, not hypothetical.** `resolve_values()` is called from 8 command sites, three of which are read-only inspection commands (`deploy values list` / `get` / `show`) run as often as someone wants while debugging — and the single hook point inside `resolve_values()` has no way to distinguish "a real deploy" from "someone looking at resolved values." Wiring it as-is would reproduce, one layer down, exactly the read-only-command polling-volume problem step 1 fixed for `command.executed` (problem 3) — this time without step 1's clean fix, since `_is_audit_mutating_operation()` classifies *commands*, not *calls into a shared controller method* invoked by both mutating and read-only commands alike.
  - `secret.accessed` stays in the closed enum with its `true` default, so the concept and the class-aware default remain intact and documented (see `AUDIT_EVENT_DEFAULTS` in `audit_config_model.py`, which now carries this same reasoning as an inline comment) — this is a deliberate non-decision, revisitable if a concrete cross-store correlation need materialises, not an oversight to be quietly rediscovered later.

**Auditor's-lens review: two real gaps found, design only (not yet implemented).** Reviewed the closed enum against what an ISO 27001 / NIS2 / ISAE-style auditor actually tests for (change management, change-approval evidence, control-effectiveness evidence, configuration integrity, access control, supply-chain provenance) rather than against SIEM/ops convenience. `deployment.completed`/`policy.violated`/`drift.detected` map cleanly onto core controls and are already wired. `build.completed` turned out weaker than initially assumed: an auditor doesn't care that a build happened, they care what went into what got deployed — better satisfied by `deployment.completed` referencing the build manifest's artifact hash than by a standalone event, so it stays unwired (see the `build.completed`/`validation.completed` discussion — `validation.completed` has no side effect and no persisted artifact, same exclusion class as `secret.accessed`; `build.completed` is a genuine "not needed *as its own event*" call, revisit only if supply-chain attestation becomes a dedicated requirement). Two real, more consequential gaps surfaced instead, both **found in code, not assumed**:

**Gap A — `workitem.approved`/`rejected`/`completed`/`cancelled` bypass the entire ADR-0066 pipeline.** `WorkItemController.resolve()` (called by `approve()`/`reject()`/`complete()`/`cancel()`) still calls the raw `logger.audit.audit()` journal function directly, with `f"workitem.{status}"` as the action string:
```python
audit(
    f"workitem.{status}",
    outcome="success",
    target=item_id,
    detail={"resolved_by": result.resolved_by, "note": note},
)
```
This predates ADR-0066 and was never migrated — unlike `workitem.created`/`workitem.resumed` (which *do* route through `AuditController.forward()`, from the deploy side), the four resolution outcomes are not in the closed enum at all, bypass the policy gate entirely (fire unconditionally, regardless of `spec.audit.policy.events`), never get the CloudEvents/ECS envelope, and never reach a configured sink — only the local journal. This is exactly the class of problem ADR-0066 exists to fix, on a producer this ADR's own migration missed. It matters specifically because **change-approval evidence — who approved a production change, when, with what justification — is one of the single most commonly tested controls** (ISO A.8.32, SOC2 CC8.1), and today it is the weakest-represented event in the entire set: present only as an unstructured local-journal line, invisible to any SIEM.

Proposed design:
- Add four new closed-enum event types: `workitem.approved`, `workitem.rejected`, `workitem.completed`, `workitem.cancelled` — Outcome class, default **on** (matching `workitem.created`/`workitem.resumed`'s existing precedent; these are exactly as auditable as the creation/resume events already wired).
- Replace the raw `audit(...)` call in `WorkItemController.resolve()` with `AuditController(work_path=...).forward(f"workitem.{status}", payload, audit_config=...)`, resolving `AuditConfigModel` internally via `ConfigurationService.get_instance()` — matching `forward_policy_violation()`'s pattern of doing config resolution once, inside the shared method, not at each call site.
- Payload: `item_id`, `deployment` (from `result.deployment`), `commit` (from `result.commit`), `resolved_by`, `note`. No `execution_id` — approve/reject/complete/cancel are independent CLI invocations (often days after the deploy that created the gate paused), so there is no natural parent execution to correlate to; `_build_envelope()` already falls back to a fresh UUID when `execution_id` is absent, exactly as designed for this case.
- `WorkItemController` lives in `controllers/`, the same layer as `AuditController` — no ADR-0003 layering obstacle, same situation as `secret.accessed`'s corrected analysis.

**Gap B — `deploy destroy` produces no deployment-outcome event at all.** `destroy_deploy_command.py` calls the shared `_write_deployment_manifest(action="destroy", ...)` (the BOM/artifact-tracking manifest, also used by `deploy run`) but never builds a `DeployLogModel`, never calls `AuditController.write_deploy_log()`, and never calls `forward()` — the entire "Layer 4a/4b/4c" block that `run_deploy_command.py` has (PR enrichment, `forward("deployment.completed", ...)`, push-to-remote) simply does not exist for destroy. A destructive, irreversible action is currently **less observable** to the audit subsystem than a routine deploy — every ADR-0066 control (gate, envelope, sink fan-out) that applies to `deploy run` applies to nothing when infrastructure is torn down.

Proposed design:
- Add a new closed-enum event type `deployment.destroyed` — Outcome class, default **on**, classified as `event.kind: alert` (not plain `event`) in `_EVENT_TYPE_METADATA`, matching the treatment already given to `policy.violated`/`drift.detected` — a destroy is categorically more consequential than a routine deploy and should be distinguishable in SIEM alerting rules, not folded into the same `deployment.completed` stream.
- Extract the existing deploy-log-write-and-forward block from `run_deploy_command.py` (`DeployLogModel` construction → `controller.write_deploy_log()` → `enrich_with_pr_data()` → `controller.forward(event_type, ...)` → optional `push_to_remote()`) into a new shared helper on `BaseDeployCommand`, parameterised by `event_type` — the same "one place, one path" principle `_write_deployment_manifest()` already follows for the manifest side (it already takes an `action: "deploy" | "destroy"` parameter; the new helper should too).
- `run_deploy_command.py` calls the shared helper with `event_type="deployment.completed"`; `destroy_deploy_command.py` calls it with `event_type="deployment.destroyed"` — closing the gap without duplicating the ~70-line block a second time.

Both are now implemented (see below) — no longer design-only.

**Both gaps implemented.** ✅ Done.
- Gap A: `WorkItemController.__init__`/`local()`/`from_config()` now carry `work_path`. `resolve()` calls a new `_forward_resolution_event()` which resolves `AuditConfigModel` via `ConfigurationService.get_instance()` and forwards `workitem.{approved,rejected,completed,cancelled}` through `AuditController.forward()` — gated, enveloped, sink-forwarded, journal-written exactly once. `request()`'s old raw `audit("workitem.created", ...)` call was removed outright (not migrated) rather than kept as a second, divergent path: it duplicated `RunDeployCommand._forward_workitem_event("workitem.created", ...)`, which already forwards properly from the one and only caller of `request()` (`WorkItemGateController.evaluate_and_create()`, invoked exclusively from `run_deploy_command.py`). Four new closed-enum types added to `AUDIT_EVENT_DEFAULTS` (default `true`) and `_EVENT_TYPE_METADATA` (`("event", ["configuration"])`, matching `workitem.created`/`workitem.resumed`).
- Gap B: `BaseDeployCommand` gained `_get_git_field()`/`_get_current_commit()` (moved from `RunDeployCommand`, no behaviour change) and a new `_write_deploy_log_and_forward(event_type, success)` — the generalised form of what used to be `RunDeployCommand._write_deploy_log()`, parameterised by `event_type` so both commands share one implementation. `RunDeployCommand._write_deploy_log(success)` is kept as a one-line wrapper calling it with `"deployment.completed"` (avoids rewriting the large existing test suite that patches/calls `_write_deploy_log` directly — behaviour is identical, just delegated). `DestroyDeployCommand` gained its own `_finalize()` override, calling the shared helper with `"deployment.destroyed"` under the same `deploy_started_at and not dry_run` guard `RunDeployCommand` already used. New closed-enum type `deployment.destroyed` added (default `true`, `event.kind: alert` in `_EVENT_TYPE_METADATA` — distinguishable from routine deploys in SIEM alerting rules, matching `policy.violated`/`drift.detected`'s treatment).
- Docs updated to match: `docs/help/audit.md`'s policy-gate table and `docs/guides/siem-audit-forwarding.md`'s event table both list the five new event types now.
- Verified via full `Check.ps1` (ruff, mypy `./src ./tests`, full pytest suite, smoke tests, doc-coverage guards) — all green, no test rewrites needed beyond what's noted above.

**A third gap, found by comparing `drift.detected` against cost: `cost.threshold_exceeded`.** ✅ Done. Reviewing what SIEM signal exists for each of ADR-0065's four durable-storage record kinds surfaced a real asymmetry: `drift.detected` fires unconditionally whenever `DriftDeployCommand` finds any drift at all (`_forward_drift_audit_event()`, wired above) — but cost had no equivalent producer anywhere. `CostThresholdPolicy` (a `plan`-phase policy, `validators/policies/cost_threshold_policy.py`) already produces a `policy.violated` event when a *deploy's* estimated cost exceeds a configured ceiling — that path is real and already wired — but it only fires during `deploy`/`build`'s own policy evaluation. `CostController._record_history_snapshot()` (where ADR-0065 Phase 1's git-push was wired) is only called from `CostController.show()` — i.e. only `strata cost show` produces a history snapshot at all. `diff()` (used by both `strata cost diff` and `deploy run`'s auto-diff step) never calls it, so a deploy's own cost check, however expensive, produces no history entry and therefore no threshold signal either — this new event only ever fires from a standalone `cost show`, and never triggered any policy evaluation before this change, no matter how expensive the result or how sharply it changed since the last snapshot.

Unlike drift, "any cost" is not inherently alert-worthy the way "any out-of-band change" is — a trigger condition is needed, not just a presence check. Implemented, deliberately reusing `CostThresholdPolicy`'s existing `max_monthly` vocabulary rather than inventing a second one:

- New closed-enum event type `cost.threshold_exceeded` — Domain class, default **on**, `event.kind: alert` in `_EVENT_TYPE_METADATA` (same treatment as `drift.detected`/`policy.violated`/`deployment.destroyed`).
- New config, alongside cost's existing `repository` block (`spec.cost.history`): `alert: { max_monthly: <float>, delta_percent: <float> }` (`CostAlertConfigModel`), both optional. `max_monthly` mirrors `CostThresholdPolicy`'s field name and meaning exactly, deliberately, so operators don't learn a second vocabulary for the same concept; `delta_percent` is new — a relative-jump trigger `CostThresholdPolicy` has no equivalent for, since a policy only ever sees one deploy's estimate, never a time series.
- Trigger, evaluated in `CostController._forward_cost_audit_event()`, called right after the existing `_push_cost_history()` (same call site, same best-effort/non-fatal wrapping): fires if `total_monthly > alert.max_monthly` ("ceiling"), or if `delta_from_previous > 0` and `(delta_from_previous / (total_monthly - delta_from_previous)) * 100 >= alert.delta_percent` ("delta") — a cost *decrease* never fires the delta condition, only the ceiling can fire on its own. `delta_from_previous` is not recomputed — `CostHistoryStore.record_snapshot()` already computes and stores it on every snapshot; the method reads it from `store.latest()` rather than duplicating the arithmetic.
- Payload: `deployment`, `recorded_at`, `currency`, `total_monthly`, `delta_from_previous`, `alert_reason` (`"ceiling"` or `"delta"`), `provisioners` (per-provisioner breakdown, same shape as `cost.json`), `version` when available.
- Deliberately does not touch `CostThresholdPolicy` or its `policy.violated` path — that stays exactly as-is for deploy-time gating (`deny`/`warn`/`audit` enforcement); this new event is additional SIEM coverage for the case the policy engine structurally cannot see (a standalone cost check, or a trend the policy's single-snapshot view has no memory of), not a replacement for it.
- Docs updated to match: `docs/help/audit.md`'s event table lists the new event type. Verified via full `Check.ps1` (ruff, mypy, full pytest suite including new `tests/strata/controllers/test_controllers_cost_audit.py`, Sphinx docs build, all guards) — all green.

## Consequences

### Good

- **One place to configure audit** — `spec.audit` covers journal, policy, and routing, and exists on the configuration document only; `logging.yaml` survives as a documented machine-local override rather than a hidden prerequisite
- **The audited party cannot quietly reduce what is audited** — no environment-level override means "was production auditing on?" is answerable from one document
- **Credentials cannot leak through a sink, because sinks have no fields to leak through** — problem 9 becomes impossible rather than fixed
- **One connection model** — every outward destination is an integration, so `spec.integrations` is the complete inventory of what strata talks to, and `strata tools status` can report on all of it
- **Webhook and syslog gain retry, backoff, and authentication** for free by inheriting `SiemBaseIntegration` — the built-in webhook sink had none of it
- **The default configuration stops shipping noise** — `cli_action: false` plus a mutating-only producer removes ~95% of measured volume before it can reach a billed destination
- **Audit can answer "who"** — `actor` exists, and `execution_id` joins invocation to outcome
- **Configuration means what it says** — every declared knob is read, and typos fail validation instead of failing silently
- **Declaring an integration twice now works** — two Splunk indexes, two webhooks; previously the second was silently discarded
- **New producers are one line** — the four unwired event types and ADR-0064's metrics record stop being plumbing projects
- **`audit resend` actually resends** — to every configured destination, which is the only reason the command exists
- **The envelope and event names are standards, not inventions** — CloudEvents 1.0 for identity and transport semantics, ECS for field names, so SIEM field extraction is a no-op and `event.kind: alert` routes policy violations and drift without a correlation rule
- **`strata audit status` exists** — the subsystem can be inspected rather than assumed
- **~50 lines of sink validation deleted**, along with `_send_webhook`, `_send_syslog`, `_format_cef`, and `_resolve_siem_sinks`

### Neutral

- **Three classes, one flat `events` map** — the classes govern *defaults and documentation*, not the config shape; operators still write one boolean per type
- **`forward_to_siem()` is deleted outright** — no wrapper, no deprecation window
- **Filtering stays boolean** — sufficient for observed use cases; Option C remains available if predicates are ever genuinely needed
- **Journal retention is unchanged** — rotation already existed and was already correct; only its configuration location moves
- **One credential syntax on the audit path** — `${secret:}` / `${var:}` only; the other two conventions survive elsewhere until a dedicated ADR converges them

### Risk

- **Restricting the `cli_action` producer removes entries some operator may depend on.** Someone may be grepping `.strata/audit.log` for `command.schema_get`.
  - Mitigation: the entries were never useful for audit; anyone needing full command tracing has the application log at DEBUG. Note it in the changelog.
- **Turning on `policy.events` changes behaviour for workspaces that set it while it was inert.** A workspace with `deploy_audit: false` set on the assumption it did nothing would stop receiving events.
  - Mitigation: ship step 3 in a minor release and log at `warning` on the first run where the gate suppresses an event that would previously have been delivered.
- **Routing `command.executed` through `forward()` (gap 3 fix) makes the local journal itself subject to the gate for the first time.** Every mutating CLI invocation used to leave an unconditional `.strata/audit.log` entry; since `command.executed` defaults to disabled, that stops happening by default the moment this ships — a real, user-visible change from today's behaviour, not merely a SIEM-forwarding change.
  - Mitigation: this is consistent with how every other gated event type already behaves (the gate has always blocked the journal write too, since step 4) — `command.executed` is not a special case, it is catching up to the rest. Documented in the changelog and in `docs/help/audit.md`; an operator who wants the old "always leaves a local trace" behaviour back sets `spec.audit.policy.events.command.executed: true` (which, with no sink referencing it, produces a local-journal-only record).
- **Two-phase journal configuration means entries can land in two files during one run.** A `spec.audit.journal.path` pointing elsewhere leaves the pre-config entries in the default file.
  - Mitigation: the window is pre-config-load only; `strata audit status` reports both paths when they differ.
- **Moving journal config to `spec.audit` puts a filesystem path in shared, committed configuration.** A workspace that sets an absolute path breaks every other machine.
  - Mitigation: the documented value is workspace-relative; absolute paths are exactly what the `logging.yaml` override tier is for, and validation warns when `journal.path` is absolute.
- **The layered credential resolver is a behaviour change for existing SIEM configs.** A bare env-var name in `authentication.api_key.api_key` — the documented convention today — stops working and becomes a validation error.
  - Mitigation: accepted deliberately. The error names the `${secret:KEY}` replacement, and the ambiguity being removed (is `SPLUNK_TOKEN` a token or a variable name?) was itself a hazard.
- **Every existing `spec.audit` block breaks — sinks, policy keys, and credential fields alike.** There is no old-shape configuration that still validates.
  - Mitigation: accepted; this is the clean-break decision. What the release owes operators is a validation error that prints the replacement verbatim, not a shim. Changelog entry and an upgrade section in the docs.
- **Keying integration instances on `config.name` changes behaviour for every integration, not just SIEM.** Two Vault or Bitwarden declarations that previously collapsed into one object now become two, each with its own configuration and its own connection.
  - Mitigation: the previous behaviour silently discarded configuration, so anything relying on it was relying on a bug. But it is a real behaviour change with reach beyond this ADR — it warrants its own changelog entry and its own test pass across the integration suite.
- **A workspace with no SIEM now has no sinks at all.** With `ndjson` and `stdout` removed, a user who wanted a second local file of audit events has only the journal.
  - Mitigation: the journal is that file, with rotation the sink never had. If a *second* local destination is genuinely wanted, `type: file` can be added as an integration later without breaking anything, because the sink model no longer constrains it.
- **Removing environment-level `spec.audit` means per-environment differences now require a profile.** A team that expected to vary sinks by writing an environment file has to restructure into per-profile configuration sets instead.
  - Mitigation: the field never worked — it was merged into a model nothing read — so no existing workspace is relying on it. Profiles already exist and already select configuration sets; this uses them rather than adding a mechanism.
- **Restricting audit to configuration documents is not tamper-proofing.** Anyone who can run the CLI can edit the configuration document.
  - Mitigation: stated explicitly in the ADR rather than implied. It removes the easy path and makes posture reviewable in one place; real tamper-resistance is ADR-0065's append-only external store, and this ADR should not be read as providing it.

## Decisions to settle before implementation

This ADR redefines `spec.audit` as a clean break — no shim, no deprecation cycle (see "This is a clean break"). What remains to settle is therefore not *how much* to break, but the handful of choices that would be expensive to revisit afterwards.

**Settled:** the sink migration is taken in full rather than phased; integration instance identity is folded in as its prerequisite; the envelope and event type names are taken from CloudEvents 1.0 and ECS rather than invented; audit is configurable only in configuration documents, with profiles providing per-environment differentiation; no backwards compatibility is provided, only a validation error that names the replacement; `policy.events` values take `Union[bool, AuditEventPolicyModel]`, with a bare bool as shorthand for `{enabled: <bool>}` (see "Policy values are a `bool` or an object" above); `forward()` retries transient sink delivery failures in-process rather than relying solely on `strata audit resend` (see "In-process retry for transient delivery failures" below); `sink.events` stays a plain inclusion list, with no negation syntax; `strata audit resend` / `strata audit export` gain on-demand secret resolution scoped to sink authentication only — never to event payload content (see "On-demand secret resolution for resend/export—scoped to authentication only" below); and `actor` resolves from the cloud provider CLI identity used by the deployment first, falling back to CI/OS identity (see "What `actor` resolves from" below) — **implemented** as the single `resolve_actor()` function in `controllers/actor_controller.py`, superseding this section's original resolution order with a control-plane identity check ahead of it (see below). Topic 3 is deferred to the forthcoming CLI-consolidation ADR, not decided here. No topics remain genuinely open, and none block implementation.

### What `actor` resolves from

**1. Settled and implemented: control-plane identity first, then cloud provider CLI identity, then CI actor, then OS login.**

Strata already has three integrations that answer "who is authenticated right now" for a cloud provider — `AzureCLIIntegration.get_subscription()` (whose underlying `az account show` JSON also carries a `user` object with the signed-in principal's UPN/name), `AWSCLIIntegration.get_identity()` (`Account`/`UserId`/`Arn` from `aws sts get-caller-identity`), and `GCloudCLIIntegration.get_account()` (the active `gcloud` account email). Whichever of these backs the provisioner actually executing the deployment is the strongest available claim of "who ran this" — it is a credential the provider's own IAM issued and can be independently verified against that provider's activity logs, not a value strata invented.

The implemented resolution order, in `resolve_actor()` (`controllers/actor_controller.py` — deliberately placed in `controllers/`, not `utils/`, because it depends on `IdentityController` and `IntegrationService`; see ADR-0003):

0. **Control-plane identity (ADR-0067)** — if `IdentityController` has an authenticated session (a human logged into strata's own control plane), that identity outranks everything below it, because strata itself performed the authentication rather than merely reading an ambient credential. This was the one piece this ADR left as a forward reference to ADR-0067; it is now wired in as step 0, not a separate, unimplemented resolution path.
1. **Cloud provider identity** — resolved from whichever of `azure_cli` / `aws_cli` / `gcloud_cli` is configured and available, checked in that fixed order (Azure, then AWS, then GCloud) via a shared `find_available_integration_with_capability()` helper (`controllers/integration_lookup.py`). Azure contributes `get_signed_in_user()`'s `name`; AWS contributes the last path segment of the `Arn` from `get_identity()`; GCP contributes `get_account()`.
2. **CI actor env vars** — `CI_ACTOR` (a generic override, checked first), then `GITHUB_ACTOR`, then `BUILD_REQUESTEDFOR` (Azure DevOps) — used when no cloud integration is configured or available.
3. **OS login** — `$USER`, then `%USERNAME%`, then `getpass.getuser()` (more portable than `os.getlogin()`, which fails without a controlling tty) — falling back to the literal string `"unknown"` if every source is unavailable, since `resolve_actor()` never raises.

This sharpens, rather than removes, the earlier caveat that **`actor` is self-asserted, not authenticated**: that caveat now applies precisely to steps 2 and 3 — `GITHUB_ACTOR` is a runner-supplied environment variable and `$USER`/`%USERNAME%` are trivially set, so treat those as claims, not proof. Steps 0 and 1 are different in kind: step 0 was authenticated by strata's own control-plane login; step 1 was authenticated *by the cloud provider*, so it is externally verifiable against that provider's own audit trail (Azure AD sign-in logs, AWS CloudTrail, GCP Cloud Audit Logs) even though strata itself does not verify it further. ECS `user.name` carries whichever value resolved, and does not distinguish these cases in the schema — that distinction is documentation, not a new field, to avoid the same kind of dead-configuration complexity this ADR exists to remove elsewhere.

This was the one decision in this section that could corrupt *stored* records if revisited later, and it no longer blocks implementation — nor is it still pending: every command that previously duplicated this fallback chain (`base_deploy_command.py`, `lock_deploy_command.py`, `run_build_command.py`, `workitem_controller.py`, `promote_controller.py`) now calls the single `resolve_actor()` instead, closing the exact "scattered implementation" risk this ADR's own "one implementation, one place" principle (problem 8) warns about elsewhere.

**Out of scope, on purpose:** this resolves `actor` for CLI-invoked commands and events only — no login, no server, no session, for the fallback steps (1-3). The moment a *user* (rather than a CI runner or a local CLI invocation) needs to authenticate to a *server* — ADR-0065's Phase 3 control plane, any future dashboard or approval UI — the problem changes shape: OIDC/OAuth2 login, session or token issuance and revocation, mapping an IdP identity onto strata's `actor`, and an authorization model for who may approve or deploy where. ADR-0065 already names that gap without resolving it (its open question 5, and its Phase 3 description of needing "an authorization model for who may deploy where"). [ADR-0067](0067-server-identity-authentication-authorization.md) settles that separately rather than improvising it inside either this one or ADR-0065 — it involves choices (IdP/protocol, session vs. token model, in-package vs. separate service) that neither ADR is scoped to make. ADR-0067's CLI-side steps (identity capability, `IdentityController`, six identity-provider integrations, `sln doctor --login`) are implemented; its control-plane session outranking this ADR's CLI-side resolution chain (step 0 above) is implemented as well, ahead of Phase 3 itself being scheduled — only the server-side relying party, session store, and RBAC enforcement (ADR-0067 steps 7-10) remain gated on that scheduling.

### Cheap insurance — costs little now, forecloses a break later

**2. Policy value shape — settled: `Union[bool, AuditEventPolicyModel]`.**

Decided in favour of the union. `Dict[str, bool]` would foreclose per-event severity, sampling ("audit 1 in N `command.executed`"), and retention hints, and the single-break constraint makes the map's shape expensive to revisit later. The value type is therefore `Union[bool, AuditEventPolicyModel]` with a bare bool normalised to `{enabled: <bool>}`; the reserved fields stay commented out until a producer reads them. See "Policy values are a `bool` or an object" in the Decision Outcome for the model and normalisation. Today's boolean configuration is unchanged.

### Deferred to the CLI-consolidation ADR

**3. CLI command group naming.**

`strata audit changes / diff / export` operate on deploy-logs; `audit status` would report on the journal and sinks; `audit resend` forwards to sinks. Three referents under one verb. But CLI naming is something strata settles in *dedicated* consolidation ADRs, not piecemeal inside a feature ADR — ADR-0060, superseded by ADR-0062, dissolved the overlapping `env` group into `deploy`/`rollout`/`sln` on exactly this reasoning. So the audit command renaming is **handed to the forthcoming deploy-service CLI-consolidation ADR**, folded in with the other renames it will already be making, rather than decided here.

What this ADR does pin is the constraint that outlives any renaming: whatever these commands end up called, they **operate on the local data available in `.strata`** and nothing else. `audit status` reads the resolved `spec.audit` and the on-disk journal; `audit changes / diff / export` read `.strata/deploy-log`; `audit resend` replays from the deploy-log to sinks. None of them requires a live deployment or a network round-trip to answer, so they stay usable offline regardless of what the consolidation ADR names them.

### Settled: no negation in `sink.events`

**4. Settled: no.** `sink.events` stays a plain inclusion list — `None` for "everything the gate admits", or an exact list of event types to include. No `["*", "!secret.accessed"]` exclusion syntax.

The reasoning is the same one that rejected Option C for the gate itself: a negation operator is the first step toward a predicate language, and Option C was rejected precisely because strata already has a policy engine (ADR-0006) for that need. An inclusion-only list keeps `sink.events` readable at a glance and keeps the validation in "Why two filters, and how they stay consistent" simple — a named event is either wanted or it is not, with no operator precedence to reason about. If a genuine need for exclusion emerges later, it is additive: `sink.events` can grow a negation syntax, or route through the policy engine, without breaking any inclusion list written today.

### In-process retry for transient delivery failures — settled

**5. Settled: `forward()` retries transient failures in-process; `strata audit resend` is the backstop, not the primary mechanism.**

`strata audit resend` requires someone — or something — to notice a delivery failure and re-invoke the CLI afterwards. That works for an operator investigating a SIEM outage. It does not work for a CI/CD pipeline: a pipeline runs `strata deploy run`, the run completes, the process exits, and nothing re-invokes `strata audit resend` on its behalf later. A transient DNS blip or a 503 from the SIEM at the moment of the event is otherwise **permanent** for that record unless a human happens to go looking — which contradicts the "best-effort delivery, but never silent" driver as much as a silent failure does.

So `forward()` adds a second, bounded retry layer on top of the one `_post_json` already gives `webhook` and `syslog`:

- **Scope:** only errors classified as transient — connection errors, timeouts, and 5xx/429 responses. 4xx (other than 429) fail immediately; a bad credential or a malformed payload will not be fixed by retrying it.
- **Bounds:** a small fixed number of attempts (e.g. 3) with exponential backoff, capped at a low total wall-clock budget. This runs synchronously inside the command that raised the event — `deploy run`, `build run`, etc. — so the budget must stay short enough that a flaky sink cannot meaningfully stall a deployment. This is a *retry*, not a queue: strata does not gain a background worker or persistent outbox from this decision.
- **Exhaustion:** if all attempts fail, the event is logged at `warning` (already decided above), counted in the command's `messages[]`, and left for `strata audit resend` to recover later — resend remains necessary for outages that outlast the retry budget, it just stops being the only thing standing between a transient blip and a permanently lost record.
- **Journal is unaffected:** the journal write happens before any sink is attempted and is not retried or blocked by sink retry — a slow or failing sink must never risk the local record.

This resolves in favour of yes to "does `forward()` add a second layer" from the two options the topic originally posed. The unconditional case for it is the pipeline scenario: without it, every transient network blip during an unattended run becomes a silent, permanent gap in exactly the runs — CI/CD deployments — this ADR is most concerned with capturing.

### On-demand secret resolution for resend/export — scoped to authentication only

**6. Settled: yes, `strata audit resend` and `strata audit export` resolve `${secret:...}` / `${var:...}` on demand — but only for sink `authentication` fields, never for event payload content.**

These commands run outside a deployment and have no `ResolvedValues` sitting around, so without on-demand resolution they could never reach a credential-backed sink at all — that would make `resend` unable to do the one thing it exists for. So they gain the ability to call the shared resolver (the same `${secret:KEY}` / `${var:KEY}` promoted util from "Secret-bearing fields resolve through the store") against the secret store directly, scoped narrowly to the `authentication` block of the integration being connected to. That resolution exists purely to **authenticate the connection** — the HTTP `Authorization` header, the Splunk HEC token, the syslog TLS credential.

What it explicitly does **not** do is touch the event payload being resent. That distinction is not a convenience carve-out; it reflects an invariant that already follows from the rest of this ADR: **audit records never contain secret material in the first place.** The journal and deploy-log store event metadata — who, what, when, outcome, correlation key — never credentials, so there is nothing secret-shaped in `data.strata` to resolve or to leak. `secret.accessed` (the domain event from the class table above) records the *fact* that a secret was read — key name, actor, timestamp — never its value. So "resolve to connect, not to send" is not a special rule invented for resend; it is what the existing payload contract already guarantees, made explicit here because resend is the one command that could otherwise be tempted to resolve too much.

Concretely: `resend` reads a stored event from the deploy-log, resolves the target sink's `authentication` fields on demand to open the connection, and forwards the **unmodified, already-secret-free** payload. If resolution of the authentication fields itself fails (secret store unreachable, key not found), that sink's resend fails with a clear error naming the sink and the missing reference — the same exit-code-3 diagnostic discipline as everywhere else in this ADR — and other sinks are attempted independently.

### Genuinely open — safe to defer

None. All topics raised in this section have been settled above.

### Sequencing note

With the envelope settled, ADR-0065 is unblocked — its database schema can be derived from the CloudEvents + ECS shape rather than waiting. ADR-0064 needs a small amendment: its `meta.recorded_at` and `meta.execution_id` become duplicates of CloudEvents `time` and ECS `labels.execution_id` and should be dropped from its own record.

Every topic in this section is now settled or explicitly deferred (topic 3, to the CLI-consolidation ADR). Nothing here blocks implementation.
