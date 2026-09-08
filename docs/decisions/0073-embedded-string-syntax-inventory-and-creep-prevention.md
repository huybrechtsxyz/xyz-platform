# Embedded String-Prefix Syntax — Inventory and Creep Prevention

- Status: implemented — Locator system fully consolidated (`@repo_name/...`, `./`/`../`, `git@`/`scheme://`); Expression system's `path`/`yaml`/`jinja` kinds built and wired; doc migration and the new-convention-justification rule both done (see Decision Outcome)
- Date: 2026-09-01
- Related: [ADR 0070-Helm OCI Repositories and Value Substitution](./0070-helm-oci-repositories-and-value-substitution.md), [ADR 0072-Clarify spec.layers, spec.layering(s), and spec.paths](./0072-clarify-layering-vs-path-convention.md)

## Context and Problem Statement

While researching ADR 0072's `rules:` mechanism (`spec.<field>[*].<attr>` vs. a file-existence
path template, distinguished purely by regex-matching the rule string's own shape — no
`type:`/`kind:` discriminator field), a broader pattern became visible: strata has
accumulated a growing number of **independent, uncoordinated conventions where a plain
YAML string's own shape — a prefix, a delimiter, a small embedded expression — decides how
it gets interpreted**, each invented separately for one specific feature.

None of these are individually wrong — each solves a real, narrow problem, and several
(`@repo_name/...`, `strata://`) are already documented, deliberate, load-bearing
conventions. The concern is the *trend*: every new feature is one uncoordinated decision
away from inventing its own embedded mini-syntax instead of asking "does an existing
convention already cover this, and if not, should this really be a string-shape trick at
all?" Two concrete costs already surfaced in this codebase from that lack of coordination:

1. **Cognitive load compounds silently.** A DevOps author authoring strata YAML doesn't
   read one schema — they implicitly need to know that `@` means cross-repo, `strata://`
   means a structural URI, `${VAR}` means a Helm secret/variable/feature substitution
   token, `spec.x[*].y` means a config membership lookup, and so on — each learned
   ad hoc, from a different doc page or from trial and error, with no single place that
   says "these are all the special string shapes strata recognizes."
