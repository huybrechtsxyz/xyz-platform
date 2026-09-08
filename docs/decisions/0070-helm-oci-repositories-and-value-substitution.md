# Fix Helm OCI Chart Repository Support and `${KEY}` Value Substitution

- Status: implemented
- Date: 2026-08-24

## Implementation Notes

**Completed 2026-08-25.** Both bugs fixed in `src/strata/deployers/helm_deployer.py`
only (no changes needed in `helm_builder.py` — the builder's `${KEY}` emission was
already correct, only the deployer's consumption of it was missing). 69/69 tests
passing in `tests/strata/deployers/test_deployers_helm.py` (27 new), 32/32 unchanged
in `tests/strata/builders/test_builders_helm.py`.

- Bug 1: added `HelmModuleTarget.is_oci`, an `oci://` branch in `validate_workspace()`
  (skips alias assignment, builds `chart_ref` directly from the OCI URL), an
  `is_oci` skip in `setup()`'s repo-add loop (with a distinct "N OCI chart(s)
  detected" message), and an `is_oci`-aware lint-skip message in `check()`.
- Bug 2: added `_TOKEN_RE`, `_find_env_tokens()`, `_resolve_token()` (collision
  across secrets/variables/features is fatal, not resolved via precedence),
  `_escape_set_value()`, and `_build_value_overrides()`; wired into `check()`
  (local charts only), `plan()`, and `apply()`, appending `--set-string` args
  after `-f <values_file>`.

> **Update (ADR-0075):** the untyped `${KEY}` shape from Bug 2 (and its
> `env:`-key scoping, ambiguity-is-fatal resolution via `_resolve_token()`)
> has been retired and replaced by the typed `${var:KEY}` / `${secret:KEY}` /
> `${feature:KEY}` syntax shared with `TerraformDeployer`'s backend config —
> see [ADR-0075](0075-unify-terraform-helm-value-expression-syntax.md). The
> OCI chart repository support from Bug 1 is unaffected.

## Context and Problem Statement

Two independent bugs in the Helm builder/deployer path (`src/strata/builders/helm_builder.py`,
`src/strata/deployers/helm_deployer.py`) make `type: helm` modules unusable for any chart
distributed via an OCI registry, and make secret/variable injection silently produce
literal placeholder strings instead of real values in the deployed release. Both were
confirmed by reading the source directly (strata `1.6.1`, also present on `main`), not
inferred from behavior alone.

### Bug 1 — `oci://` chart repositories are not supported

`SourceModel.chart_repository` ([src/strata/models/common_models.py](../../src/strata/models/common_models.py#L277))
explicitly documents OCI refs as valid input: *"Helm chart repository URL or OCI
reference (e.g. `https://charts.goauthentik.io` or `oci://ghcr.io/org/charts`)"*. The
deployer never special-cases the scheme, though:

```python
# src/strata/deployers/helm_deployer.py
def _sanitize_repo_name(url: str) -> str:
    name = re.sub(r"^https?://", "", url)   # oci:// is never stripped
    name = re.sub(r"[^a-zA-Z0-9]", "-", name)
    name = name.strip("-")
    return name[:20]
```

`validate_workspace()` always does:

```python
repo_name = _sanitize_repo_name(chart_repository)
chart_ref = f"{repo_name}/{chart_name}"
```

and `setup()` always calls `helm repo add <repo_name> <chart_repository>` regardless of
scheme. `helm repo add` only understands classic `index.yaml`-based HTTP(S) repos — it
does not work against an OCI registry URL. Since `setup()` discards the `repo add` exit
code (`# Ignore failure — repo may already be registered`), the failure is swallowed
silently, and `plan()`/`apply()` then fail later with `chart_ref` pointing at a repo
alias that was never actually registered.

**Repro:** a module with
`source: {chart_repository: "oci://ghcr.io/some-org/charts", chart_name: "foo"}` →
`strata deploy run` fails at `apply` (`repo <alias> not found`), even though the exact
same `oci://...` ref works fine with a bare
`helm upgrade --install <name> oci://ghcr.io/some-org/charts/foo` on the CLI directly
(Helm 3.8+ supports OCI refs natively, no `repo add` needed).

**Real-world trigger:** immich-app's official Helm chart moved from an HTTP repo to
OCI-only distribution (`oci://ghcr.io/immich-app/immich-charts/immich`) — this is likely
to affect any recently-migrated chart, not just this one.

### Bug 2 — `${KEY}` secret/variable substitution never resolves for Helm modules

`helm_builder.py`'s module docstring claims: *"Secrets and variable/feature references
are emitted as `${KEY}` substitution tokens. The deployer injects real values via
`--set` flags at deploy time."* This does not happen.

`_render_module_artifacts()` writes `env.KEY = "${SECRET_NAME}"` literally into
`values.yaml` for any `ModuleServiceEnvironmentModel` with `secret:`/`var:`/`feature:`
set. At deploy time, `helm_deployer.py`'s `plan()`/`apply()`:

```python
with inject_compose_env(self.resolved_values):
    ...
    args = ["upgrade", "--install", ..., "-f", str(target.values_file), ...]
```

`inject_compose_env()` only sets plain OS environment variables around the subprocess
call — it never generates `--set KEY=value` args, and it never runs any substitution
pass over `values.yaml` before handing it to Helm. Unlike Docker Compose, Helm does
**not** interpolate `${VAR}` inside a values file against the process environment, so
the literal string `${SECRET_NAME}` gets deployed as the actual value.

**Repro:** module with
`services: [{name: x, environment: [{key: DB_PASSWORD, secret: DB_PASSWORD}]}]`,
`type: helm` → after `strata deploy run`, `helm get values <release>` shows
`DB_PASSWORD: ${DB_PASSWORD}` (the literal token), not the resolved secret.

## Considered Options

### Bug 1 — OCI support

- **A. Skip `repo add`/`repo update` for `oci://` sources.** When
  `chart_repository.startswith("oci://")`, don't register a repo alias at all;
  construct `chart_ref` as `f"{chart_repository.rstrip('/')}/{chart_name}"` (the full
  OCI ref) and pass it straight to `helm upgrade`/`helm lint`/`helm pull`. `check()`
  already special-cases registry charts by skipping `lint` (`repo_url is not None`) —
  OCI charts fall into the same bucket without extra code.
- **B. Attempt `helm registry login` + `repo add` translation.** Try to map OCI refs
  onto Helm's classic repo model via some shim. Rejected — Helm itself does not treat
  OCI registries as "repos" in the `repo add` sense; there is no clean 1:1 mapping, and
  this would just reintroduce the same fragility the bug report is about.

### Bug 2 — value substitution

- **A. Emit `--set-string <path>=<value>` per token at `plan`/`apply` time.** Walk the
  rendered `values.yaml` doc for `${KEY}` tokens (matching resolved variable/secret/
  feature names) and emit a corresponding `--set-string` flag per match, using values
  already available via `self.resolved_values`. Keeps the on-disk `values.yaml`
  secret-free (as the builder intends) since the real value only ever appears as a
  process argument passed to `helm`, not written to disk. `--set-string` also avoids
  Helm's YAML-type-coercion surprises (e.g. a secret value that looks like `"true"` or
  `"123"` being parsed as bool/int instead of string).
  - Trade-off: secret values become visible in the OS process list (`ps`/task manager)
    for the lifetime of the `helm` subprocess — same exposure class already accepted by
    `inject_compose_env`/`inject_tf_vars` for other deployers, so this does not
    introduce a new risk category, only extends secret exposure from process env to
    process args.
- **B. Render a temp, substituted copy of `values.yaml` at deploy time and delete it
  after `helm upgrade` completes.** Keeps `strata build run` output secret-free
  (substitution happens only at deploy time), but requires careful temp-file lifecycle
  handling (crash-safe cleanup, correct permissions, avoiding leaving secrets on disk if
  the process is killed mid-run) that the codebase doesn't currently have a pattern for
  in this deployer.
- **C. Do nothing / require chart-native secret handling (e.g. External Secrets
  Operator).** Rejected — contradicts the documented behavior in the builder's own
  docstring and leaves the feature silently broken for any user who takes the
  docstring at face value.

## Decision Outcome

Chosen for Bug 1: **Option A** — special-case `oci://` in `_sanitize_repo_name`'s
caller (or an equivalent scheme check in `validate_workspace()`/`setup()`), skipping
`repo add`/`repo update` entirely for OCI sources and building `chart_ref` directly
from the full OCI URL.

Chosen for Bug 2: **Option A** — emit `--set-string` flags derived from
`self.resolved_values` for each `${KEY}` token found in the rendered values file, at
`plan()`/`apply()` time only (never at build time), because it avoids introducing new
temp-file lifecycle/cleanup responsibility in the deployer and reuses the existing
argument-injection pattern (`inject_compose_env`/`inject_tf_vars`) already established
for other deployers in this codebase.

### Consequences

- Good: `type: helm` modules work against OCI-only chart distributions (e.g.
  immich-app) without any workaround.
- Good: `${KEY}` tokens in Helm values actually resolve to real secret/variable/feature
  values at deploy time, matching the behavior already documented (and already relied
  upon by users following the docstring).
- Good: `strata build run` output remains secret-free — substitution only happens as
  process arguments at deploy time, never written back to `values.yaml` on disk.
- Bad: `setup()`/`check()` need an explicit scheme branch, adding a small amount of
  branching complexity to `helm_deployer.py`.
- Bad: secret values become visible in OS process listings for the duration of each
  `helm` subprocess call (same exposure class as existing `inject_tf_vars` usage for
  Terraform, not a new risk category for this codebase).
- Bad: `--set-string` values must be matched against `${KEY}` tokens present in the
  rendered values doc — a token whose key doesn't resolve (typo, deleted secret) needs
  explicit handling (fail loudly rather than silently leaving the literal token in
  place, which is exactly today's silent failure mode).
- Bad: the `${KEY}` token format carries no type prefix, so a token cannot be traced
  back to whether it came from `var:`, `secret:`, or `feature:` — if the same name is
  declared in more than one of a module's `references.variables` /
  `references.secrets` / `references.features` (nothing in the schema prevents this),
  resolution is ambiguous and must fail rather than guess, adding another fatal-error
  case beyond plain "not found".

## Design

### Bug 1 design — OCI-aware chart resolution

Add an `is_oci: bool` field to `HelmModuleTarget` rather than overloading `repo_url`
(setting it to `None` for OCI would break `check()`'s existing
`target.repo_url is not None` → "skip lint, it's a registry chart" branch, which must
keep treating OCI charts as registry charts too — they still can't be `helm lint`ed as
a local directory).

```python
@dataclass
class HelmModuleTarget:
    ...
    repo_url: Optional[str]      # unchanged — set for BOTH classic and OCI registry charts
    repo_name: Optional[str]     # None for OCI (no alias is ever registered)
    is_oci: bool = False         # NEW — True when chart_repository starts with "oci://"
```

`validate_workspace()` branches on scheme when resolving `chart_ref`:

```python
if chart_repository:
    if chart_repository.startswith("oci://"):
        chart_ref = f"{chart_repository.rstrip('/')}/{chart_name}"
        repo_name = None          # no alias — helm resolves oci:// refs natively
        is_oci = True
    else:
        repo_name = _sanitize_repo_name(chart_repository)
        chart_ref = f"{repo_name}/{chart_name}"
        is_oci = False
    repo_url = chart_repository
else:
    chart_ref = str(deployment_build_path / ns_name_str / module_name)
    repo_url = repo_name = None
    is_oci = False
    chart_version = None
```

`setup()` only collects `(repo_name, repo_url)` pairs — and only calls `helm repo add` —
for non-OCI targets:

```python
for target in self._helm_modules:
    if target.is_oci:
        continue  # OCI refs need no repo alias; helm resolves them natively
    if target.repo_url and target.repo_name and target.repo_name not in seen:
        seen[target.repo_name] = target.repo_url
```

`check()`'s existing `if target.repo_url is not None:` skip-lint branch needs no change
(OCI targets still have `repo_url` set) — only its message is worth splitting for
clarity: `"(OCI chart — lint skipped; use plan for dry-run)"` vs the existing
`"(registry chart — lint skipped; use plan for dry-run)"`, using `target.is_oci` to pick
the wording.

`plan()`/`apply()` need **no changes** — they already just pass `target.chart_ref`
straight to `helm upgrade`, and a full `oci://ghcr.io/org/charts/foo` ref is a valid
`helm upgrade` chart argument on its own (Helm resolves OCI refs without a registered
repo, only requiring `helm registry login` first for private registries — out of scope
here, same as classic repos requiring credentials already aren't handled by this
deployer today).

### Bug 2 design — deploy-time `${KEY}` → `--set-string` injection

New private helpers in `helm_deployer.py` (colocated like the existing
`_sanitize_repo_name`, since there is a single caller module):

```python
_TOKEN_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")  # whole-string match only —
                                                               # mirrors how the builder
                                                               # emits tokens (never as a
                                                               # substring of a larger value)

def _find_env_tokens(values_doc: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Return (dotted_path, token_key) for every ${TOKEN} leaf found *only* under
    each top-level entry's 'env' sub-dict (values_doc[entry]['env'][k] == '${TOKEN}').

    Deliberately NOT a full-tree walk of values_doc. helm_builder.py only ever emits
    ${KEY} tokens under entry['env'] (from svc.environment var:/secret:/feature:
    refs) — module.spec.configuration and svc.configuration are raw, user-authored
    pass-through values merged elsewhere in the same doc. Walking those too would
    risk matching a user-typed '${...}'-shaped string (or content from a raw
    values.yaml brought in via spec.files) that was never meant as a strata
    substitution token, and forcing it through resolution/failure it doesn't need.
    dotted_path uses Helm --set dot notation, e.g. 'myservice.env.POSTGRES_PASSWORD'."""
    ...

def _resolve_token(token: str, resolved: ResolvedValues) -> Tuple[Optional[str], Optional[str]]:
    """Look up a token name in resolved.secrets, resolved.variables, and
    resolved.features. Returns (value, None) if the name appears in exactly one
    namespace, (None, error_message) if it appears in none OR in more than one.

    IMPORTANT: unlike an earlier draft of this design, this is NOT a precedence
    order (secrets-win-over-variables-etc). The builder emits ${KEY} with no type
    prefix, so a token's origin (var: vs secret: vs feature:) is not recoverable
    at deploy time. ModuleReferenceModel.variables / .secrets / .features
    (module_model.py) are three independent Optional ref lists with no
    cross-namespace uniqueness constraint — nothing stops the same name (e.g.
    'DB_PASSWORD') from being declared as both a variable and a secret. Silently
    picking one via precedence could deploy the wrong value with no error at all,
    which is worse than today's bug (today's failure is at least visible as the
    literal token). A same-name collision across namespaces is therefore treated
    as an unresolved/ambiguous token — same fatal path as 'not found'. Feature
    booleans render as 'true'/'false'."""
    ...

def _escape_set_value(value: str) -> str:
    """Backslash-escape characters with special meaning in Helm's --set
    mini-language (\\, ',', '.', '=', '{', '}', '[', ']') so secret values
    containing them survive as literal strings instead of being parsed as
    additional --set assignments or nested paths. Order matters: escape '\\'
    first, before any other character, or characters escaped later would have
    their own backslash re-escaped."""
    ...

def _build_value_overrides(
    values_file: Path,
    resolved: Optional[ResolvedValues],
    ns_name: str,
    module_name: str,
) -> Tuple[List[str], List[str]]:
    """Parse values_file, find all ${TOKEN}s under each entry's 'env' dict via
    _find_env_tokens(), resolve each via _resolve_token(), and return
    (['--set-string', 'path=value', ...], error_messages). A token that is
    unresolved OR ambiguous (found in more than one of secrets/variables/
    features) is reported as an error and produces no --set-string arg —
    callers must treat any non-empty error_messages as fatal, not deploy with
    the literal token left in place."""
    ...
```

Integration points — `_build_value_overrides()` is called once per target,
immediately before constructing the `helm` argv, in every step that reads
`target.values_file`:

- `check()` — only reached for local (non-registry, non-OCI) charts, since registry/OCI
  charts already skip `lint` entirely; append the returned `--set-string` args to the
  `lint` command.
- `plan()` — append to the `--dry-run --install` argv, after `-f values_file`.
- `apply()` — append to the `--install` argv, after `-f values_file`.

Placement relative to `-f` doesn't affect correctness (Helm's `--set*` family always
takes precedence over `-f` regardless of argument order), but appending immediately
after `-f str(target.values_file)` keeps the values-related flags visually grouped in
the logged command string.

Failure handling: if `_build_value_overrides()` returns any error messages, the step
returns `(False, messages)` immediately — the same pattern `_run_helm()` already uses —
so a typo'd or deleted secret reference fails the deploy loudly instead of silently
shipping the literal `${KEY}` string, which is the exact failure mode this ADR exists
to close.

`inject_compose_env(self.resolved_values)` around `plan()`/`apply()`/`destroy()` is kept
as-is — it no longer does any of the substitution work (that was always a
misunderstanding baked into the original docstring, since Helm never reads `${VAR}`
from the process environment), but leaving plain OS env vars set around the `helm`
subprocess call is harmless and matches the pattern used by other deployers; removing
it is not required to fix either bug and is left out of scope.

**Known adjacent inconsistency, not fixed by this ADR:** `ModuleServiceEnvironmentModel`'s
own docstring claims `var:` is *"resolved at build time"*, but
`_render_module_artifacts()` in `helm_builder.py` currently emits `var:` as the exact
same `${KEY}` token as `secret:`/`feature:` — i.e. today's code already treats all three
uniformly as deploy-time tokens. The design above preserves that uniform treatment
(resolving `var` tokens via `--set-string` alongside secrets/features) rather than
special-casing `var` to be baked into `values.yaml` at build time, since changing *when*
`var` resolves is a separate, independent decision from fixing the two bugs in this
ADR — flagged here so it isn't mistaken for an oversight.
