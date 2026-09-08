# Unify `${var:}`/`${secret:}`/`${feat:}` Value-Expression Substitution Across Terraform and Helm

- Status: proposed
- Date: 2026-09-08
- Related: [ADR-0007](0007-deployment-state-locking.md), [ADR-0019](0019-configurable-terraform-build-output.md), [ADR-0066](0066-audit-event-routing-policy-model.md), [ADR-0070](0070-helm-oci-repositories-and-value-substitution.md), [ADR-0073](0073-embedded-string-syntax-inventory-and-creep-prevention.md)

## Context and Problem Statement

Strata has two independent, differently-shaped mechanisms for resolving a
provisioner's non-module "plumbing" configuration (state-backend settings,
Helm chart values) against `ResolvedValues` (variables/secrets/features) at
deploy time — neither aware the other exists, and each with a gap the other
doesn't have.

**Terraform** (`spec.provisioners[].backend.configuration`, introduced by
[ADR-0007](0007-deployment-state-locking.md), documented in
[ADR-0019](0019-configurable-terraform-build-output.md)) uses a typed-prefix
syntax, `${var:KEY}` / `${secret:KEY}`, resolved by
`TerraformDeployer._resolve_backend_expr()`
([src/strata/deployers/terraform_deployer.py](../../src/strata/deployers/terraform_deployer.py#L806)):
partial `re.sub` (works embedded anywhere in a string), scans every value in
the whole `backend.configuration` dict, no ambiguity possible (the prefix
names the namespace). But an unresolved key is **silently left as the literal
`${var:KEY}` text** — ADR-0019 records this as deliberate ("Unresolved
expressions are left as-is so backends that accept literal `${...}` strings
… are not broken"), but Terraform's `-backend-config` values are always
opaque plain strings passed straight to the backend provider (e.g. azurerm's
`resource_group_name`) — there is no real case where a backend provider wants
to receive the literal four characters `${var:x}`. In practice this means a
typo'd or undeclared reference is never caught: not at build time (fixed
earlier in this same investigation — see `collect_backend_expr_keys()` in
[src/strata/validators/terraform_input_validator.py](../../src/strata/validators/terraform_input_validator.py)),
and not at deploy time either.

**Helm** (`values.yaml`, introduced by
[ADR-0070](0070-helm-oci-repositories-and-value-substitution.md)) uses a bare,
untyped `${KEY}` syntax, resolved by `HelmDeployer._resolve_token()` /
`_build_value_overrides()`
([src/strata/deployers/helm_deployer.py](../../src/strata/deployers/helm_deployer.py#L104-L266)):
an unresolved *or ambiguous* (same name declared as more than one of
secret/variable/feature — only possible because the syntax carries no type)
key **hard-fails** the deploy step — stricter and safer than Terraform's
behavior. But the match is scoped to dict nodes keyed **literally `"env"`**,
at any depth, and requires an **exact whole-value match** (`^\$\{KEY\}$`) —
a token embedded in a larger string (`"postgres://user:${DB_PASSWORD}@host"`)
or placed anywhere outside an `env:` block (e.g. the documented
`configuration:` escape hatch, or a chart field like `adminPassword` that
isn't nested under `env`) is **never even looked at**, so the literal
placeholder text is silently written into the deployed release with no error
or warning anywhere. There is also no build-time check at all for Helm's
`${KEY}` references — typos are only ever caught (if at all) at deploy time.

Neither gap is hypothetical; both were found and confirmed by direct code
reading in the session that produced this ADR (see the fix already applied to
`terraform_input_validator.py` for the build-time half of the Terraform gap,
and the `_find_env_tokens()`/`_TOKEN_RE` code + its own tests
— `test_ignores_tokens_outside_env`, `test_ignores_partial_token_match` in
[tests/strata/deployers/test_deployers_helm.py](../../tests/strata/deployers/test_deployers_helm.py#L749-L756)
— for the Helm gap).

[ADR-0066](0066-audit-event-routing-policy-model.md) already surveyed this
exact space from the audit/SIEM angle, cataloguing `${secret:KEY}`/`${var:KEY}`
as "Convention C" — "the only one of the three existing conventions that
reaches the secret store rather than the process environment" — and adopted
it as *the* standard for credential-bearing fields on the audit path, while
explicitly stating: *"Converging credential conventions across the whole
codebase is out of scope — it touches a dozen integrations and deserves its
own ADR."* This ADR is that follow-up, scoped specifically to the two
provisioners (Terraform, Helm) that already resolve values from
`ResolvedValues` outside their native input mechanism.

**Explicitly out of scope: Docker Compose.** `ComposeBuilder` also emits bare
`${KEY}` tokens into `docker-compose.yml`, but those are resolved by **Docker
Compose's own native `.env`-file interpolation**, not by strata code — the
bare-token shape is Compose's file format, not a strata convention, and
strata cannot change what a third-party tool interprets. Nothing here touches
Compose.

**Explicitly out of scope: Terraform module inputs.** `variables.tf` /
`TF_VAR_*` / `*.auto.tfvars.json` is a completely separate, already-correct
pipeline (module variables are never expressed via `${var:}` — they're
injected directly by name). Only `backend.configuration` uses the expression
syntax, because Terraform backends must initialize before any module variable
exists. This ADR does not touch the module-input pipeline or its existing
`variables.tf` cross-check (`check_inputs()`).

## Decision Drivers

- **Per the "Introducing a new convention" rule** ([README.md](README.md#introducing-a-new-convention)):
  prefer reuse over invention. A syntax already exists and was already chosen
  as the strata-wide direction by ADR-0066 — the answer here is to generalize
  it, not invent a third shape.
- **One implementation, not copies.** Two independently-written resolvers for
  what is conceptually the same operation (substitute a typed reference into
  a string using `ResolvedValues`) is exactly the pattern the README's
  convention rule exists to prevent.
- **Fail loud on a bad reference, always.** A typo'd or undeclared
  `${var:}`/`${secret:}`/`${feat:}` reference should never silently ship as
  literal placeholder text in a live deployment — for a secret specifically,
  that's a functional failure masquerading as success.
- **No back-compat constraint accepted for this decision.** The premise
  explicitly given for this design: assume no installed base depends on the
  current bare `${KEY}` Helm syntax or on Terraform's current silent-pass-
  through-on-miss behavior.

## Considered Options

### Option A — Leave both as-is

- Good: zero effort, zero risk of regression.
- Bad: does not close either gap found (Terraform's silent-pass-through,
  Helm's env-only/exact-match blind spot); leaves two independently-maintained
  resolvers that can and did drift apart in exactly the ways ADR-0066 already
  warned about for the credential-convention space generally.

### Option B — Fix Helm's scoping/matching in place, keep it a separate bare-`${KEY}` mechanism

Extend `_find_env_tokens`/`_resolve_token` to do a full-tree walk and partial
`re.sub`, but keep the untyped `${KEY}` syntax as its own thing.

- Good: smaller diff; Terraform untouched.
- Bad: a full-tree walk over an **untyped** token is exactly the collision
  risk `_find_env_tokens()`'s own docstring already flags (a chart's own
  runtime-side `${VAR}` envsubst template, or any other legitimate string that
  happens to look like `${SOMETHING}`, would now be misdetected) — the
  scoping-to-`env:` restriction was a real, necessary mitigation *for an
  untyped syntax specifically*. Doesn't fix the ambiguous-name problem either
  (still needs the "declared as more than one of" fallback). Two resolvers
  remain.

### Option C — Unify on Convention C (`${var:}`/`${secret:}`/`${feat:}`), one shared resolver, applied to both Terraform's `backend.configuration` and Helm's whole values document (recommended)

Drop Helm's bare `${KEY}`. One shared, typed-prefix resolver used by both
provisioners' plumbing-config fields; safe to run as a full-tree walk
precisely *because* the prefix removes the collision risk Option B still has
— nothing produces the literal substring `${var:` or `${secret:` by accident
the way a bare `${TAG}` can appear in a third-party chart's own runtime
templating.

- Good: single implementation; typed syntax eliminates the ambiguous-name
  class of error entirely (impossible by construction, not just detected);
  closes both gaps in one motion; completes the direction ADR-0066 already
  committed to and explicitly deferred.
- Bad: breaking change for any Helm module already written against bare
  `${KEY}` (accepted per this decision's stated premise); Terraform backend
  deploys that previously "succeeded" only because an unresolved key silently
  passed through will now hard-fail (arguably were already broken — see
  Consequences).

### Option D — Wrap the expression in ADR-0073's `ExpressionModel` (`kind:` discriminator)

- Good: reuses the general expression-system machinery ADR-0073 built.
- Bad: ADR-0073 already considered exactly this and declined it — its "Scope
  rule" reserves `kind:` for schema positions where **the same field** can
  validly hold more than one *kind* of expression (the problem
  `PathConventionModel.rules` had). `backend.configuration` values and Helm
  values-doc strings only ever hold this one expression kind; adding a
  discriminator there is pure schema churn in the opposite direction —
  ADR-0073 says so directly: the regex kind "remains defined but not yet
  consumed anywhere beyond its precedent (`helm_deployer.py`'s `${VAR_NAME}`
  token, which predates and doesn't itself need migrating to
  `ExpressionModel`)."

## Decision Outcome

Chosen: **Option C**, because it is the only option that removes rather than
patches the structural cause of both gaps (an untyped, per-provisioner,
independently-reimplemented resolver), and because it is explicitly the
follow-up work ADR-0066 already named and deferred.

### The syntax

```
${var:KEY}      -- from spec.variables
${secret:KEY}   -- from spec.secrets
${feat:KEY}     -- from spec.features
```

Bare `${KEY}` (Helm's current syntax) is removed. `${feat:KEY}` is new for the
Terraform side — `WorkspaceIacBackendModel.configuration`'s field description
already documents `${feat:enable_encryption}`
([src/strata/models/workspace_model.py](../../src/strata/models/workspace_model.py#L401))
but `_resolve_backend_expr`'s regex has only ever matched `(var|secret)` — this
ADR makes the documented behavior real instead of fixing the docstring to
match the gap.

### One shared resolver + collector

New functions, alongside `ResolvedValues` in
[src/strata/utils/resolved_values.py](../../src/strata/utils/resolved_values.py)
(the module that already owns `collect_inputs_from_keys`, `inject_tf_vars`,
`inject_compose_env` — the natural home, since both new functions operate
directly on `ResolvedValues`):

```python
EXPR_PATTERN = re.compile(r"\$\{(var|secret|feat):([^}]+)\}")

def resolve_expr_string(value: str, resolved: ResolvedValues) -> Tuple[str, List[str]]:
    """Partial re.sub over `value`. Every match must resolve — an unmatched
    key is always an error (never a silent pass-through). Returns
    (resolved_value, errors)."""

def collect_expr_refs(node: Any) -> Set[Tuple[str, str]]:
    """Recursively walk any structure (dict/list/str) and return every
    (kind, key) pair referenced anywhere — no 'must be nested under a key
    named env' restriction; safe precisely because the kind: prefix makes
    false-positive matches implausible."""
```

`collect_backend_expr_keys()` (added to
[terraform_input_validator.py](../../src/strata/validators/terraform_input_validator.py)
earlier in this same investigation, to fix the immediate build-blocking bug)
is superseded by `collect_expr_refs()` and removed — same primitive, one
implementation, no `(var|secret)`-only special case.

### Terraform side

- `TerraformDeployer._resolve_backend_expr()` / `_build_backend_config()` call
  `resolve_expr_string()`. An unresolved key now aborts the deploy step
  instead of silently passing the literal expression text to
  `terraform init -backend-config`.
- `TerraformBuilder._validate_inputs()`'s backend-key exclusion (added earlier
  this session) switches from `collect_backend_expr_keys()` to
  `collect_expr_refs()` — same behavior, one fewer bespoke function.
- **New build-time check**: every `(kind, key)` collected from
  `backend.configuration` must resolve against a declared
  variable/secret/feature (stage-scoped, reusing the existing
  `_allowed_secret_keys_for_stages()` logic) — reported as a build error. This
  mostly formalizes protection the variables.tf exclusion fix already implies,
  but now also catches a reference to a genuinely undeclared name (today that
  would only surface as a silent pass-through at deploy time).
- The `variables.tf`/`TF_VAR_*` module-input pipeline is untouched.

### Helm side

- `HelmDeployer._build_value_overrides()` walks the **entire** rendered values
  document via `collect_expr_refs()` / `resolve_expr_string()` — not just
  `env:`-keyed dicts — closing both the "outside `env:`" and the
  "embedded/partial match" gaps in one change.
- `_TOKEN_RE`, `_find_env_tokens()`, and `_resolve_token()`'s ambiguous-name
  detection are deleted — ambiguity across namespaces is now impossible by
  construction, since the prefix names the namespace.
- **New build-time check**, mirroring Terraform's: every reference collected
  from the module's rendered values doc must resolve against a declared
  variable/secret/feature, reported as a build error by `HelmBuilder` — Helm
  gains build-time typo detection it has never had.
- `HelmBuilder`'s existing `_validate_helm_values()` (chart `values.yaml`
  key-path check, warn-only) is untouched — a different question (does the
  chart accept this key path at all) answered by a different, already-correct
  mechanism.

### What doesn't change

- Compose's `${KEY}` — Docker Compose's own file format, not a strata
  convention.
- Terraform module inputs (`variables.tf` cross-check, `TF_VAR_*` injection).
- Helm's chart-values key-path warning.

## Consequences

- Good: one resolver, one collector, one syntax for "a provisioner plumbing
  field resolved from `ResolvedValues`" — any future provisioner needing the
  same capability (Bicep parameters, ArgoCD app values, …) reuses this instead
  of inventing a fourth shape.
- Good: closes a real, previously silent, secret-shaped failure mode in Helm
  (deploying a literal placeholder string as a live credential) and a real,
  previously silent, undetected-typo failure mode in Terraform's backend
  config.
- Good: build-time typo detection for Helm value-expressions reaches parity
  with what Terraform module inputs have always had.
- Good: directly completes the convergence work ADR-0066 named and
  deliberately deferred, rather than leaving it unclaimed indefinitely.
- Bad: removes Helm's bare `${KEY}` syntax — a breaking change for any
  existing module YAML written against it. Acceptable only under this
  decision's explicit no-back-compat premise; if this were actually shipped,
  it would need a major-version bump and a migration note, neither of which
  is designed here.
- Bad: a Terraform backend config that happened to "work" only because an
  unresolved `${var:KEY}` silently passed through as literal text (and the
  backend provider tolerated or ignored the garbage value) will now hard-fail
  at deploy time. Judged as surfacing a pre-existing latent misconfiguration
  rather than a real regression, since no legitimate backend provider field
  is meant to receive that literal text — but it is a behavior change for
  whatever was silently limping along before.
- Neutral: `_validate_helm_values()`'s separate warn-only enforcement level is
  untouched; whether to make it configurable/stricter is still open, tracked
  as its own future decision, not folded into this one.

## Remaining Work

<!-- Required while Status is proposed / in-progress / partially-implemented.
     Remove this section once Status becomes implemented. -->

- **Not started.** This ADR records the design only. Implementation phases,
  in order:
  1. Add `EXPR_PATTERN`, `resolve_expr_string()`, `collect_expr_refs()` to
     `resolved_values.py`, with direct unit tests (partial match, multiple
     refs per string, unresolved-key error, all three kinds).
  2. Terraform: switch `_resolve_backend_expr()`/`_build_backend_config()` to
     the shared resolver (fail-loud on unresolved); switch
     `_validate_inputs()`'s exclusion set to `collect_expr_refs()`, removing
     `collect_backend_expr_keys()`; add the new "references must resolve"
     build-time check; add `${feat:}` test coverage.
  3. Helm: switch `_build_value_overrides()` to the shared resolver over the
     whole values doc; delete `_TOKEN_RE`/`_find_env_tokens()`/ambiguous-name
     handling in `_resolve_token()`; add the new build-time "references must
     resolve" check to `HelmBuilder`; migrate/replace the now-obsolete
     `TestFindEnvTokens`/exact-match/env-only test cases in
     `test_deployers_helm.py`.
  4. Docs: update the Helm and Terraform sections of
     [docs/platform/builders.md](../platform/builders.md) (remove bare
     `${KEY}` examples, document the three-kind typed syntax); add a
     cross-link from [ADR-0066](0066-audit-event-routing-policy-model.md) and
     [ADR-0070](0070-helm-oci-repositories-and-value-substitution.md) to this
     ADR; update [ADR-0073](0073-embedded-string-syntax-inventory-and-creep-prevention.md)'s
     expression-system inventory table (the `${VAR_NAME}` row is now the
     shared, three-kind, two-provisioner mechanism, not Helm-only).
  5. Confirm no other call site depends on Helm's bare-`${KEY}` shape (grep
     `docs/`, `config/*/deploy/` sample workspaces) before removing it.