2. **Collision risk is real, not theoretical.** `${VAR_NAME}` (ADR 0070's Helm value
   substitution, [`helm_deployer.py`](../../src/strata/deployers/helm_deployer.py#L99))
   deliberately avoids `{{ VAR_NAME }}` specifically *because* `{{ }}` is already Helm's
   own Go-template/Sprig delimiter, and off-the-shelf charts can legitimately contain
   literal `{{ ... }}` text in their default values (see the code comment added directly
   above `_TOKEN_RE` as a result of this research). That near-collision was caught this
   time. Nothing currently prevents the next one.

Also directly related: the `@repo_name/...` convention itself — while a legitimate,
deliberate, documented convention — was independently re-detected via raw
`str.startswith("@")` at roughly 15 call sites across the codebase instead of one shared
predicate. **Fixed within this ADR** (2026-09-03) rather than tracked separately as
originally planned — but it is the same underlying
symptom: a string-shape convention that exists without one canonical, discoverable
definition.

**This ADR did not originally propose fixing or unifying any of this** — it started purely
as an inventory, so any future feature tempted to add "just one more" prefix would have
something to check against. Follow-up work (2026-09-02/03, documented below) went further
than planned: the Locator system's `@repo_name/...`/`./`/`../`/`git@`-normalize issues and
the Expression system's `path`/`yaml`/`jinja` kinds were designed *and* implemented, not just
inventoried. See [Decision Outcome](#decision-outcome) for the final state.

## Inventory — every embedded string-shape convention found in the codebase today

The inventory splits into exactly two systems (confirmed by follow-up research, see below):
a **locator system** (file-based — the string names or classifies an external thing to
resolve) and an **expression system** (the string is evaluated against data to produce a
result). Every convention found falls into exactly one; nothing straddles both.

**Locator system** — the string names or classifies an external thing to resolve (files,
URLs, URIs, format identifiers) — never evaluated against data, no truth value:

| Shape                                                | Meaning                                                        | Where                                                                                                                                                              |
| ---------------------------------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `@repo_name/...`                                     | Cross-repo file reference                                      | [`resolve_path()`](../../src/strata/utils/system.py#L203) + ~15 independent re-detections (separate tech-debt item)                                                |
| `strata://<kind>/<name>[/<child-kind>/<child-name>]` | Durable structural URI to a workspace object (ADR-0034)        | [`strata_uri.py`](../../src/strata/utils/strata_uri.py)                                                                                                            |
| `file://...`                                         | Local-file-based Helm chart repo vs. a remote repo URL         | [`helm_chart_file_collector.py`](../../src/strata/builders/sbom/helm_chart_file_collector.py#L129)                                                                 |
| `git@...` / `scheme://...`                           | Git remote URL vs. local filesystem path                       | [`add_repo_solution_command.py`](../../src/strata/commands/repo/add_repo_solution_command.py#L15) (`_is_local_path()`/`_REMOTE_URL_RE`)                            |
| `./` / `../`                                         | Local relative module source vs. registry/git-hosted reference | [`sbom_utils.py`](../../src/strata/utils/sbom_utils.py#L146), [`terraform_module_collector.py`](../../src/strata/builders/sbom/terraform_module_collector.py#L127) |
| version-string shape                                 | semver / git SHA / OCI digest detection                        | [`sbom_utils.py`](../../src/strata/utils/sbom_utils.py#L18), [`version_service.py`](../../src/strata/services/version_service.py#L322-323)                         |

### Verified use-site inventory (2026-09-03)

Every locator convention traced to its actual call sites, not just one representative example:

**`@repo_name/...` — confirmed exactly 15 independent `str.startswith("@")` sites (real duplication, same job at every site):**

1. [`utils/system.py:204`](../../src/strata/utils/system.py#L204) — `resolve_path()`, the canonical resolver
2. [`services/base_service.py:552`](../../src/strata/services/base_service.py#L552)
3. [`services/deployment_service.py:1687`](../../src/strata/services/deployment_service.py#L1687)
4. [`services/module_service.py:55`](../../src/strata/services/module_service.py#L55)
5. [`services/template_resolver.py:410`](../../src/strata/services/template_resolver.py#L410)
6. [`services/template_resolver.py:450`](../../src/strata/services/template_resolver.py#L450)
7. [`utils/graph.py:85`](../../src/strata/utils/graph.py#L85)
8. [`builders/compose_builder.py:445`](../../src/strata/builders/compose_builder.py#L445)
9. [`controllers/promote_controller.py:1401`](../../src/strata/controllers/promote_controller.py#L1401)
10. [`controllers/guide_controller.py:148`](../../src/strata/controllers/guide_controller.py#L148)
11. [`controllers/graph_controller.py:407`](../../src/strata/controllers/graph_controller.py#L407)
12. [`controllers/diagram_source_controller.py:514`](../../src/strata/controllers/diagram_source_controller.py#L514)
13. [`controllers/diagram_source_controller.py:1089`](../../src/strata/controllers/diagram_source_controller.py#L1089)
14. [`commands/validate/run_validate_command.py:95`](../../src/strata/commands/validate/run_validate_command.py#L95)
15. [`commands/deploy/run_deploy_command.py:380`](../../src/strata/commands/deploy/run_deploy_command.py#L380)

Only #1 is the canonical resolver — the other 14 independently re-detect the same shape.

**`strata://` — already well-consolidated, no action needed:**

- [`utils/strata_uri.py`](../../src/strata/utils/strata_uri.py) — canonical `SCHEME`, `StrataUri` class, `parse_uri()`
- [`services/diagram_service.py:19`](../../src/strata/services/diagram_service.py#L19) — `_CLICK_DIRECTIVE_RE` extracts a `strata://` URI *embedded inside* a `click <id> "strata://..."` directive string. A different context (pulling a URI out of surrounding text), not a re-detection of the scheme itself — legitimate, not duplication.

**`file://` (Helm chart repo classification) — single site, no duplication:**

- [`builders/sbom/helm_chart_file_collector.py:129`](../../src/strata/builders/sbom/helm_chart_file_collector.py#L129) — only usage.

**`git@...`/`scheme://...` — 3 sites, but different purposes, not straight duplication:**

| File:line                                                                                                                  | Purpose                                                            | Shape                                                                   |
| -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------- |
| [`models/repository_model.py:168`](../../src/strata/models/repository_model.py#L168)                                       | **Validate** a field already typed `RemoteType.GITOPS`             | `^(https?://\|git@\|ssh://)` — deliberately narrow                      |
| [`commands/repo/add_repo_solution_command.py:15`](../../src/strata/commands/repo/add_repo_solution_command.py#L15)         | **Classify** an untyped URL as local-vs-remote to derive `type:`   | `^[A-Za-z][A-Za-z0-9+\-.]*://\|^git@` — deliberately broad (any scheme) |
| [`commands/repo/status_repo_solution_command.py:289`](../../src/strata/commands/repo/status_repo_solution_command.py#L289) | **Normalize** `git@host:org/repo` → `host/org/repo` for comparison | transform, not a classifier — see confirmed bug below                   |

**Correction (2026-09-03) — the "worth a sanity check" concern below was wrong, retracted:**
traced what each of #1/#2 actually validates — `repository_model.py:168` validates
`RemoteModel.repository` (deployment `spec.remotes[]`); `add_repo_solution_command.py:15`
classifies a URL to set `SolutionSpecRepositoryModel.type` (`solution.json`'s repo registry),
which has **no URL-format validator at all** (`url: str`, no regex). Different models, never
cross-validated — there is no code path where #2's broader allowlist accepting a URL causes
#1 to later reject it. No fix needed for #1/#2.

**Confirmed real bug instead — FIXED (2026-09-03) — in #3, `_normalize_repo_url()`:** this
function is the actual bridge between the two models (matches a `RemoteModel.repository`
against a locally-cloned repo's real `git remote get-url origin` for `strata repo status`).
Empirically verified `ssh://git@github.com/acme/repo.git` (scheme-based SSH form) did **not**
normalize to the same value as `git@github.com:acme/repo.git` (SCP-form) or
`https://github.com/acme/repo.git`, despite all three identifying the same remote — the
SCP-form rewrite regex only matched at the very start of the string, so a leading `ssh://`
scheme prevented it from firing, leaving `git@` in the result. Impact: `strata repo status`
would silently report a locally-cloned repo as "not linked" if the clone's origin used
`ssh://` syntax while `spec.remotes[].repository` used SCP-form syntax (or vice versa) — a
real false negative, previously untested (only `https://` vs SCP-form was covered). Fixed by
stripping the scheme first, then stripping any leftover `user@` prefix, only applying the
SCP-specific regex when no scheme is present at all. Verified via a new regression test
(`test_scheme_based_ssh_form_equals_scp_form`) that fails on the old code and passes on the
fix; all 16 existing `_normalize_repo_url` tests still pass; ruff/mypy clean.

**`./`/`../` (relative module source detection) — 2 sites, identical duplicated check — FIXED (2026-09-03):**

- [`utils/sbom_utils.py:146`](../../src/strata/utils/sbom_utils.py#L146)
- [`builders/sbom/terraform_module_collector.py:127`](../../src/strata/builders/sbom/terraform_module_collector.py#L127)

Both did `if source.startswith("./") or source.startswith("../")` — same job, copy-pasted, not
shared. Consolidated into a new `is_local_module_source(source) -> bool` predicate added to
`sbom_utils.py` (the domain-cohesive home — `terraform_module_collector.py` already imports
`terraform_module_to_purl` from the same module, so no new cross-module dependency was
introduced). Both sites now call the shared predicate. Verified via existing
`test_builders_sbom_phase1.py`/`test_utils_sbom.py` suites plus 4 new direct unit tests for the
predicate itself; ruff/mypy clean.

**Version-string shape sniffing — 3 regexes, but check different identifier types, not duplicated:**

- [`utils/sbom_utils.py:18`](../../src/strata/utils/sbom_utils.py#L18) — `_SEMVER_RE`
- [`services/version_service.py:322`](../../src/strata/services/version_service.py#L322) — `_GIT_SHA_RE`
- [`services/version_service.py:323`](../../src/strata/services/version_service.py#L323) — `_OCI_DIGEST_RE`

Semver lives only in `sbom_utils.py`; git-SHA/OCI-digest live only in `version_service.py` — no
overlap, each regex checks a distinct identifier shape in a distinct concern (SBOM inventory vs.
version-pointer resolution). Not a duplication problem.

**Net picture before designing a fix:**

| Convention                             | Verdict                                                                                                                                                      |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `@repo_name/...`                       | **Fixed (2026-09-03)** — 15 sites migrated to shared `is_cross_repo_ref()`/`split_repo_ref()`/`local_relative_part()`/`resolve_path()` helpers, per category |
| `./`/`../`                             | **Fixed (2026-09-03)** — 2 sites migrated to shared `is_local_module_source()` in `sbom_utils.py`                                                            |
| `git@.../scheme://...`                 | **Fixed (2026-09-03)** — validate/classify (#1/#2) confirmed as no actual interaction (different models); real bug found and fixed in normalize (#3)         |
| `strata://`, `file://`, version-string | **Fine as-is** — already single-sourced or legitimately distinct concerns                                                                                    |

### Locator system implementation plan: `@repo_name/...` (2026-09-03)

Tracing what each of the 15 `str.startswith("@")` sites actually *does* with the detection
(not just that it detects) splits them into 6 categories — only some are safe to consolidate
onto `resolve_path()` directly:

| Category                                      | Sites                                                                                                                                                                                                                                                                       | Verdict                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A — already correct**                       | [`base_service.py:552`](../../src/strata/services/base_service.py#L552), [`run_validate_command.py:95`](../../src/strata/commands/validate/run_validate_command.py#L95)                                                                                                     | Already delegate to `resolve_path()`. Swap raw `.startswith("@")` guard → `is_cross_repo_ref()` for consistency only.                                                                                                                                                                                                                                                                                                                                     |
| **B — false positives, different convention** | [`module_service.py:55`](../../src/strata/services/module_service.py#L55), [`compose_builder.py:445`](../../src/strata/builders/compose_builder.py#L445)                                                                                                                    | These are `@module/service` cross-**module** dependency syntax, not `@repo_name/...` at all — reuse of bare `@` for an unrelated meaning. No change to logic; add a code comment flagging the distinct convention so future audits don't conflate them.                                                                                                                                                                                                   |
| **C — cosmetic/graph-symbolic**               | [`utils/graph.py:85`](../../src/strata/utils/graph.py#L85), [`graph_controller.py:407`](../../src/strata/controllers/graph_controller.py#L407)                                                                                                                              | Must NOT resolve (slugify for Mermaid ID; keep symbolic in a dependency graph edge). Swap detection → `is_cross_repo_ref()`, keep the special-case behavior unchanged.                                                                                                                                                                                                                                                                                    |
| **D — deliberate "not yet supported" guard**  | [`diagram_source_controller.py:514`](../../src/strata/controllers/diagram_source_controller.py#L514), [`:1089`](../../src/strata/controllers/diagram_source_controller.py#L1089)                                                                                            | Explicitly reject `@` refs today ("Diagram sources cannot resolve @repo/ references yet"). Swap detection → `is_cross_repo_ref()`, keep the early-exit behavior unchanged.                                                                                                                                                                                                                                                                                |
| **E — genuine duplicate reimplementations**   | [`template_resolver.py:410`](../../src/strata/services/template_resolver.py#L410) (`resolve_at_repo_path()`), [`guide_controller.py:148`](../../src/strata/controllers/guide_controller.py#L148) (`resolve_file_path()`)                                                    | Hand-rolled repo_map lookups structurally identical to `resolve_path()`'s `@` branch. Replace both with direct calls to `resolve_path()` — no behavior change, removes ~30 duplicated lines total.                                                                                                                                                                                                                                                        |
| **F — deliberate deferred/partial behavior**  | [`deployment_service.py:1687`](../../src/strata/services/deployment_service.py#L1687), [`promote_controller.py:1401`](../../src/strata/controllers/promote_controller.py#L1401), [`run_deploy_command.py:380`](../../src/strata/commands/deploy/run_deploy_command.py#L380) | All three strip `@repo_name/` and treat the remainder as `work_path`-relative, explicitly commented "Full cross-repo resolution is deferred to a later phase" — NOT swappable to `resolve_path()` (would resolve against the *other* repo's location via `repo_map`, a real behavior change). Consolidate the identical inline `lstrip("@").split("/", 1)[-1]` dance (verified byte-identical semantics across all three) into one shared helper instead. |

**Implemented (first pass):** three shared helpers added to
[`utils/system.py`](../../src/strata/utils/system.py), alongside `resolve_path()` (which now
uses them internally instead of its own inline `@` detection):

- `is_cross_repo_ref(value) -> bool` — the canonical detection predicate (replaces raw
  `str.startswith("@")` for Categories A/C/D).
- `split_repo_ref(value) -> Optional[Dict[str, str]]` — parses into
  `{"repo_name": ..., "rest": ...}` or `None`; does not validate against a `repo_map` (that
  remains `resolve_path()`'s job).
- `local_relative_part(value) -> str` — the Category F fallback helper: strips an
  `@repo_name/` prefix and returns only the trailing relative path, silently discarding the
  repo name. Verified to reproduce all three existing Category F call sites' exact behavior,
  including the bare-`@repo_name`-with-no-slash edge case (returns the repo name unchanged).

**Migrated (2026-09-03) — all 15 sites now consistent, full test suite green (6279 passed, 16
skipped, 0 failed):**

- **Category E** (2 sites) — `template_resolver.py`'s `resolve_at_repo_path()` and
  `guide_controller.py`'s `resolve_file_path()` now both delegate to `resolve_path()`/
  `split_repo_ref()` instead of hand-rolled repo_map lookups. `resolve_at_repo_path()` kept as
  a distinct, non-raising wrapper (callers there treat "unresolvable" as "skip", not an error).
- **Category F** (3 sites) — `deployment_service.py`, `promote_controller.py`, and
  `run_deploy_command.py` now all call `local_relative_part()` instead of independently
  duplicating the same `lstrip("@").split("/", 1)[-1]` dance. Behavior unchanged (deferred
  cross-repo resolution remains deferred — this only removed the triplicated code, not the
  underlying design decision to defer).
- **Categories A/C/D** (6 sites) — `base_service.py`, `graph_controller.py`, and
  `diagram_source_controller.py` (2 sites) now call `is_cross_repo_ref()` instead of raw
  `str.startswith("@")`. `run_validate_command.py` (Category A) already called `resolve_path()`
  directly and was left as-is (no `is_cross_repo_ref()` swap needed there — its own
  `.startswith("@")` check is a cheap pre-guard before a local import, not worth touching).
  `utils/graph.py`'s `slugify_path()` (Category C) was deliberately **left unchanged** — it's
  a `strata.utils` module, and per `docs/platform/utilities.md`'s "no cross-imports between
  utils modules" convention, it cannot import `is_cross_repo_ref` from `strata.utils.system`;
  its one-line `.startswith("@")` check is trivial enough that self-contained duplication is
  the correct trade-off here, not a violation worth fixing.
  **Superseded for Category D specifically** — see "Category D follow-up" immediately below;
  `diagram_source_controller.py`'s two sites went further than a detection swap and now
  actually resolve `@repo/...` refs instead of rejecting them.
- **Category B** (2 sites) — `module_service.py` and `compose_builder.py` unchanged in logic;
  added a `# NOTE:` comment at each site clarifying this `@` is the unrelated
  `@module/service` cross-module convention, so future greps/audits don't conflate the two.

**Category D follow-up (2026-09-03, later same day):** `diagram_source_controller.py`'s two
Category D guards were upgraded from "reject `@repo/...` and skip" to actually resolving the
reference. The controller already imports `SolutionController` (used by its own `repositories`
source) and its sibling `cache_controller.py` already had the exact merge pattern
(`SolutionController.get_repo_map()` + `ConfigurationService.get_remote_map()`, solution names
take precedence) wired through `resolve_path()`. A new cached `_get_repo_map()` helper applies
that same pattern; `_get_resolved_environments()` and `_iter_workspace_refs()` now call
`resolve_path(..., repo_map=self._get_repo_map())` instead of early-exiting on
`is_cross_repo_ref()`. An unregistered repo name still reports a clear per-reference error
(`resolve_path()`'s own `ValueError`), it just no longer blocks *registered* repos too.

**Remaining work in the `@repo_name/...` consolidation — none; fully migrated.** The
`git@.../scheme://...` and `./`/`../` items below were tracked here as separate, not-yet-done
work at the time this section was written; both have since been resolved — see their own
sections above (git@/scheme normalize bug fixed; `./`/`../` consolidated into
`is_local_module_source()`).

**Expression system** — a small language inside the string, evaluated against data to
produce a result (a boolean, a matched value, a set, a substituted string):

| Shape                                             | Meaning                                                                                                                                                         | Where                                                                                                                            |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `spec.<field>[*].<attr>`                          | Config-model membership lookup (query half of ADR-0072's `rules:`)                                                                                              | [`ExpressionModel.query()`](../../src/strata/models/expression_model.py) (`kind: yaml`) — implemented 2026-09-03, see below      |
| `{segment}`                                       | Path-convention placeholder (file-existence half of the same `rules:`)                                                                                          | [`ExpressionModel.check_path()`](../../src/strata/models/expression_model.py) (`kind: path`) — implemented 2026-09-03, see below |
| `${var:KEY}` / `${secret:KEY}` / `${feature:KEY}` | Typed value-expression substitution — shared by Terraform backend config and Helm values (ADR-0075, supersedes the untyped `${VAR_NAME}` Helm-only token below) | [`resolved_values.py`](../../src/strata/utils/resolved_values.py) (`resolve_expr_string()`, `collect_expr_refs()`)               |
| `{{ var }}`                                       | Jinja2 variable (unrelated domain — `strata new --template` scaffolding)                                                                                        | [`template_resolver.py`](../../src/strata/services/template_resolver.py#L24)                                                     |
| `>=`, `<=`, `==`, `!=`, `>`, `<` prefix           | Comparison expression (cost/threshold gates)                                                                                                                    | [`gate_controller.py`](../../src/strata/controllers/gate_controller.py#L57)                                                      |
| `field op value`                                  | Diagram conditional expression                                                                                                                                  | [`diagram_expressions.py`](../../src/strata/utils/diagram_expressions.py#L24)                                                    |

`spec.<field>[*].<attr>` and `{segment}` are the two halves of one mechanism — ADR-0072's
`rules:` dict, where a single value position meant *either* a spec-membership query *or* a
file-existence path template, distinguished (before the 2026-09-03 fix below) only by
regex-sniffing the string's shape (`is_spec_rule()`, now deleted). That dispatch ambiguity —
one schema position, two unrelated meanings — is exactly what the expression system's `kind:`
discriminator (below) was built to fix, and now does. Previously
these two rows were split across both tables here; correctly, both belong in the expression
system (`yaml` kind and `path` kind respectively) since both are evaluated against data, never
used as bare locators.

**Not strata's own convention** (foreign file formats — excluded from concern here):
`ref:` in [`manifest_artifact_collector.py`](../../src/strata/services/manifest_artifact_collector.py#L82)
reads real git plumbing (`.git/HEAD` symbolic-ref format), not a strata-authored marker.

## Research follow-up (2026-09-02): a file-based locator system and an expression system

Follow-up research confirmed the inventory above splits cleanly into exactly the two systems
now reflected in the tables above:

1. **Locator system (file-based)** — `@repo_name/...`, `strata://...`, `file://`/`git@...`/
   `scheme://...` repo-type detection, `./`/`../` relative-source detection, version-string
   shape sniffing. These answer "where is this thing" or "what format is this", not "is this
   true" or "what does this resolve to". This is the system this ADR is **not** proposing to
   change — each convention here stays as-is; only the `@repo_name/...` duplication was fixed
   (see the Locator system sections above).
2. **Expression system** — the subset that needs one interpreter to run against some input and
   produce a result (a boolean, a matched value, a set, a substituted string). This is the
   system this ADR's `ExpressionModel` proposal targets. It decomposes cleanly into exactly
   four *kinds*, each already present in the codebase in some form:

   | Kind    | What it does                                               | Existing precedent                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
   | ------- | ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
   | `path`  | Substitute `{segment}` captures, check file existence      | `path_convention.py`'s `evaluate_file_rule()` — the file-existence half of ADR-0072's `rules:` mechanism                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
   | `yaml`  | JMESPath query against structured config data              | `path_convention.py`'s `resolve_spec_rule()` — the `spec.field[*].attr` half of the same `rules:` mechanism, hand-rolled today instead of using real JMESPath (the existing syntax is already valid JMESPath as-is)                                                                                                                                                                                                                                                                                                                                                  |
   | `regex` | Fixed-shape pattern match/extract, no evaluation semantics | `helm_deployer.py`'s `${VAR_NAME}` token — **deliberately not Jinja**: `{{ }}` is already Helm's own Go-template/Sprig delimiter, so Jinja would collide with literal `{{ }}` text already present in off-the-shelf charts' `values.yaml`. Confirms `regex` must stay a first-class, permanent kind, not a fallback everything migrates off of.                                                                                                                                                                                                                      |
   | `jinja` | Boolean/comparison expression evaluation                   | `diagram_expressions.py`'s `parse_condition()` **already** compiles a closed `field op value` grammar down to a real Jinja expression fragment, executed by the existing `templater.py` environment — proof this pattern already works in this codebase. `gate_controller.py`'s independent `>=`/`<=`/etc. regex+dict comparison evaluator could adopt the same approach via Jinja2's `Environment.compile_expression()` (evaluates a single expression against a context dict, distinct from full template rendering) — not yet done, a smaller separate follow-up. |

   Proposed shape: `ExpressionModel(kind: path|yaml|regex|jinja, expression: str)` — an explicit
   `kind:` discriminator field, per this ADR's own stated principle ("a schema field is
   discoverable, validated, and documented by the model itself; a string-shape convention is
   not"). Immediate concrete scope at proposal time: only `PathConventionModel.rules` (then
   the shape-sniffed dispatch, `is_spec_rule()`'s regex) would adopt `path`+`yaml` kinds —
   **this is what was actually implemented, see "Implemented" below.** `regex`/`jinja` kinds
   are defined for completeness — `jinja` was later wired into `gate_controller.py`'s
   comparison evaluator (see Decision Outcome); `regex` remains defined but not yet consumed
   anywhere beyond its precedent (`helm_deployer.py`'s `${VAR_NAME}` token, which predates and
   doesn't itself need migrating to `ExpressionModel`).

   Efficiency note: whichever kind, the compiled form (`jmespath.compile(...)`,
   `re.compile(...)`, `Environment().compile_expression(...)`) should be built once — at model
   validation time (e.g. a Pydantic `model_validator(mode="after")` storing the compiled object
   in a `PrivateAttr`, excluded from serialization) — and reused for every file/deployment
   checked afterward, since `ConfigurationModel` (and therefore its `spec.paths[].rules`) is
   loaded once per CLI invocation and then evaluated once per file during a workspace scan.
   Recompiling the same expression string per file would be wasted, avoidable work.

   **Update (2026-09-03): this was the original proposal text below — since implemented, see
   the "Implemented" section further down.** `jmespath` was added as a new dependency (it
   wasn't in `pyproject.toml` at proposal time) — small, well-known (AWS/Azure CLI `--query`,
   Ansible `json_query`), and notably runs the exact same `spec.zones[*].name` syntax already
   documented in ADR-0072 unchanged — only the underlying engine moved from hand-rolled to a
   real standard.

   **Scope rule — not every expression needs `kind:`.** `ExpressionModel` solves *shape
   ambiguity*, not "this field is an expression". Add `kind:` only when the *same schema
   position* can validly hold more than one kind of expression, the way `PathConventionModel`'s
   `rules:` dict value can mean either a spec-membership query or a file-existence path
   template today, resolved only by regex-sniffing the string (`is_spec_rule()`). Most other
   expression sites already disambiguate by schema position and don't need it:
   `GateWhenConditionsModel.cost_delta_monthly` is *always* a numeric comparison,
   `spec.style.highlight[].condition` is *always* a `field op value` expression — the field
   itself already tells you everything a `kind:` discriminator would, so adding one there would
   be pure schema churn (over-formalizing), the same "creep" this ADR exists to prevent, just
   pointed the other direction. Those two can still share one compiled-Jinja evaluator
   internally (via `Environment.compile_expression()`) to kill the current code duplication
   between `gate_controller.py` and `diagram_expressions.py` — as a plain typed string field,
   with no `kind:`, no `ExpressionModel` wrapper, and no YAML schema change at all.

## Implemented (2026-09-03) — `ExpressionModel` built and wired into `PathConventionModel.rules`

The `path`/`yaml` kinds are no longer a proposal — fully implemented, tested (6306 passed, 16
skipped, 0 failed full suite), and wired end-to-end:

- **`jmespath>=1.0`** added as a real dependency (`pyproject.toml`), plus `types-jmespath` for mypy.
- **`ExpressionModel`** ([`models/expression_model.py`](../../src/strata/models/expression_model.py)) —
  `kind: path|yaml|regex|jinja` + `expression: str`, compiled once at construction
  (`model_validator(mode="after")` + `PrivateAttr`), with kind-specific methods: `.query()`
  (yaml), `.matches()` (regex), `.evaluate()` (jinja), `.check_path()` (path — delegates to
  `path_convention.py`'s `evaluate_file_rule()` via a lazy import, reusing rather than
  duplicating the substitution logic).
- **`PathConventionModel.rules`** changed from `Dict[str, str]` to `Dict[str, ExpressionModel]`.
  A new `validate_rules_kind()` model validator restricts values to `kind: yaml`/`kind: path`
  only — `kind: regex`/`kind: jinja` in this position now raise a clear `ValueError` at
  YAML-parse time instead of being silently mismatched later.
- **`path_convention.py`**: `is_spec_rule()`, `resolve_spec_rule()`, `_resolve_step()`, and
  `_SPEC_RULE_RE` — the hand-rolled dotted-path walker this whole ADR started from — are
  **deleted**. `evaluate_conventions()`'s dispatch now checks `rule.kind` directly instead of
  regex-sniffing a string. The `yaml` kind branch calls `rule.query(configuration_model.model_dump())`
  and checks membership against the returned list; the dict-aware `spec.custom`/
  `spec.configuration`/`spec.properties` fallback `_resolve_step()` needed (ADR-0072) is now
  handled automatically and more completely by `model_dump()` producing a plain dict tree that
  JMESPath traverses natively — no special-casing needed at all.

**Layering discovery during wiring**: `path_convention.py` (`strata.utils`) importing
`ExpressionKind` from `strata.models.expression_model` at module level broke the `strata.utils
is not allowed to import strata.models` contract (ADR-0003 — `models` sits *above* `utils`, not
below). Fixed by comparing `rule.kind == "yaml"` (a plain string) instead of importing the enum
— `ExpressionKind(str, Enum)` compares equal to its raw string value, so no import is needed at
all for the comparison. Confirmed via `lint-imports`: 0 new violations (2 pre-existing, unrelated
ones remain: `services→controllers`, `commands→server`).

**Test fixture discovery**: `test_path_convention_policy.py`'s `_make_config_model()` previously
built a `MagicMock()` instead of a real `ConfigurationModel` — worked fine against the old
`getattr()`-based walker, but breaks under `model_dump()` (a `MagicMock.model_dump()` doesn't
return a real dict). Rewritten to construct genuine `ConfigurationModel`/`ConfigurationSpecModel`/
`ConfigurationZoneModel` instances. This also surfaced that `spec.environments[*].name` — used
as an example in this ADR's own inventory, in ADR-0072, and in several docs — was **never a real
field** on `ConfigurationSpecModel`; it only ever "worked" in tests because `MagicMock` silently
accepts any attribute. The one test that exercised it (`test_multiple_violations_same_convention`)
was rewritten to validate two segments against the real `spec.zones` field instead. `TestSpecRule`
(direct tests of the now-deleted `is_spec_rule`/`resolve_spec_rule`) was removed — that coverage
now lives in `test_models_expression.py`'s `TestExpressionModelYamlKind`.

**Not yet done**: the ~22 documented `validate:` YAML examples across `docs/config/configuration.md`,
`docs/decisions/0052-path-convention-validation.md`, `docs/decisions/0042-deep-validation-layer-consistency.md`,
and `docs/platform/policies.md` still show the old bare-string shape and need updating to
`{kind: ..., expression: ...}`. Also still using the non-existent `spec.environments[*].name`
example in places — should be corrected to a real field (e.g. `spec.zones[*].name`) while fixing
the shape. The `regex`/`jinja` kinds remain unwired (by design, per the Scope rule above) until
the `gate_controller.py`/`diagram_expressions.py` consolidation is separately tackled.

## Goal

Not to unify or redesign any of the above right now. Only:

1. **One place these are all written down**, so nobody has to rediscover the full list by
   grepping the codebase the way this research did.
2. **A principle for future additions**: before inventing a new embedded string-shape
   convention for a narrow use case, check (a) whether an existing convention above
   already covers the need, and (b) whether a real schema field (an explicit `type:`/
   `kind:` discriminator) would serve better than another shape-sniffed string — a schema
   field is discoverable, validated, and documented by the model itself; a string-shape
   convention is not.
3. **A trigger to actually research consolidation later** — see
   [Decision Outcome](#decision-outcome) — rather than letting this fade back into "13
   independent tricks nobody wrote down."

## Decision Outcome

**Original scope (2026-09-01):** no mechanism change, inventory only — existing conventions
grandfathered as-is.

**Actual outcome (2026-09-03):** went further than originally scoped. The Locator system's
`@repo_name/...`, `./`/`../`, and `git@`/`scheme://` duplication/bugs were fixed (see the
Locator system sections above). The Expression system's `path`/`yaml`/`jinja` kinds were
designed, implemented as `ExpressionModel`, and wired into `PathConventionModel.rules` and
`gate_controller.py`'s comparison evaluator, replacing the shape-sniffed `is_spec_rule()`
dispatch this ADR was originally written to flag (see "Implemented" above). `diagram_expressions.py`
was deliberately left on its own existing Jinja-fragment approach — a different job
(template-embedding vs. immediate evaluation) from `gate_controller.py`'s need, not a gap.
Documentation (`configuration.md`, `policies.md`, ADR-0072) was migrated to the new
`{kind, expression}` shape; historical ADRs `0052`/`0042` were deliberately left untouched.
Whether new conventions need explicit justification was decided: yes, formalized in
[docs/decisions/README.md#introducing-a-new-convention](README.md#introducing-a-new-convention).
Nothing from the original inventory or the follow-up work remains open.
