# strata — Changelog

All notable changes to this project are documented in this file.
This project adheres to [Keep a Changelog](https://keepachangelog.com/) and follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

## [1.9.6] - 2026-09-08

### Fixed

#### **`strata build run` never resolved `spec.extends` (ADR-0039)**

- **Root cause**: `DeploymentExtensionResolver` was only wired into `BaseDeployCommand._before_execute()` and `PlatformValidator` (`validate --deep`) — `BaseBuildCommand._before_execute()` called `DeploymentService.load(...)` directly on the raw leaf file, with no equivalent resolution step. Any field only present on a base file reached via `spec.extends` (`workspace`, `stages`, `locking`, ...) was simply absent, so the build aborted downstream in `DeploymentService.load_deploy_services()` with `"Workspace not found in deployment"` — even though the exact same file passed `strata validate --deep` (which does resolve `extends`) moments earlier.
- **Fix**: new shared `BaseCommand._load_deployment_service_with_extends(file_path, repo_map)` — resolves `spec.extends` when present (`DeploymentExtensionResolver.needs_resolution()`/`.resolve()`), loads and Pydantic-validates the merged result, and returns the validated `DeploymentService` (or `None`, with error(s) appended to `self._errors`). `BaseDeployCommand` and `BaseBuildCommand` both call this now instead of each maintaining their own copy of the resolver block. `BaseBuildCommand` also gained the partial-deployment pre-flight rejection `BaseDeployCommand` already had — a partial base file has no complete workspace/stages target to build against either.
- **Two related swallowed-error bugs found and fixed in the same pass**:
  - Constructing `DeploymentService(path=..., data=merged_extends_data)` directly and calling `.validate()` does not auto-populate `self._errors` the way the `.load()` classmethod does (`service._errors.extend(errors)` after `validate()`) — so a broken `extends` chain's Pydantic errors were silently discarded rather than surfaced. Fixed to match `platform_validator.py`'s already-correct pattern.
  - `DeploymentService.load_deploy_services()`'s `"Workspace not found in deployment"` branch only called `self.logger.error(...)` — never appended to `self._validation_errors` — so `get_validation_errors()` returned `[]` and JSON output showed `{"success": false, "errors": []}` with the real cause visible only on stderr. Fixed to append a real error message.
- **Testing**: new `TestLoadDeploymentService` in `test_commands_base.py` (plain load, extends resolution, broken-chain error, Pydantic-error surfacing) and `TestLoadDeployServicesMissingWorkspace` in `test_services_deployment.py`.

#### **`RemoteModel.deploy_path` was optional but effectively mandatory for gitops remotes**

- **Root cause**: `RepositoryController._resolve_target_path()` falls back to `remote.name`/`remote.repository` when `deploy_path` is unset, and `ConfigurationService.get_remote_map()` silently drops the remote from the map entirely — neither matches where `strata repo add` actually clones the repo (`solution.json`'s own `repos/<name>` convention), producing a `"has not been fetched yet"` error even when the repo genuinely is fetched, just at a different path.
- **Fix**: `RemoteModel`'s `model_validator(mode="after")` now requires `deploy_path` whenever `type == gitops` — fails loud at config-validation time instead of silently misresolving at deploy time. `bundled`/`container` remotes are unaffected (they don't need a stable local checkout path).
- **Verified non-breaking**: every shipped `config/*/config/*.yaml` gitops example (6 configs + 2 scaffold templates) already declared `deploy_path` explicitly. Only test fixtures needed updates (`tests/data/configurations/configuration-standard.yaml`, `test_status_repo_tags.py`, `test_ref_convention_policy.py`) plus one doc example (`docs/config/environment.md`).
- **Deliberately not implemented** (see follow-up issues filed instead): auto-defaulting `deploy_path` from `solution.json` by URL-matching — needs a git-URL normalization utility that doesn't exist yet and is a new cross-registry convention deserving its own ADR; scoping `RepositoryController.ensure_remote_refs()` to only the remotes a deployment actually references, instead of every declared gitops remote — needs dependency-tracking that doesn't exist anywhere in the codebase today.
- **Testing**: new `tests/strata/models/test_models_repository.py`.

## [1.9.5] - 2026-09-08

### Added

#### **Deployment change-reference tracking (ADR-0074)**

- **Problem**: strata already records everything observable from inside the deploying process — actor, timestamps, commit, stages, policy results, lock trail, outcome — but nothing about *why* a production deployment happened. No link to a ticket/work-item/change record, no required justification.
- **Model**: new `ChangeReferenceModel` (`src/strata/models/change_reference_model.py`) — `system` (open string, not an enum), `id`, `reason` (required whenever `--change-id` is supplied), optional `classification`/`title`/`url`, plus `supplied_by`/`supplied_at` provenance. A single reference only — no list; a rollback deploy supplies its own reference rather than auto-inheriting the original.
- **Supplying it**: new `click_change_reference` decorator adds `--change-id`/`--change-system`/`--change-title`/`--change-url`/`--change-classification`/`--reason` (each with a matching `STRATA_CHANGE_*` env var) to `deploy run` and `deploy destroy`. `configuration.spec.change_tracking` supplies workspace-wide defaults (`system`, `url_template`, `id_pattern` regex, an optional `classifications` allowlist).
- **Recording it**: written to `DeploymentManifestSpecModel.change_reference` and `DeployLogModel.change_reference` on every terminal outcome (success, failure, or policy rejection) — not just on success.
- **Enforcing it**: new `change_reference_required` policy (`ChangeReferenceRequiredPolicy`), evaluated at new `deploy_before`/`destroy_before` phases via a shared `BaseDeployCommand._evaluate_preflight_policies()` helper (moved out of `RunDeployCommand` so `DestroyDeployCommand` gets the identical enforcement path). A configured allowlist/pattern mismatch is a `click.UsageError` (exit code 2); a missing reference when the policy is `deny` blocks the deploy before any provisioning starts.
- **Explicitly out of scope (Phase 3, considered and rejected)**: verifying the referenced ticket actually exists/is in the right state. An anonymous HTTP check against most ticketing systems can't reliably distinguish "real ticket" from "typo" (login-page redirects return 200 for both), and verification doesn't belong on the deploy hot path — the same "emit facts, aggregate downstream" principle as ADR-0064. Left as a future server-side (ADR-0065) capability instead.
- **Testing**: new `test_models_change_reference.py`, `test_commands_deploy_change_reference.py`, `test_commands_deploy_change_reference_policy.py`, `test_change_reference_required_policy.py`, plus registry/audit-fixture updates across the existing deploy command test suite.

See [ADR-0074](../docs/decisions/0074-deployment-change-reference.md) for the full design, including the 4 considered options and why per-provider ticketing integrations (Option B) and reusing the existing approval-gate work-item field (Option C) were rejected.

#### **Unified `${var:}`/`${secret:}`/`${feature:}` value-expression syntax across Terraform and Helm (ADR-0075)**

- **Root problem**: Terraform's backend config (`spec.provisioners[].backend.configuration`) and Helm's `values.yaml` each resolved references against `ResolvedValues` with their own independently-written, differently-shaped mechanism — and each had a gap the other didn't. Terraform's `${var:}`/`${secret:}` resolver silently left an unresolved reference as the literal `${var:KEY}` text instead of failing; Helm's bare, untyped `${KEY}` token only matched inside a dict node keyed literally `env`, and only on an exact whole-value match — a token embedded in a larger string, or placed anywhere outside an `env:` block, was silently never looked at and shipped as literal placeholder text (including for secrets).
- **Fix**: one shared resolver, `resolve_expr_string()`/`collect_expr_refs()`/`EXPR_PATTERN` (new, `src/strata/utils/resolved_values.py`), used by both provisioners. Every reference must resolve — an unmatched key is now always a hard error, never a silent pass-through, on both sides.
  - **Terraform**: `TerraformDeployer._resolve_backend_expr()`/`_build_backend_config()` now fail the deploy step loud on an unresolved reference instead of passing through literal text; `TerraformBuilder` gained a build-time check that every backend-config reference resolves against a declared variable/secret/feature.
  - **Helm**: `HelmDeployer._build_value_overrides()` now walks the *entire* rendered values document (not just `env:`-keyed dicts) and classifies each leaf by kind — a leaf referencing a secret (even mixed with `var:`/`feature:` refs in the same string) is resolved and passed as `--set-string` (never written to disk, matching Terraform's existing secrets-never-touch-disk rule); a leaf with only `var:`/`feature:` refs is substituted into a sibling `<stem>.resolved.yaml` file used via `-f` instead. `HelmBuilder` gained a matching build-time check — Helm reaches build-time typo detection parity with Terraform's `variables.tf` cross-check for the first time.
- **Breaking**: removes Helm's old bare `${KEY}` syntax entirely (no back-compat fallback, per the decision's explicit premise). A repo-wide audit of `config/*/stack/*.yaml` found and migrated the one real usage (`config/aws-eks/stack/aws-mod-alb-controller.yaml`, `${EKS_CLUSTER_NAME}` → `${var:EKS_CLUSTER_NAME}`); Docker Compose's own unrelated `${KEY}` `.env` interpolation is untouched.
- **Testing**: new `test_utils_resolved_values_expr.py` for the shared primitives; `test_deployers_helm.py`'s obsolete `TestFindEnvTokens`/`TestResolveToken` replaced with `TestFindExprLeaves`/`TestBuildValueOverrides` (including a leaf mixing `var:`/`secret:` routing as secret-shaped); new `TestHelmBuilderValidateExprRefs`; full suite green (6475 passed).

See [ADR-0075](../docs/decisions/0075-unify-terraform-helm-value-expression-syntax.md) for the full design, including the rejected alternatives (fixing Helm's scoping in place while keeping it untyped; wrapping the expression in ADR-0073's `ExpressionModel`) and the escaping-semantics rationale for the split-by-kind output mechanism.

## [1.9.4] - 2026-09-07

### Fixed

#### **Windows CLI crash — missing `colorama` dependency**

- Every CLI invocation on Windows crashed with `SystemError: ConsoleRenderer with colors=True ... requires colorama`. `colorama` was never declared as a dependency. Added as a Windows-only dependency; the console renderer also now falls back to uncolored output instead of crashing if it's still missing at runtime.

#### **Partial deployments never checked `environments[]` file existence (#279)**

- **Root cause**: `spec.partial: true` deployments intentionally skip full Phase 2 semantic validation (they're base files, not complete deploy targets, and may legitimately omit required leaf-only fields like `workspace`). That skip also meant any `environments[]` entry already present in the partial file — a straightforward file reference, unrelated to leaf-only requirements — was never checked for existence, so a typo'd or moved environment file went undetected until some later leaf deployment extending the partial file was built or deployed.
- **Fix**: new `DeploymentService.check_environment_refs_exist()` — a lightweight, existence-only check (reusing the existing `_validate_file_refs()` helper) that runs in `PlatformValidator` specifically for the partial-file branch, under `--deep` only (same scope as Phase 2). It does not reject the deployment for being partial and runs no other cross-reference checks, just resolves each `environments[].file` (through the merged config/solution repo map) and reports any missing file as `PARTIAL_ENVIRONMENT_FILE_NOT_FOUND`.
- **Side fix bundled in the same PR — layer segment/filename ambiguity**: `resolve_layers()`/`LayerAgreementPolicy` derive convention segment *values* from a deployment file's path via `match_pattern()`. When a file sits exactly one directory level shallower than its convention's full segment depth, its own filename lines up with the last placeholder position instead of being an absorbed trailing part, and got silently captured as if it were that segment's real directory value. New `_dir_only()` helper in `path_convention.py` strips the filename before matching for segment-*value* derivation only — Level 1 "which convention applies" matching is untouched, since a file's own name legitimately satisfying a convention's overall shape (e.g. a single-segment convention matching a file at the workspace root) is a structural match, not a segment value.
- **Testing**: new coverage in `test_services_deployment.py`, `test_services_deployment_environment_only.py`, `test_utils_resolve_layers.py`, `test_layer_agreement_policy.py`, and `test_validators.py`.

## [1.9.3] - 2026-09-04

### Fixed

#### **Unified build-artifact path resolution across every builder/deployer pair (ADR-0071)**

- **Root problem**: every builder/deployer pair independently re-derived the same per-namespace/per-module/per-provisioner build output path shape, with nothing enforcing agreement between the two sides — a change to one side's path shape would only be discovered when the other side failed to find its file at runtime. Found while writing ADR-0070.
- **Impact Analysis (2026-09-03)** traced every pair against the actual code: Terraform/Bicep/Ansible already shared `SolutionController.get_provisioner_path()`; Helm, Compose, and Sync (argocd/flux) each independently re-derived their own shape with no shared helper.
- **Fix — Option B**: extended the existing `get_provisioner_path()` pattern with three new `SolutionController` methods — `get_module_build_path()` (Helm), `get_namespace_compose_path()` (Compose), `get_sync_output_path()` (Sync/argocd/flux) — each a single source of truth called from both the builder and deployer side of its pair. All 7 duplicated call sites updated. `helm_builder.py`'s `_copy_namespace_module_files()` turned out to share the same `module_dir` shape as its sibling `_build_namespace()` and needed the same fix, not just the one originally scoped site.
- **Testing**: 18 new tests added across `SolutionController` (direct coverage for all 4 canonical path helpers, including `get_provisioner_path()` itself — previously untested), and each builder/deployer pair. `SyncBuilder`/`SyncDeployer` had zero test coverage of any kind before this pass — new test files created from scratch.

#### **Bicep provisioners had no builder — `strata build run` silently produced nothing for them**

- **Symptom**: `terraform_builder.py`'s provisioner-copy logic is explicitly gated on `provisioner == "terraform"`; there was no equivalent for `bicep` anywhere in `src/strata/builders/`. `bicep_deployer.py` already expected a builder to have copied `.bicep` files into the build output (its own error message said *"Run 'strata build run' first to copy IaC artefacts to the build folder"*), but nothing ever did.
- **Compounding bug found alongside**: `bicep_deployer.py`'s own `solution_controller is None` fallback used a third, independently-different path shape (`self.build_path / self._iac_model.name`) — not `get_provisioner_path()`'s shape, not even the pre-fix `terraform_deployer` shape, and didn't descend into the deployment's build path first. Unreachable in real CLI usage (a real `SolutionController` is always constructed), but a live landmine for tests/direct/library use.
- **Fix**: new `src/strata/builders/bicep_builder.py` (`BicepBuilder`), modeled on `AnsibleBuilder._copy_provisioner_source()`'s shape (simpler than Terraform's — no tfvars validation, no lock-backend complexity), wired into `run_build_command.py`'s phase list as `_execute_bicep_build()`. `bicep_deployer.py` gained a `_get_working_dir()` method mirroring `terraform_deployer`'s corrected fallback shape (`target_path` → else `source_path`, raising `ValueError` when neither is set instead of silently fabricating a path).
- **Testing**: 13 new tests in `test_builders_bicep.py`, 4 in `test_bicep_deployer.py` covering the corrected fallback and an end-to-end `validate_workspace()` regression test.

#### **`source.reference` (git-ref pinning) silently ignored by Ansible and Helm**

- **Root cause**: `SourceModel.reference` is documented as generic ("only valid for git-based sources"), not Terraform-specific, but `TerraformBuilder._extract_source_at_ref()` was the only place that actually implemented pinned-ref extraction via `git archive`. `AnsibleBuilder._copy_provisioner_source()` had zero references to `.reference` anywhere — declaring `reference: v1.2.0` on an Ansible provisioner's source validated successfully and was silently ignored, copying whatever the current working-tree checkout happened to be instead of the pinned ref. The same gap existed one level down in `helm_builder.py`'s local (non-registry) chart copy, at the **module** level (`ModuleModel.spec.source`).
- **Fix**: `_extract_source_at_ref()` had no Terraform-specific coupling at all (pure git/path logic using only `self._messages`, `shutil`, and `GitIntegration`), so it moved unchanged from `TerraformBuilder` to `BaseBuilder`. `AnsibleBuilder` and the new `BicepBuilder` both call the shared method; `HelmBuilder`'s local-chart copy branch was restructured into a 3-way split (reference / dry-run / normal copy) to do the same.
- **Latent test-authoring trap found and fixed along the way**: the pre-existing `TestHelmBuilderLocalChartTemplates._build_with_local_chart` test helper never set `src.reference` on its `MagicMock()` source, so it defaulted to a truthy `MagicMock` instance — meaning those regression tests were silently being routed through the newly-added reference-extraction branch instead of the plain-copy branch, without ever failing, purely by coincidental identical output (repo_root wasn't a real git repo, so the reference branch's own fallback produced the same result). Fixed by explicitly setting `src.reference = None`.

#### **`WorkspaceIacModel.backend`/`.output` had no type restriction, unlike `.properties`**

- **Symptom**: `validate_provisioner_fields()` already restricted `.properties` to Ansible-only, raising a clear validation error otherwise. `.backend` (*"Backend configuration for state storage"*) and `.output` (*"Build output profile for Terraform provisioners"*) had no equivalent restriction, despite both field descriptions naming Terraform specifically — declaring either on a `bicep`/`ansible`/`compose`/`helm` provisioner validated successfully and was then silently ignored everywhere (no lock ever acquired, no overlap check ever ran, no tfvars emission profile ever applied).
- **Fix**: extended `validate_provisioner_fields()` with the same restriction pattern for both fields, mirroring the existing `properties` check's exact error-message style.
- **Bonus finding while writing tests**: the same validator's `is_sync` check (`self.provisioner in {str(t) for t in _SYNC_PROVISIONER_TYPES}`) was independently and unrelatedly broken. `str()` on a `class X(str, Enum)` member returns the enum's default `__str__` (`"ProvisionerType.ARGOCD"`), not `.value` (`"argocd"`) — so the set being compared against never contained anything a real provisioner string could match, making `is_sync` **always** `False`. `source` was therefore wrongly required even for `argocd`/`flux` sync provisioners, contradicting the field's own documented "optional for sync provisioner types" behavior. Confirmed live via `WorkspaceIacModel.model_validate({"provisioner": "argocd", "source": None, ...})` — realistic YAML-shaped input, not just an artifact of enum-typed test construction. Fixed by comparing against `{t.value for t in _SYNC_PROVISIONER_TYPES}` instead.

#### **`strata deploy lock status`/`release`/`history` could act on the wrong Terraform provisioner's lock**

- **Symptom**: `lock_deploy_command.py` had its own module-level `_resolve_lock_backend(deployment_service, work_path)`, independent from `BaseDeployCommand._resolve_lock_backend(stages)` (used by `deploy run`). The command-file version iterated **all** workspace provisioners and returned the backend of the **first** Terraform provisioner with one — no stage awareness. `BaseDeployCommand`'s version correctly looks up the provisioner matching the deployment's actual `stage.provisioner`. In a workspace with more than one Terraform provisioner (e.g. a `networking` stage on one backend, a `compute` stage on another), `deploy run` locked the correct one, but `deploy lock status`/`release`/`history` would silently report on — or force-release — whichever Terraform provisioner happened to come first in `spec.provisioners`, regardless of which stage was actually asked about.
- **Fix**: deleted the duplicate module-level function; all three `Lock*Command` classes now call the inherited, stage-aware `BaseDeployCommand._resolve_lock_backend()`, passing the deployment's own stages.
- **Testing**: 6 new regression tests (`TestResolveLockBackendMatching`), including a test proving the exact scenario the bug produced (`test_ignores_earlier_unrelated_terraform_provisioner`) — this method had zero direct test coverage before.

#### **Provisioner-type comparisons mixed string literals and the `ProvisionerType` enum inconsistently**

- Roughly half of `.provisioner ==`/`!=` comparisons in the codebase used the `ProvisionerType` enum; the other half compared against a bare string literal, functionally equivalent today only because `ProvisionerType(str, Enum)` mixes in `str`, but meaning a typo'd literal would silently never match instead of failing loudly the way an unchecked attribute access on the enum would. All 7 bare-string sites (5 in `terraform_builder.py`, 2 in `ansible_builder.py`) converted to `ProvisionerType.TERRAFORM`/`ProvisionerType.ANSIBLE`.

See [ADR-0071](../docs/decisions/0071-unify-build-artifact-path-construction.md) for the complete investigation, including the full provisioner-type audit addendum (verifying `script`/`argocd`/`flux` correctly have no builder or path-helper dependency, by design).

## [1.9.2] - 2026-09-02

### Fixed

#### **Environment `properties`/`custom` shallow-merge bug**

- **Symptom**: `EnvironmentService.merge_envfiles()` merged `spec.properties`, `spec.custom`, and `spec.overrides.properties` with plain `dict.update()` (shallow). When a later environment file in the chain (e.g. `prd.yaml` after `base.yaml`) redeclared a nested object under one of these sections to change a single key, the entire object from the earlier file was replaced — sibling keys not repeated in the later file silently vanished from the merged `EnvironmentModel`.
- **Inconsistency**: `TerraformBuilder._resolve_merged_properties()` already deep-merges the same sections across the workspace → environment → deployment layers via a local `_deep_merge()` closure. This meant the merged `EnvironmentModel` (what an operator inspects via `strata values list`) and the final rendered `.tfvars.json` (what Terraform actually sees) could disagree on the same nested object — a key dropped during environment-file merging could reappear during Terraform-layer merging if it also existed at the workspace level.
- **Root cause**: two independent merge implementations for what should be one semantic — "compose layered dict data" — with no shared utility, so they drifted (one shallow, one deep) without anyone deciding that on purpose.
- **Fix**: Added `strata.utils.dict_merge.deep_merge()` — a standalone, self-contained recursive merge function (nested dicts merge key-by-key; any other conflicting value is replaced wholesale). `EnvironmentService.merge_envfiles()` now uses it for `properties`, `custom`, and `overrides.properties` instead of `dict.update()`. `TerraformBuilder._resolve_merged_properties()` now imports the same shared function instead of defining its own local closure, so both call sites are guaranteed to stay consistent.
- **Docstring updated**: `merge_envfiles()`'s per-section semantics table changed from "shallow `dict.update`" to "deep merge" for the three affected sections, with a note pointing at the shared `deep_merge()` used by both merge points.
- **Testing**: New `tests/strata/utils/test_utils_dict_merge.py` covers flat override, nested recursive merge, mismatched-type wholesale replacement, list replacement (not concatenation), non-mutation of inputs, and empty-base/empty-override identity cases. Added a nested `integration_config` fixture to `tests/data/environments/environment-merge-{base,override}.yaml` and a regression test (`test_nested_properties_deep_merged_not_replaced_wholesale`) proving a base-only nested key survives a later file partially overriding the same object.
- **`ConfigurationLoader.deep_merge()` intentionally left as-is** — per the `docs/platform/utilities.md` "no cross-imports between utils modules" convention, it keeps its own self-contained recursive-merge implementation rather than importing the new shared function; behavior is identical, just not code-shared, to avoid introducing a utils→utils dependency.

#### **Same shallow-merge bug in resource/module environment overrides — comments claimed "Deep merge", code did shallow merge**

- **Symptom**: `DeploymentService.apply_environment_overrides()` applied `spec.environments[].overrides.resources[].configuration`/`custom` and `overrides.modules[].configuration` with `workspace_resource.configuration.update(...)` / `.custom.update(...)` / `target_module.configuration.update(...)` — plain shallow merge. Two of the three call sites had an inline comment reading `# Deep merge configuration (override wins)` / `# Deep merge custom (override wins)` that was simply incorrect — the code never did a deep merge. A resource or module override that redeclared a nested `configuration`/`custom` object to change one key silently dropped every sibling key from the workspace-level definition.
- **Blast radius**: unlike `get_merged_properties()` (dead code, see below), this is on the live `apply_environment_overrides()` path exercised by every `strata build`/`strata deploy` that uses a resource or module override with nested `configuration`/`custom` data.
- **Fix**: Both `configuration` and `custom` merges (resource overrides and module overrides) now use the shared `strata.utils.dict_merge.deep_merge()`. `references` and `labels` overrides were deliberately left as shallow `dict.update()` — both are typed as flat `Dict[str, str]` (cross-resource references) / conventional flat k8s-style label maps, not arbitrary nested config, so shallow replacement is the correct, unchanged behavior there.
- **Testing**: New `tests/strata/services/test_services_deployment_resource_configuration_override.py` — builds a mocked workspace resource/module with nested `configuration`/`custom` dicts, applies an environment override that only touches one nested key, and asserts sibling keys (both top-level and nested) survive while the overridden key changes.

#### **`EnvironmentService.get_merged_properties()` — same bug pattern, but dead code**

- Found during the same audit: `get_merged_properties()` (workspace → environment → override properties merge) had the identical shallow `dict.update()` bug, but has zero callers in `src/` or `tests/` — `TerraformBuilder._resolve_merged_properties()` is the actual code path used for build output. Fixed for consistency (now uses `deep_merge()`) and marked `.. deprecated::` in its docstring noting it's dead code kept only for backward compatibility with any external callers, rather than removed outright.

## [1.9.1] - 2026-09-02

### Changed

#### **Clarify layering and path resolution (ADR-0072)**

Merged three overlapping layering mechanisms and refactored layer resolution into a single, shared entry point with explicit three-state error handling. Discovered and fixed three silent-failure paths, one regression, and one latent pattern bug during implementation review.

**Core Refactoring:**

- **Schema consolidation**: Removed `configuration.spec.layering` (artifact-path matcher) and `spec.layerings` (list of schemes), merging both into `spec.paths` with a new `resolves: layers` flag that marks a convention as owning hierarchy (segment names, order, patterns, defaults)
- **Deployment shape change**: `deployment.spec.layers` changes from a flat freeform dict to `{follows, segments}`, where `follows` names a convention and `segments` provides explicit overrides; any omitted segment is derived from the deployment file's path, then falls back to the segment's default, then becomes "not applicable"
- **Removed `required` flag**: Layer segments now have three explicit states (resolved/default/not-applicable), so every deployment in a family legitimately has different depths; no more forced hand-typed values
- **Improved matcher**: `spec.paths.pattern` now uses genuinely segment-aware matching (respects path depth) instead of `fnmatch` (where `*` and `**` both match across `/`), fixing false positives where `deploy/*/*/**` and `deploy/*/*/*/**` patterns all matched shallow files
- **Validation order**: `validate:` rules now check each segment's **resolved** value rather than raw path captures, so explicitly-declared values and shallower deployments are no longer skipped
- **Error on ambiguity**: Two conventions matching one file now raise a hard error naming both (instead of silent first-match pick)

**New Entry Point and Three-State Model:**

- **`resolve_layers()` (src/strata/utils/path_convention.py)** — Single shared entry point for all layer resolution, returning `LayerResolution(convention=ConventionModel, values=Dict[str, str], error=LayerResolutionError)` with three explicit states:
  - State 1: `convention` set, `error=None` → resolved against a named convention; `values` are the resolved segments (path-derived + explicit overrides + defaults + not-applicable)
  - State 2: `convention=None`, `error=None` → no convention matched; values are pass-through (explicit-only, for templates); no artifact path can be computed
  - State 3: `error` set → resolution failed (pattern typo, ambiguous match, bad `follows` name, or declared-but-unclaimed); callers must surface the error
- **Two-level resolution precedence**: Level 1 selects convention (explicit `follows` → auto-detect → none); Level 2 gets values (path derivation → explicit override → default → not-applicable)
- **Dict-aware pattern matching**: Added helper `resolve_spec_rule()` that handles both attribute access and dict lookups, fixing a regression where `validate:` rules pointing into freeform `spec.configuration`/`spec.properties` dicts validated nothing

**Regressions Fixed:**

1. **Namespace overlap check (Check #3) silent regression** — `OverlapController._compute_artifact_path()` was coupled to `resolve_layers()` segment names; when no convention matched, every deployment collapsed to the same key_set, preventing the cross-depth namespace-uniqueness warning. Fixed by adding fallback to explicit segment names when resolution fails, with permanent regression test.

2. **Silent errors in promote layer resolution** — `PromoteController._resolve_dep_layers()` called `resolve_layers()` but discarded any error; a bad pattern, ambiguous convention, or typo would silently fall back to explicit-only values, potentially misrouting promoted deployments. Refactored to use `_dep_rel_paths` dict (replacing `object.__setattr__` stash) and surface errors as warning messages.

3. **Silent errors in path convention policy** — `PathConventionPolicy.evaluate_conventions()` never checked or reported `resolve_layers()` errors. Now emits violations when resolution fails.

**Consistency Fix:**

- **Intentional asymmetry between `get_resolved_layers()` and `get_artifact_path()` (ADR-0072 Remaining Work)** — When no convention matched but layers were declared, both methods now consistently reflect that state:
  - `get_resolved_layers()` → explicitly-declared values only ("pass-through"; templates need `layers.zone` lookup to work)
  - `get_artifact_path()` → empty string (no segment order without convention; artifact belongs nowhere)
  - The contradiction is **intended**, not a bug — GitOps templates like `targetNamespace: {{ layers.environment | default('default') }}` would retarget to `default` namespace if we blanked both. Formalized via `LayerResolution` three states with documented asymmetry.

**Detection & Prevention:**

- **Silent no-op detection** — `resolve_layers()` now emits an error (but **only** when both layers are declared **and** conventions exist) when a deployment declares `spec.layers` but no convention claims it. This catches typos (pattern `zoneZZ/{zone}` instead of `zones/{zone}`) without false-positives for workspaces with no layering setup. Error message:
  ```
  '{rel_path}' declares spec.layers but no resolves: layers convention claims it. 
  Its artifact path would be empty. Fix the convention's scope/pattern, or remove spec.layers.
  ```
  - Guard condition: only fires when (1) `spec.layers` declared **and** (2) ≥1 `resolves: layers` convention exists — workspaces without layering stay completely silent
  - Tested in 9 scenarios; 0 false positives

**Latent Pattern Bug Fixed:**

- **Pattern `{environment}` was capturing first path segment ("deploy") instead of environment** — All 8 example stacks (Azure AKS, AWS EKS, GCP GKE, Hetzner Compose, Kamatera Swarm + 2 scaffolds) inherited a generic pattern from migration. These stacks encode nothing in paths (`deploy/[stack]-[env].yaml`), so the pattern was returning `{'environment': 'deploy'}`. Fixed by changing all 8 to literal-only patterns `pattern: "deploy"` with no captures. This was masked by the asymmetry — `get_artifact_path()` returned empty (no convention match), but `get_resolved_layers()` still had the garbage derivation; comprehensive pattern-matching tests added to catch this family of bugs.

**Affected Files:**

- Models: `deployment_model.py` (LayersModel.follows, LayersModel.segments), `configuration_model.py` (PathConventionModel.resolves), `platform_artifact_model.py` (LayerResolution new type)
- Utils: `path_convention.py` (resolve_layers entry point, dict-aware resolve_spec_rule)
- Services: `deployment_service.py` (get_resolved_layers, get_artifact_path asymmetry docs, _validate_deployment_layers error reporting)
- Controllers: `overlap_controller.py` (layer_key_set fallback, regression test), `promote_controller.py` (_dep_rel_paths dict, error surfacing), `graph_controller.py` (path_convention integration)
- Builders: `platform_builder.py` (resolve_layers integration)
- Validators: `path_convention_policy.py` (error emission)
- Configs: 8 stack configs + 2 scaffolds migrated (pattern fixes)
- Tests: `test_utils_resolve_layers.py` (13 new tests, all three states)
- Docs: `docs/config/configuration.md` (60+ line rewrite), `docs/config/deployment.md` (spec.layers shape docs), `docs/GLOSSARY.md` (layer/scope/convention definitions)

### Added

- **`strata deploy run --namespace NAME` (repeatable) scopes the helm provisioner to specific namespace(s) for a single run**
  - Problem: `HelmDeployer.validate_workspace()` unconditionally loaded every namespace in `deployment_service.get_namespace_services()` on every run — no way to scope a single invocation to a subset without temporarily editing `workspace.spec.namespaces` (and its topology's matching sub-list) and reverting afterward
  - New `DeploymentStageModel.helm_namespaces: Optional[List[str]]` field (`src/strata/models/deployment_model.py`) — plural, default-deny allowlist, same shape as the existing `secrets` field. Deliberately named distinctly from the existing singular `namespace` field, which is sync-provisioner-only (argocd/flux, build-time, single value injected into the Jinja2 template context) — the two fields are unrelated and documented as such in both docstrings
  - New `DeploymentService._validate_helm_stage_namespaces()` (sibling to the existing `_validate_sync_stages()`) validates `stage.helm_namespaces` entries against `workspace.spec.namespaces` via the existing `_load_workspace_namespaces()` helper — hard error on unknown names, wired into `_validate_dynamic()` so it runs on every `deploy run` (not just `--deep`)
  - New CLI option `--namespace NAME` (repeatable, `deploy run` only, `src/strata/commands/cli_deploy.py`) → `RunDeployCommand._namespaces`. `_execute_provisioning()` hard-errors before any stage runs if a supplied name isn't in `deployment_service.get_namespace_services()`
  - `BaseDeployer` gained a plain `namespace_filter: Optional[List[str]] = None` attribute (`src/strata/deployers/base_deployer.py`) — not a constructor parameter, so no deployer subclass's `__init__` signature changed. `BaseDeployCommand._create_deployer()` sets it post-construction: CLI `--namespace` (only `RunDeployCommand` ever sets `self._namespaces`) takes precedence over the stage's declarative `helm_namespaces`; every other command/deployer is unaffected
  - `HelmDeployer.validate_workspace()` filters `namespace_services` by `self.namespace_filter` when set (otherwise unchanged — omit for no filtering, the default)
  - Docs: `docs/config/deployment.md` — new "`namespace` vs `helm_namespaces`" comparison section under `## Stages`
  - Tests: `tests/strata/models/test_models_deployment.py` (`TestDeploymentStageModelHelmNamespacesField`), `tests/strata/services/test_services_deployment.py` (`TestValidateHelmStageNamespaces`), `tests/strata/commands/test_commands_deploy.py` (`TestExecuteProvisioningNamespaceFilter`), `tests/strata/deployers/test_deployers_helm.py` (`TestHelmDeployerValidateWorkspaceNamespaceFilter`)

## [1.8.3] - 2026-08-27

### Fixed

- **`strata deploy run` always failed with `ServiceNotValidatedError: Service 'EnvironmentService' must be validated before use` (regression since v1.7.0)**
  - `RunDeployCommand._load_related_services()` (`src/strata/commands/deploy/run_deploy_command.py`) was a stale no-op override (`return True`) that never called `deployment_service.load_deploy_services()` — predates v1.7.0, originally harmless because `BaseDeployCommand._before_execute()` used to call `deployment_service.load_deploy_services()` directly and unconditionally
  - Commit `0720edb3` (2026-08-12, shipped in v1.7.0) refactored `_load_related_services()` into an overridable hook so lightweight, environment-only commands (`deploy show`, `values get`/`list`/`resolve` — see their own `_load_environment_related_services()`-based overrides) could skip the full workspace load; from that point on, `RunDeployCommand`'s pre-existing no-op override silently intercepted the call instead of delegating to the base class
  - Effect: `DeploymentService._environment_service`/`_workspace_service` were never populated for `deploy run`. `strata validate --deep` and `strata build run` against the same deployment file both succeeded (they use different loading paths), masking the bug until `deploy run` crashed on the very first call to `_resolve_values()` → `ValueController.resolve_values()` → `DeploymentService.get_environment_service()`, which raises `ServiceNotValidatedError` when `_environment_service is None`
  - Fix: removed the override entirely — `RunDeployCommand` now inherits `BaseDeployCommand._load_related_services()` (full `load_deploy_services()` + `validate_related_services()` + `apply_environment_overrides()`), matching the pre-v1.7.0 behavior
  - New tests in `tests/strata/commands/test_commands_deploy.py` (`TestRunDeployCommandLoadRelatedServices`): an identity check that the class no longer defines its own `_load_related_services`, plus a behavioral check that the inherited method actually calls `deployment_service.load_deploy_services()` and propagates its result
  - No test previously caught this because existing `RunDeployCommand` tests mock `_deployment_service` (or `execute()` itself) rather than exercising the real `DeploymentService.load_deploy_services()` → `get_environment_service()` path end-to-end

## [1.8.2] - 2026-08-25

### Fixed

- **Terraform input validation flagged secrets belonging to a different provisioner as errors**
  - `TerraformBuilder._collect_declared_input_keys()` (added v1.7.0, ADR-0063 Gap 3) swept every secret declared anywhere in `environment.yaml` into every Terraform provisioner's declared-inputs set, with no scoping by stage or provisioner
  - New `TerraformBuilder._stages_for_provisioner()` resolves which deployment stages actually route to a given provisioner (`stage.provisioner` name → `stage.topology`/`topology.provisioner` → sole workspace provisioner fallback), mirroring `BaseDeployer._resolve_iac_model()`'s exact resolution order
  - New `TerraformBuilder._allowed_secret_keys_for_stages()` unions those stages' `secrets:` allowlists (`['*']` = all), matching `ResolvedValues.for_stage()`'s identical deploy-time secret scoping
  - `_collect_declared_input_keys()` now scopes only SECRETS this way; variables/features remain a global check (never stage-scoped at deploy time either — injected into every stage unfiltered)
  - No stages defined at all, or no stage resolves to a given provisioner → falls back to the legacy unscoped behavior (no regression for single-provisioner workspaces without stage-level secret scoping)
  - 19 new tests in `tests/strata/builders/test_builders_terraform.py`, including an end-to-end `_validate_inputs()` regression test reproducing the reported false-positive and confirming a genuine `variables.tf` typo is still caught

- **Local Helm charts with standard Go-template syntax crashed the entire build**
  - `HelmBuilder` copies a local chart's source directory then unconditionally Jinja2-renders every text file in it via `BaseBuilder._apply_templates_to_dir()` — Helm's own `templates/` files use Go-template syntax (e.g. `{{ .Release.Name }}`), which Jinja2 cannot parse (`TemplateSyntaxError: unexpected '.'`), aborting the entire build
  - `_apply_templates_to_dir()` gained an `exclude_dirs` param to skip named subdirectories at any depth; `HelmBuilder` now excludes `templates/` when templating a copied local chart — that subtree is rendered by Helm itself at deploy time
  - Defense in depth: both `_apply_templates_to_dir()` and `_apply_template_to_file()` now also catch `jinja2.TemplateError`, skipping and logging a warning for any one unparseable file instead of aborting the whole build
  - Regression tests in `tests/strata/builders/test_builders_base.py` and `tests/strata/builders/test_builders_helm.py`, including the exact `{{ .Release.Name }}` repro and a nested-subchart case

### Added

- **Helm `${TOKEN}` secret substitution now works at any nesting depth, not just `entry.env.KEY`**
  - `HelmDeployer._find_env_tokens()` previously only matched `values_doc[entry]['env'][key]` — exactly one level of nesting where `env` is a direct child of a top-level entry — so any real-world chart with chart-mandated deep nesting (e.g. Immich's `controllers.main.containers.main.env.DB_PASSWORD`) or a flat `{env: {...}, image: {...}}` shape had no way to opt into token substitution at all
  - Generalized to a recursive walk that scans any dict node keyed literally `env` (dict-shaped only), at any nesting depth — still scoped to `env`-keyed dicts specifically, not an unrestricted tree walk, so a user-typed `${...}`-shaped string in unrelated `svc.configuration`/`module.spec.configuration` pass-through values is still never matched
  - Strict superset of the old behavior — no existing token resolution changes
  - Not implemented: the Kubernetes-native list env shape (`env: [{name: KEY, value: value}]`) — documented as an explicit non-goal
  - 6 new tests in `tests/strata/deployers/test_deployers_helm.py` for deep nesting, top-level `env`, non-dict `env` values, and no-recursion-inside-a-matched-`env`-dict

## [1.8.1] - 2026-08-25

### Fixed

- Corrected `VERSION.txt` (bumped to `1.8.0-alpha` in the Kroki integration commit) — setuptools' dynamic-version PEP 440 normalization turns this into `1.8.0a0` (no hyphen) at install time, which fails `strata`'s own strict-semver self-checks (`tests/strata/commands/test_version.py`). No prior release in this repo's history had ever used a prerelease suffix; reverted to the established plain `X.Y.Z` convention. No functional changes.

## [1.8.0] - 2026-08-25

### Added

- **Diagram visualization in VS Code extension (ADR-0034)** — full implementation across 4 phases:
  - Phase 1: `kind: diagram` YAML schema (`sources` + Jinja `template`, optional `layout`/`style` sugar), `strata diagram show`/`list`, VS Code preview pane with live theme integration
  - Phase 2: `strata://` URI scheme, `strata diagram resolve`, click-to-open-file, hover tooltips, reverse cursor→node lookup, `strata validate --deep` link-rot checking for hand-authored `click` directives
  - Phase 3: all built-in sources (`topology`/`files`/`resources`/`modules`/`namespaces`/`network`/`firewalls`/`dns`/`stages`/`environments`/`tenants`/`history`/`promotion`/`approvals`/`variables`/`secrets`/`features`/`values`/`policies`/`drift`/`locks`/`repositories`/`outputs`/`sbom`), the full "Top 10" built-in diagram set, `strataDiagrams` sidebar view, `/diagram` chat command, and 4 new cookbook diagrams completing the non-flowchart set (`drift-summary` pie, `gate-sequence` sequence, `environment-complexity` quadrant, `secret-store-flow` sankey)
  - Phase 4: Visual Builder webview (`diagramBuilderProvider.ts`), round-trip guarantee (`--print-template` escape hatch), `/diagram create <description>` natural-language generation (via the chat request's own LLM, validated through the standard `DiagramService.validate()` pipeline with a one-retry repair loop), Mermaid-markdown/SVG/PNG export (SVG/PNG entirely client-side — DOM SVG extraction + canvas rasterization in the preview webview, no network round-trip)
  - See ADR-0034's "Deferred (By Design)" section for what's intentionally out of scope (Phase 5: parameterization, comparison mode, dashboard mode, gallery, large-topology perf work)
- **`strata diagram show --format svg|png` via Kroki** — new `kroki` integration (`diagram_render` capability) renders Mermaid diagrams to real SVG/PNG image files via `https://kroki.io` (zero-config) or a self-hosted instance (`STRATA_KROKI_ADDRESS` or a declared `type: kroki` integration). `--format` is distinct from `--output` (console/json/text response envelope). See `docs/help/kroki.md`.
- **Required-integration validation scoped by capability (ADR-0069, Option B)**
  - `IntegrationService.validate_required_integrations()` gains an optional `capabilities: Optional[Set[Type]] = None` parameter — when given, only `required: true` integrations whose `spec.capabilities` intersect that set are validated
  - `initialize_integrations()` no longer calls `validate_required_integrations()` internally, fixing a singleton "first caller wins" bug where only the first command in a process got its integrations validated
  - 7 call sites updated (`IdentityController`, `CostController`, `AuditController`, `ExportAuditCommand`, `find_available_integration_with_capability()`, `ValueController`, `DoctorSlnCommand`) to explicitly scope validation to their required capabilities
  - Fixes a false failure reported by the haven team: `strata values get` previously failed on a `required: true` terraform integration it never needed (only secret/variable/feature stores); the workaround of setting `required: false` broke `strata build`/`deploy`, which do need it

### Fixed

- **`strata diagram show -f refs` (and `-f topology`) resolved file references relative to the referencing file's directory instead of the workspace root**
  - `_add_edge_and_walk()` (`src/strata/controllers/graph_controller.py`) resolved every `file:` reference (`spec.workspace.file`, resource/module/namespace/network/firewall/dns entries) as `source_file.parent / ref` — relative to the *referencing file's own directory* — instead of `self._work_path / ref` — relative to the *workspace root*, which is the convention used everywhere else (`BaseService._resolve_file_path()`, `resolve_path()` in `src/strata/utils/system.py`)
  - Same bug in `_resolve_workspace_path()` when following a deployment's `spec.workspace.file` via `entry_path.parent`
  - Effect: any workspace where the referencing file lives below the workspace root (the normal case, e.g. `config/deployment.yaml` referencing `config/stack/workspace.yaml`) produced a doubled path prefix (`config/config/stack/workspace.yaml`), which doesn't exist on disk — every such node rendered `:::missing` in `strata diagram show -f refs` and `-f topology`, even though `strata validate --deep` on the same files was clean
  - Fix: both call sites now resolve against `self._work_path` unconditionally
  - No test coverage previously caught this because existing fixture workspaces have their entry file directly at the workspace root (`source_file.parent == self._work_path`), masking the bug
  - See ADR-0015

## [1.7.0] - 2026-08-12

### Breaking Changes

- **Cost estimation gated behind declared `infracost` integration**
  - `strata cost show`, `strata cost diff`, and the automatic post-plan cost diff in `deploy run --dry-run` previously worked off of any `infracost` binary found on PATH, regardless of `configuration.yaml`
  - They now require an explicit `infracost` entry under `spec.integrations` (`capabilities: [cost]`, `enabled: true`), matching how every other integration (secret stores, provisioners) is gated
  - An installed binary with no declaration no longer does anything; `strata cost history` is unaffected (reads past `cost show` snapshots, needs no estimator)

### Added

- **CLI login for a control plane or any OIDC service (ADR-0067)** — new `identity` integration capability with six first-class providers (`azure_ad`, `google`, `aws_identity_center`, `auth0`, `github_oauth`, `generic_oidc`), declared under `spec.integrations` like any other integration. No dedicated `strata login` command — login triggers lazily via `strata sln doctor --deep --login`. A control-plane session's authenticated identity outranks the CLI-local `actor` resolution chain (ADR-0066). See `strata help --topic identity`.

- **Provisioner-managed resources**
  - New field: `WorkspaceResourceModel.managed_by: Optional[Literal["provisioner"]]` (`src/strata/models/workspace_model.py`) — marks a resource as fully provisioner-owned
  - Sibling field `WorkspaceResourceModel.file: Optional[str]` now optional (was required)
  - New validator `validate_file_or_managed_by()` enforces strict XOR: resource must have `file` reference OR `managed_by` set, but never both
  - Validation rejects invalid `managed_by` values; only `"provisioner"` is currently allowed (extensible for future values like `"external"`, `"configuration"`, etc.)
  - Workspace loading skip (`src/strata/services/workspace_service.py`) skips file resolution/loading when `resource_ref.managed_by` is set; stores `None` in resource service cache to mark the resource as externally-managed
  - Builder skip (`src/strata/builders/platform_builder.py`) guards against `None` resource services in both resource emission loop and firewall merge loop
  - Use case: multi-tenant IaC deployments where Terraform modules fully define resources (VMs, databases, networks, etc.) and workspace YAML only needs to wire topology/provisioners without detailed resource specs. Eliminates stub resource files.
  - Backward-compatible: existing workspaces with `file: <path>` continue working unchanged

- **Git ref pinning on `SourceModel` (ADR-0063, Gap 1)** — Provisioner sources now accept an optional `reference` field (branch, tag, or commit SHA) that overrides the workspace-level remote default. This allows two provisioners referencing the same remote to pin different versions (e.g., platform baseline on `v1.4.0` and team module on `main`). Resolution priority: `source.reference` → environment remote override → remote default. When a ref is pinned, `git archive` extracts the subtree without mutating the working tree.

- **Structured variable types (ADR-0063, Gap 2)** — `VariableStoreModel` now accepts an optional `type` field (`string`, `number`, `bool`, `object`, `list`, `map`) that declares the intended HCL type. When set, strata validates that the YAML value matches the declared type and emits it as a native JSON type in `.auto.tfvars.json` instead of always stringifying.

- **Terraform input validation (ADR-0063, Gap 3)** — `strata build run` now cross-checks declared variable/feature/secret keys from environment YAML against the module's `variables.tf` declarations. Undeclared inputs (typos) are errors that block the build with fuzzy-match suggestions. Unsupplied required variables (no default) are reported as warnings.

- **Helm values validation (ADR-0063, Gap 3)** — For local Helm charts, `strata build run` now cross-checks `module.spec.configuration` keys against the chart's default `values.yaml`. Typos in top-level and one-level-deep keys are reported with fuzzy-match suggestions. Warnings only (does not block build). Registry charts are skipped (not available at build time).

- **Output passing between provisioners (ADR-0063, Gap 4)** — Provisioners now accept an `inputs_from` field that declares explicit dependencies on other provisioners' outputs. Supports `mapping` (key rename), `prefix` (add prefix to all keys), and `select` (allowlist) modes. Validated at schema level: unknown provisioner references, self-references, and circular dependencies are rejected.

- **Combined deployment outputs artifact (ADR-0063, Gap 5)** — After a successful `deploy run`, strata now writes a `deployment-outputs.json` file that merges all stages' Terraform outputs into a single registry-consumable document. Outputs are keyed by stage name; sensitive output keys are listed but values omitted.

---

## [1.6.1] - 2026-08-03

### Added

- **SQLite-backed resolved-model cache — ADR-0026**
  - `CacheService` (`src/strata/services/cache_service.py`) — SQLite-backed cache (WAL mode) at `.strata/cache/model/cache.db`, `cache`/`cache_inputs` tables; `get()`, `warm()`, `status()`, `invalidate()`/`invalidate_by_path_prefix()`/`invalidate_all()`, `export()`
  - Cache key: SHA-256 hash of every input file's contents (`compute_cache_key()`) plus a schema version, so any edit to a source YAML automatically invalidates the entry — no manual busting needed
  - `CacheController` (`src/strata/controllers/cache_controller.py`) — collects the full transitive set of input files for a deployment: deployment/environment/workspace files, `spec.providers[]/resources[]/modules[]/namespaces[]/firewalls[]/dns_zones[]/networks[].file`, one level of namespace→module recursion, `spec.configurations[].file`, and `spec.tenant` (when present on disk); deliberately excludes module `source` paths (app code, often a glob), documented as a permanent exclusion
  - New CLI group `strata cache` (`src/strata/commands/cli_cache.py`): `warm [-f deployment | --all]`, `status [-f deployment]`, `clear`, `export [path]`
  - `strata build run`/`strata build plan` auto-warm the cache from the `PlatformArtifactModel` they already built, after a successful (non-dry-run) run — no re-resolution needed; `strata policy check` auto-warms opportunistically from the `platform.json` it reads off disk (it never builds a fresh model). New `--no-cache-warm` flag (env `STRATA_NO_CACHE_WARM`) added to all three
  - Deliberately NOT wired into `deploy run/show`, `values list/get/resolve` — those commands need the live `DeploymentService`/`EnvironmentService` object graph (variables/secrets/features/stage config), not the cached JSON-serialisable artifact, so caching the `platform.json` shape doesn't save them work; tracked as a follow-up in ADR-0026, not implemented
  - `strata cache warm` (explicit CLI invocation) also syncs git remote refs before resolving, matching `build run` fidelity; `--no-sync-remotes` to skip
  - VS Code extension: `src/vscode/src/providers/cacheWarmerProvider.ts` (new) — file watcher on `**/*.yaml` debounced 500ms, plus a startup warm; calls `strata cache warm --all --no-sync-remotes`/`strata cache status` under the hood (always passes `--no-sync-remotes` so an ambient auto-warm-on-save never mutates git state); status bar indicator (warm/stale/cold), best-effort only, logs to a "Strata Cache" output channel instead of showing error popups
  - `strataClient.ts` — added `warmCache()`/`getCacheStatus()` + `CacheStatusData`/`CacheStatusEntry` types; `package.json` — new `strata.cache.backgroundWarm` setting (default true) and `strata.refreshCache` command; wired into `extension.ts` (instantiate, register, dispose)
  - Tests: new suites for `cache_service`, `cache_controller`, CLI cache commands, and build-run/build-plan/policy-check auto-warm integration

### Fixed

- **`deploy run`/`deploy destroy` wrong exit code on validation failure — ADR-0004**
  - `BaseDeployCommand` (`src/strata/commands/deploy/base_deploy_command.py`) gained `self._validation_failed`/`has_validation_errors()`, so `handle_command_exit()` now correctly returns exit code 3 for schema/cross-reference validation failures instead of falling through to the generic exit code 1
  - Missing/unresolvable deployment file arguments now raise `click.UsageError` (exit code 2) instead of silently appending to `self._errors` and returning `False` (which incorrectly produced exit code 1)
  - Scope limited to `src/strata/commands/deploy/` — `build`/`validate` commands already had correct exit-code handling, not touched

### Security

- **Secret/variable/feature store outage vs "not found" conflation**
  - Previously `get_secret()`/`get_variable()`/`get_feature()` returned `None` both when a key genuinely didn't exist and when the store was unreachable or misauthenticated (network outage, expired token, etc.) — `ValueController` treats `None` as "missing" and, for secrets with a `generate:` spec, would call `generate_secret()` + `integration.set_secret()`, so a transient outage could silently overwrite a real secret with a freshly generated value, or a deploy could silently proceed with a blank value
  - New exception `SecretStoreUnavailableError` (`src/strata/exceptions/integration_exception.py`)
  - Protocol contract updated in `src/strata/integrations/capabilities.py` (`ISecretStore`/`IVariableStore`/`IFeatureStore`) — `None` now means ONLY "confirmed not found"; any connectivity/auth failure MUST raise `SecretStoreUnavailableError`
  - `src/strata/integrations/infisical.py` — `_get_access_token()` raises on universal-auth login failure; `ensure_available()` does a live login check for universal-auth mode; `_get_secret_via_api()` distinguishes HTTP 404 (returns `None`) from any other HTTP/network error (raises)
  - `src/strata/integrations/hashicorp_vault.py` (also covers `openbao.py`, which subclasses it) — `_get_secretvalue()`/`_get_secret_via_api()` follow the same 404-vs-raise contract; CLI fallback and per-auth-method token-getters (AppRole, Kubernetes) remain best-effort/unchanged so the auth-method chain can still try the next method
  - `src/strata/integrations/bitwarden.py` — `get_secret()` now raises on ANY non-success `bws` CLI outcome; deliberate conservative choice since the `bws` CLI has no reliable signal to distinguish "secret ID not found" from an auth/network failure (unlike the others' clean HTTP 404s) — trades "a genuinely-missing secret with `generate:` now blocks instead of auto-generating" for safety
  - `src/strata/integrations/azure_keyvault.py` — `get_secret()` rewritten as a loop over its existing 3-way fallback chain (CLI → API-with-cli-token → API-with-client-credentials, or reversed when `prefer_cli=False`): catches `SecretStoreUnavailableError` per attempt and tries the next method, short-circuits immediately on a confirmed 404 from an API attempt (authoritative), only re-raises once every method in the chain is exhausted without a confirmed not-found
  - `src/strata/controllers/value_controller.py` — `resolve_values()` wraps each variable/secret/feature resolution call in `try/except SecretStoreUnavailableError`, collecting messages into a new `ResolvedValues.store_unavailable_errors` list (`src/strata/utils/resolved_values.py`); always fatal (`success=False`), overriding the `strict` parameter in both directions — a store outage is a different, always-fatal category from "optional value missing, has a default"
  - `src/strata/commands/deploy/run_deploy_command.py` — `_resolve_values()` now checks `resolved.store_unavailable_errors` and aborts the deploy (`return False`) instead of the previous behavior of unconditionally returning `True` regardless of errors in non-strict mode; this is the line that actually closes the vulnerability

- **Pre-flight store availability check (fail fast, before resolving anything)**
  - `value_controller.py` gained `_preflight_check_stores()`, called at the very start of `resolve_values()` before any variable/secret/feature is resolved
  - Collects the distinct set of integration-backed store types actually referenced by the deployment (skipping `constant`/`environment`/`github`, which need no integration) and calls `ensure_available()` exactly once per distinct store — instead of discovering the same outage once per item (e.g. 10 secrets from a down Infisical previously meant 10 separate failures)
  - If any referenced, registered store is unavailable, resolution aborts immediately before touching any item
  - A store type with no registered integration at all is left to the existing per-item "not registered" error (a config error, not an availability one)

- **Pre-flight provisioner availability check (terraform/ansible/etc., before the deploy lock)**
  - `run_deploy_command.py` gained `_preflight_check_provisioners()`, called at the top of `_execute_provisioning()` — after stage/scope filtering but before the approval-gate check and before the deployment lock is acquired
  - For every stage that will actually run, it creates the deployer and runs the same `validate_workspace()`/`validate_environment()` checks (terraform/ansible/helm/bicep/compose binary presence + cloud CLI auth) that previously only ran per-stage, mid-loop, after the lock was already held
  - Previously, a missing tool on stage 3 of a 3-stage deploy was only discovered after stages 1-2 had already made real infrastructure changes
  - `on_failure: stop` (default) or `on_failure: rollback` → any failure aborts the whole deploy immediately, before the lock or any stage runs
  - `on_failure: continue` → failure is downgraded to a warning message instead (that stage would just be skipped once reached anyway) — does not block the deploy, preserving existing tolerance semantics

- Test coverage: full suite at 5106 passed / 16 skipped after these changes, including new test classes `TestValueControllerStoreUnavailable`, `TestValueControllerPreflight` (value controller), `TestResolveValuesStoreUnavailable`, `TestPreflightCheckProvisioners` (deploy command), plus updated integration tests for infisical/hashicorp_vault/openbao/bitwarden/azure_keyvault

---

## [1.6.0] - 2026-07-31

### Breaking Changes

- **CLI consolidation — `env` dissolves into `deploy`, `rollout`, `sln` — ADR-0062 (implemented)**
  - `env` command group deleted outright (`cli_env.py`, `src/strata/commands/envs/*`) — no deprecation window, no hidden alias
  - `deploy show` absorbs `env show`'s meta/properties/values/overrides/stage payload plus its `--stage NAME` secret-visibility filter
  - `deploy output` absorbs `env output`'s always-live scripting flags: `--name`, `--provisioner`, `--raw`, `--json`; `--refresh` is the equivalent of `env output`'s unconditional live query
  - `deploy status` revived (`StatusDeployCommand`, re-registered in `cli_deploy.py`) with corrected live-state behavior — no longer reuses the old plan-diff `--plan` mode (that stays with `deploy plan`)
  - New `strata rollout status` command (`src/strata/commands/rollout/status_rollout_command.py`) absorbs `env status --all`/`--path` fleet-wide scanning
  - `env info` folded into `sln status`; `env doctor` moved to new `sln doctor` command (`doctor_sln_command.py`)
  - `src/strata/mcp/server.py` — `env_*` MCP tools repointed/removed to match the new command surface
  - All internal call sites (docs, scripts, tests) updated in the same change — grepped for `env info`/`env output`/`env show`/`env status`/`env drift`/`env doctor`

- **Unified `spec.gates` schema — ADR-0059 (implemented)**
  - `spec.approvals`/`spec.approvers` (`DeploymentApprovalModel`, `DeploymentStageApprovalModel`) removed; superseded by a single deployment-level `spec.gates` list reusing ADR-0057's gate framework
  - Approver shape standardized on typed refs (`Dict[str, ApproverRef]` — `github-team`/`user`/`ado-group`) instead of `spec.approvals`'s informal shape and `spec.gates`'s plain `List[str]`
  - `run_deploy_command.py`: `_check_approvals()` deleted; its audit-only logging is absorbed into the gate evaluator's `mode: declare` branch; all 3 gate-evaluation phases (pre-plan/post-plan/post-apply) now read `deployment.spec.gates` instead of `environment.spec.gates`
  - No migration shim — existing `spec.approvals` or environment-level `spec.gates` YAML fails validation and must be rewritten to the unified shape

### Fixed

- **`ref_convention` policy / `strata repo status` design drift — ADR-0017**
  - Tag naming conventions now live only in `spec.remotes[].conventions` (`RemoteConventionsModel`) — no longer duplicated inside the policy's own `configuration.remotes[]`
  - `repo status` links a local repo to its configured remote by comparing normalized git remote URLs, and only classifies release/quality tags when that remote declares `conventions` — no more hardcoded name-prefix guessing
  - No backward compatibility with the old policy-level shape (never released/documented before this fix)

### Changed

- **Subprocess execution consolidation — ADR-0061 (implemented)**
  - All subprocess call sites (builders, deployers, controllers) now go through a single `run_command()` path in `strata.utils.system`
  - Consistent SIGTERM propagation and `timeout` handling regardless of caller — `script_deployer.py`, `lifecycle_controller.py`, `terraform_builder.py` migrated off ad-hoc `subprocess.run`/`Popen` usage
  - Buffered `Popen` path added for callers that previously bypassed the shared helper

- **Command lifecycle migration completed — ADR-0030**
  - Remaining utility commands (`secret generate`, `secret mask`) converted from free functions to `BaseCommand` subclasses for consistent `--output`/`--work-path` handling and testability

## [1.5.0] - 2026-07-27

### Added

- **Deployment workflow orchestration — ADR-0057 (Phases 1–5 implemented)**
  - `WorkItem` dataclass + `BaseWorkItemBackend` ABC with `local`, `git_tag`, `s3`, `azblob`, `gcs`, and `cloud_native` backends
  - `WorkItemController` with `request()`, `resolve()`, `approve()`, `reject()`, `complete()`, `cancel()`, `expire_stale()`, `verify_resolved()`
  - `DeploymentGateModel` / `GateWhenConditionsModel` — gate config in environment `spec.gates`; `GateConditionEvaluator` supporting `cost_delta_monthly`, `cve_critical`, `cve_high`, `ai_risk`, and `time_utc` conditions
  - Gate evaluation wired into `strata deploy run`: approval/scheduled gates pre-plan; cost_review/security_review gates post-plan with real Infracost and CVE data; verify gates post-apply
  - `strata deploy run --resume <work-item-id>` — resumes a paused deployment after gate resolution; commit-mismatch guard prevents replay attacks
  - Exit code 5 (`hand-off required`) from `strata deploy run` when a gate pauses the pipeline
  - **`strata workitem` command group**: `list`, `show`, `approve`, `reject`, `complete`, `cancel`, `expire`
  - `--as IDENTITY` flag on approve/reject — asserted identity tagged `[asserted]` in audit trail
  - Audit log entries written for `workitem.created` and `workitem.{status}` events
  - SIEM forwarding of `workitem.created` and `workitem.resumed` events via configured SIEM sinks (fires from `strata deploy run`)
  - `WorkItemBackendFactory` — backend selected via `STRATA_WORKITEM_BACKEND` env var (fallback: `local`)
  - `cve-audit.json` artifact written after SBOM CVE scan so deploy gate evaluation can read CVE counts
  - `GateContextBuilder` — assembles `GateContext` from `cost.json` and `cve-audit.json` build artifacts
  - `scheduled` gate `auto_resolve: true` — enforces deployment window; exits with code 5 outside window
  - `.strata/workitems/` added to workspace `.gitignore` template
  - Help topic: `strata help --topic workitem`

- **Help system — comprehensive documentation for all platform kinds**
  - Help files created for all 12 YAML kinds: `deployment`, `environment`, `module`, `provider`, `namespace`, `resource`, `firewall`, `network`, `dns`, `tenant` (plus existing `configuration` and `workspace`)
  - `docs/help/` established as single source of truth for all 55 help files; `src/strata/data/help/` and `src/vscode/resources/help/` are now generated artifacts (added to `.gitignore`)
  - `Build.ps1` syncs `docs/help/` to both destinations before packaging; `ci-build.yml` does the same in CI (Python job before `uv build`, VS Code extension job before `vsce package`)
  - `ai_agent` topic registered in CLI `_TOPICS` — `strata help --topic ai_agent` now works
  - SIEM help files renamed to match their topic keys (`siem_sentinel.md` → `sentinel.md`, etc.) so VS Code extension resolves them from bundled resources without requiring CLI fallback
  - `deployment` kind now includes its own topic in the context-sensitive suggestions panel

- **AI agent integration — ADR-0025 (Phases 1–4 implemented)**
  - `AiAgentIntegration(BaseIntegration)` in `strata.integrations.ai` — advisory LLM analysis at build/deploy lifecycle points; purely read-only, opt-in, no infrastructure mutations
  - Providers: `OllamaProvider` (local, no auth), `OpenAiProvider` (OpenAI + Azure OpenAI), `AzureCliProvider` (bearer token via existing `AzureCLIIntegration.get_access_token()`, no stored key), `AnthropicProvider`
  - Auth methods on existing `AuthenticationModel`: `api_key` (env var resolution), `cli` (Azure CLI), `managed_identity`; `provider: azure_cli` acquires short-lived bearer token via `az account get-access-token --resource https://cognitiveservices.azure.com/`
  - Six analysis methods: `analyse_plan()`, `diagnose_failure()`, `analyse_sbom()`, `explain_drift()`, `summarise_deployment()`, `review_policy_violations()`
  - `PromptLoader` with `.strata/prompts/<name>.md` workspace override support — resolved via `get_ai_prompts_dir(work_path)` from `utils/config.py`; built-in Python templates for all six hooks
  - `AiResponseCache` — SHA-256-keyed JSON file cache under `get_ai_cache_dir(work_path)` (`.strata/cache/ai/`); configurable TTL (default 24 h); failure-diagnosis never cached
  - `strata build plan --ai` — runs `analyse_plan()` after terraform plan; renders risk level, summary, concerns, and recommendations to console; adds `ai_analysis` key to JSON output
  - `strata build sbom --ai` — runs `analyse_sbom()` after SBOM generation; renders supply-chain risk summary to console
  - `strata deploy run --ai` — runs `diagnose_failure()` on any provisioner step failure (root cause + remediation); runs `summarise_deployment()` on successful completion
  - `type: ai_review` policy — gates deployment based on LLM risk score; configurable `risk_threshold` (`low`/`medium`/`high`/`critical`); registered in `PolicyEngine`
  - `--strict-ai-review [THRESHOLD]` on `strata build plan` and `strata deploy run` — fails non-interactively when AI risk ≥ threshold (default `high`); no policy declaration required; suitable for CI/CD
  - Interactive confirmation on `strata deploy run --ai` — prompts operator before apply when risk is high/critical and `--force` is not set; auto-blocks in non-TTY (CI) mode
  - Registered in `IntegrationFactory._BUILTIN_CLASS_MAP` as `"ai_agent"`
  - Path constants `SOLUTION_AI_CACHE_DIR = "cache/ai"` and `SOLUTION_PROMPTS_DIR = "prompts"` + builder functions `get_ai_cache_dir()` / `get_ai_prompts_dir()` added to `utils/config.py`
  - Every invocation logged to audit trail with provider, model, token counts, duration, and cache status
  - Help topic: `strata help --topic ai_agent`
  - Solution scaffold (`strata sln init`) includes commented-out `ai_agent` integration example in `configuration.yaml`
  - Phase 5: VS Code Chat Participant AI commands
    - New `@strata /review` — runs terraform plan and analyses risks using `request.model` (VS Code LM API / GitHub Copilot); streams risk level, concerns, and recommendations into chat
    - New `@strata /diagnose` — loads last failed deployment from audit history and generates root-cause + remediation analysis
    - New `@strata /sbom` — loads SBOM component inventory and analyses supply-chain risks
    - `AiPromptBuilder` (`src/vscode/src/providers/aiPromptBuilder.ts`) — resolves system prompts from `.strata/prompts/<name>.md` workspace overrides, falling back to built-in TypeScript constants
    - Auto-routing in `_handleFreeform` — detects AI-related keywords ("review plan", "why did it fail", "sbom", etc.) and delegates to the appropriate handler
    - Follow-up suggestions for all three new commands
    - `package.json` updated with `review`, `diagnose`, `sbom` slash command declarations
    - No Python AI provider configuration required in IDE — uses the model already active in VS Code (GitHub Copilot)
  - Phase 6: Extended command coverage
    - `strata validate --ai` — runs `review_policy_violations()` after finding schema errors or policy violations; explains each violation with a suggested YAML fix
    - `strata deploy drift run --ai` — runs `explain_drift()` when drift is detected; explains likely cause and reconciliation path
    - `strata env doctor --ai` — runs `explain_doctor_results()` on failed checks; provides numbered per-check remediation steps
    - `strata guide --ai` — runs `assist_guide()` when a readiness phase is blocked; explains the blockage and suggests the next concrete action
    - Two new analysis methods on `AiAgentIntegration`: `explain_doctor_results()`, `assist_guide()`
    - Two new prompt files: `data/prompts/doctor_analysis.py` (`DoctorAnalysisPrompt`), `data/prompts/guide_assistance.py` (`GuideAssistancePrompt`)
    - All eight prompts support `.strata/prompts/<name>.md` workspace overrides via `PromptLoader`
  - Phase 7: `strata policy check --ai`
    - `--ai` flag on `strata policy check` — runs `review_policy_violations()` after evaluation when any policy fails; multi-phase violations (validate/build/plan/deploy) are passed together giving the AI cross-phase context
    - Reuses existing `review_policy_violations()` and `policy_review.py` prompt — no new method or prompt file needed
    - `configuration_service` is already loaded by the command; `find_ai_integration()` uses it directly without additional loading
  - Phase 7 (continued): `strata build run --audit --ai`
    - `--ai` flag on `strata build run` — triggers `analyse_cve_results()` after the `--audit` CVE scan when findings are present
    - `CveAuditResultModel` (already stored as `self._cve_audit_result` by `_execute_audit`) is serialised and passed to the AI with the full findings list
    - New `analyse_cve_results()` method on `AiAgentIntegration` + new `CveAnalysisPrompt` (`data/prompts/cve_analysis.py`) — groups findings by severity, flags no-fix CVEs, prioritises packages to upgrade
  - Phase 7 (continued): `strata deploy health --ai`
    - `--ai` flag on `strata deploy health` — triggers `explain_health_failures()` when any probe fails; explains per-check root cause and remediation
    - Bug fix: `deploy health` command was not registered in the `deploy` group (`@deploy.command` decorator was missing); it now appears in `strata deploy --help`
    - New `explain_health_failures()` method on `AiAgentIntegration` + new `HealthAnalysisPrompt` (`data/prompts/health_analysis.py`)
  - Phase 7 (continued): `strata promote status --ai`
    - `--ai` flag on `strata promote status` — triggers `explain_promotion_status()` after loading in-flight promotions
    - AI explains which promotions are in-progress, which need attention, and recommends next action per promotion
    - New `explain_promotion_status()` method on `AiAgentIntegration` + new `PromotionStatusPrompt` (`data/prompts/promotion_status.py`)
  - Bug fix: `install_tools_command.py` and `show_log_command.py` had stale `ConfigurationService(config_paths)` constructor calls; corrected to `ConfigurationService.load(cp)`
  - Phase 7 (continued): `strata values list --ai`
    - `--ai` flag on `strata values list` — triggers `explain_unresolved_values()` when any value fails to resolve
    - AI explains per-value likely cause and exact fix command (env var export, `az keyvault secret set`, `bws secret create`, etc.) tailored to the store type
    - New `explain_unresolved_values()` method on `AiAgentIntegration` + new `UnresolvedValuesPrompt` (`data/prompts/unresolved_values.py`)
  - Phase 7 (continued): `strata deploy history --ai`
    - `--ai` flag on `strata deploy history` — triggers `summarise_deploy_history()` when ≥2 entries exist
    - AI analyses success rate trend, detects recurring failures, flags anomalies, recommends next steps
    - New `summarise_deploy_history()` method on `AiAgentIntegration` + new `DeployHistorySummaryPrompt` (`data/prompts/deploy_history_summary.py`)
  - Bug fixes: `cost/history_cost_command.py` had duplicate `_execute`/`_render_history` methods and stale `ConfigurationService` constructor; `audit/changes_audit_command.py` had same stale constructor pattern; all corrected
  - Phase 10: `strata audit changes --ai`
    - `--ai` flag on `strata audit changes` — triggers `summarise_audit_history()` after querying deploy-log entries
    - Success rate, duration stats, and failure counts pre-computed client-side; AI detects patterns and produces a narrative
    - Works with all existing filter flags: `--last N`, `--since TIMESTAMP`, `--stage NAME`
    - New `summarise_audit_history()` method on `AiAgentIntegration` + new `AuditHistorySummaryPrompt` (`data/prompts/audit_history_summary.py`)
  - Phase 7 (final): `strata service deploy --ai`
    - `--ai` flag on `strata service deploy` — triggers `diagnose_failure()` when a helm, compose, or script step fails
    - Error output captured per-target; AI receives deployer type (`helm`/`compose`/`script`), namespace/module, and error text
    - Reuses existing `diagnose_failure()` method and `failure_diagnosis.py` prompt — no new prompt or AI method needed
    - With `--force --ai`, AI diagnoses each failing service independently and continues to the next

## [1.4.0] - 2026-07-24

### Added

- **`--timeout` for deploy run and deploy destroy — ADR 0027 (implemented)**
  - `--timeout SECONDS` option on `strata deploy run` and `strata deploy destroy` (default: `0` = no timeout)
  - `timeout=0`: stage loop runs on the main thread with no overhead
  - `timeout>0`: stage loop runs in a `ThreadPoolExecutor` worker; main thread calls `future.result(timeout=N)` and triggers `coordinator.shutdown("timeout after Ns")` on expiry
  - `ShutdownCoordinator.update_lock(backend, handle)` / `clear_lock()` — thread-safe handshake so the main thread's signal handlers can release a lock acquired in the worker thread
  - Exit code 1 on timeout — same as SIGTERM; deployment is in unknown state, operator inspection required
  - 7 new tests for timeout path and lock handshake

- **SIGTERM graceful shutdown — ADR 0028 (implemented)**
  - `ShutdownCoordinator` in `strata.utils.shutdown_coordinator` — ordered shutdown: terminate subprocesses → release deployment lock → exit 1
  - Process registry: `run_command()` auto-registers/deregisters every `Popen` instance with the active coordinator — zero boilerplate in deployers
  - Signal handlers installed per-invocation (scoped to lock-holding window): SIGTERM (Unix), SIGINT (all platforms), `atexit` safety net
  - Subprocess termination: SIGTERM to all active processes → 30s grace period → SIGKILL stragglers
  - Re-entrant guard (`threading.Event`) prevents double-shutdown on rapid signals
  - `children: List[ShutdownCoordinator]` hook for future rollout/parallel-deploy fan-out
  - Wired into `RunDeployCommand._execute_provisioning()` and `DestroyDeployCommand._execute_provisioning()`
  - 20 tests (1 skipped on Windows where SIGTERM is unavailable)

- **Google Cloud CLI integration + lifecycle scripts — ADR 0055 Phase 1**
  - `GCloudCLIIntegration(BaseIntegration)` — `COMMAND = "gcloud"`; `ensure_available()` checks binary + `gcloud config get-value account` + active project (three-step check, unlike Azure/AWS which stop at auth); `get_project()`, `get_account()`, `get_access_token()` (cached), `run_gcloud()` passthrough
  - `IGCloudTool` capability protocol + `"gcloud"` in `CAPABILITY_MAP`; registered in `IntegrationFactory`
  - `strata.utils.gcloud_script_base.GCloudScript` — base class mirroring `AzureScript`/`AWSScript`; `project()` resolves via `GOOGLE_CLOUD_PROJECT` → `CLOUDSDK_CORE_PROJECT` → `gcloud config`; `account()` and `get_access_token()` helpers
  - Built-in script: `gcloud_gke_credentials.py` — `gcloud container clusters get-credentials`; `GKE_CLUSTER` + `GKE_ZONE`/`GKE_REGION`; optional `GKE_ROLE_ARN` → `GKE_INTERNAL_IP`
  - Built-in script: `gcloud_artifact_registry_login.py` — `gcloud auth configure-docker`; `GAR_LOCATION` for Artifact Registry or `GCR_ENABLE=true` for legacy GCR
  - Built-in script: `gcloud_gcs_bucket_ensure.py` — idempotent bucket create with `--no-fail-on-existing-bucket`; optional versioning, storage class, location, labels
  - Solution scaffold: `.strata/scripts/gcloud_lifecycle_example.py`
  - Help: `strata help --topic gcloud_cli`, `strata help --topic gcloud_scripts`; guide: `docs/guides/gcloud-lifecycle-scripts.md`
  - ADR 0055 updated: status → phase 1 implemented; corrected "no existing GCP integrations" (gcp_secretmanager.py and gcp_runtimeconfig.py were fictitious)
  - 35 tests, zero regressions against 4833-test suite

- **AWS CLI integration + lifecycle scripts**
  - `AWSCLIIntegration(BaseIntegration)` — `COMMAND = "aws"`; `ensure_available()` checks binary AND `aws sts get-caller-identity`; `get_identity()`, `get_region()`, `run_aws()` passthrough
  - `IAWSTool` capability protocol + `"aws"` in `CAPABILITY_MAP`; registered in `IntegrationFactory`
  - `strata.utils.aws_script_base.AWSScript` — base class mirroring `AzureScript` for AWS; adds `region()` (3-tier resolution: `AWS_DEFAULT_REGION` → `AWS_REGION` → `aws configure`) and `account_id()`
  - Built-in script: `aws_eks_credentials.py` — `aws eks update-kubeconfig` before Helm/ArgoCD/Flux; configured via `EKS_CLUSTER`, `AWS_DEFAULT_REGION`; optional `EKS_ROLE_ARN`, `EKS_CONTEXT_ALIAS`
  - Built-in script: `aws_ecr_login.py` — two-step `get-login-password | docker login`; accepts `ECR_REGISTRY` or auto-constructs from `ECR_ACCOUNT_ID` + region
  - Built-in script: `aws_s3_bucket_ensure.py` — idempotent `aws s3api create-bucket`; optional versioning, AES-256 encryption, public access block, and tags in one script
  - Solution scaffold: `.strata/scripts/aws_lifecycle_example.py` — ready-to-use starter
  - Help: `strata help --topic aws_cli`, `strata help --topic aws_scripts`; guide: `docs/guides/aws-lifecycle-scripts.md`
  - 33 tests, zero regressions against 4798-test suite

- **Azure lifecycle scripts — `AzureScript` base class and built-in scripts**
  - `strata.utils.azure_script_base.AzureScript` — base class for `.strata/scripts/*.py` lifecycle scripts; wraps Azure CLI with `run_az()`, `exit_on_failure()`, `require_env()`, `get_token()`, `log()` and strata context helpers
  - Built-in script: `azure_aks_credentials.py` — `az aks get-credentials` before Helm/ArgoCD stages; configurable via `AKS_CLUSTER`, `AKS_RESOURCE_GROUP`, optional `AKS_ADMIN_CREDENTIALS`, `AKS_CONTEXT_NAME`
  - Built-in script: `azure_acr_login.py` — `az acr login` before container push; configured via `ACR_NAME`
  - Built-in script: `azure_resource_group_ensure.py` — idempotent `az group create` for Bicep subscription-scope deployments; configured via `AZURE_RESOURCE_GROUP`, `AZURE_LOCATION`, optional `AZURE_RG_TAGS`
  - Solution scaffold includes `.strata/scripts/azure_lifecycle_example.py` — ready-to-use starter with built-in script references and custom script pattern
  - Help file: `strata help --topic azure_scripts`; guide: `docs/guides/azure-lifecycle-scripts.md`
  - 26 tests

- **Scoped multi-scheme layering — ADR 0042 Phase 1 (completed)**
  - `spec.layerings[]` field — declare multiple layering schemes, each with a glob scope that matches deployment files by path
  - `ScopedLayeringModel` — each scheme has a `name`, `scope` (glob pattern), and ordered `layers[]` list
  - First-match scope resolution — deployment file is matched against schemes in order; first match wins, no match means no layering validation
  - Shared `strata.utils.layering` module — `resolve_layering_scheme()` resolves a deployment file path to its matching scheme; `compute_artifact_path()` builds the artifact path from a scheme
  - `DeploymentService._validate_deployment_layers()` updated — uses scope resolution to pick the active scheme per deployment
  - `DeploymentService.get_artifact_path()` updated — resolves scheme, then builds path from resolved layers
  - `OverlapController._compute_artifact_path()` updated — same resolution logic for cross-manifest collision detection
  - Mutual exclusion validation — `spec.layering` and `spec.layerings` cannot both be set; validator enforces this
  - Full test coverage — overlap controller tests adapted to multi-scheme; integration tests validate both flat and scoped schemes

- **Path convention validation — ADR 0052 (completed)**
  - `spec.paths[]` field on the configuration model — declare directory structure conventions for fleet-wide path enforcement
  - `PathConventionModel` — each convention has a `name`, `scope` (glob), `pattern` with `{segment}` captures, and optional `validate` rules per segment
  - Two validation rule types: `spec.field[*].attr` for model membership lookup (e.g., declared zones); or a path template for file existence check (e.g., `customers/{tenant}/tenant.yaml`)
  - `path_convention` policy type — enforces conventions at validate phase with deny/warn/audit levels; supports per-convention filtering via `configuration.conventions`
  - Deploy-repo mode — inline convention on policy for repos without a configuration model (`configuration.scope` + `configuration.pattern`)
  - Scope matching: `fnmatch` glob; pattern matching: positional literal + capture; no-match = skip (never a violation)
  - `spec.*` rules require configuration service (deep validation); file existence rules work in surface mode
  - `file_path: Optional[Path]` added to `PolicyContext` — populated by policy engine before `evaluate()`
  - `strata.utils.path_convention` module — `match_pattern`, `resolve_spec_rule`, `evaluate_file_rule`, `evaluate_conventions`
  - 45 tests, zero regressions against 4607-test suite

- **Checkov IaC security scanning — ADR 0051 Phase 1 (completed)**
  - `checkov` policy type — runs Checkov CLI against Terraform build artifacts during the `build` phase
  - `CheckovIntegration(BaseIntegration)` — invokes `checkov --directory ... --output json --compact`; parses single and multi-framework JSON output; graceful degradation when Checkov not installed
  - `CheckovPolicy(BasePolicy)` — resolves Terraform artifact dir from `context.build_path` (deployment-scoped → `terraform/` subdir → root); applies `severity_gate` and `skip_checks` filters
  - `CheckovFinding` / `CheckovScanResult` dataclasses — structured scan result with per-finding severity, resource, file path, and guideline
  - `CheckovScanResult.findings_at_or_above(severity)` — filters findings by severity level for gate evaluation
  - `iac_security` capability added to `CAPABILITY_MAP` / `CAPABILITY_REGISTRY` with `IIacSecurityScanner` protocol
  - Registered in `IntegrationFactory._BUILTIN_CLASS_MAP` and `PolicyEngine._create()`
  - Graceful degradation: Checkov not found → skip; no `.tf` files in build path → skip; subprocess failure → skip (non-fatal, never blocks build)
  - 30 tests, zero regressions against 4637-test suite

### Changed

- **Removed hardcoded "environment" layer name constraint** — last layer no longer required to be named `"environment"`. Layer names are now arbitrary (e.g., `ring`, `stage`, `landscape` as last layer). Collision prevention is entirely owned by `OverlapController` artifact path uniqueness check, not by layer naming.
- **`spec.layering` marked as deprecated** — single-scheme flat layering still supported for backward compatibility, but `spec.layerings` is preferred for new configs. Existing deployments continue to work unchanged.

### Breaking Changes

- **Layer name constraint removal** — configurations or deployment code that relied on the final layer being named `"environment"` should be updated. The constraint was overly restrictive and served no functional purpose in artifact path generation.

- **Bicep provisioner — ADR 0046 (completed)**
  - `ProvisionerType.BICEP = "bicep"` added to the enum — Bicep is now a first-class provisioner type
  - `BicepDeployer(BaseDeployer)` — Azure-native IaC deployer using ARM deployments (no state file, no backend)
  - Steps: `setup` → `az bicep build`, `plan` → `az deployment {scope} what-if`, `apply` → `az deployment {scope} create`, `destroy` → `az deployment {scope} delete`, `output` → ARM deployment outputs
  - Four ARM deployment scopes: `resourceGroup` (default), `subscription`, `managementGroup`, `tenant`
  - `BicepDeployer` uses `AzureCLIIntegration` for all `az` calls — inherits auth check and token caching
  - `_deployment_cmd()` routes to the correct `az deployment group/sub/mg/tenant` subcommand based on scope
  - What-if result cached by `plan()` and returned by `show_plan()`; output parsed from ARM `properties.outputs`
  - Registered in `DeployerFactory._BUILTIN_MAP`
  - 27 tests, zero regressions against 4739-test suite

- **Azure CLI integration — ADR 0053 Phase 1 (completed)**
  - `AzureCLIIntegration(BaseIntegration)` — `COMMAND = "az"`; shared foundation for all Azure CLI-based operations

- **Bicep provisioner — ADR 0046 (completed)**
  - `ProvisionerType.BICEP = "bicep"` — Bicep is now a first-class provisioner type
  - `BicepDeployer(BaseDeployer)` — Azure-native IaC deployer; no state file or backend required (ARM manages state server-side)
  - Steps: `setup` → `az bicep build`, `plan` → `az deployment {scope} what-if`, `apply` → `az deployment {scope} create`, `destroy` → `az deployment {scope} delete`, `output` → ARM deployment outputs
  - Four ARM scopes: `resourceGroup` (default), `subscription`, `managementGroup`, `tenant`
  - Uses `AzureCLIIntegration` for all `az` calls — inherits auth check and token caching from ADR-0053
  - Registered in `DeployerFactory._BUILTIN_MAP`; `provisioner: bicep` valid in workspace YAML
  - `docs/config/workspace.md` updated — `provisioner: bicep` added to provisioner list with `configuration` fields documented
  - Help file: `strata help --topic bicep`
  - 27 tests, zero regressions against 4739-test suite
  - `ensure_available()` checks binary presence **and** active login (`az account show`) — surfaces "not authenticated" in Tools view immediately
  - `get_subscription()` — returns active subscription `id`, `name`, `tenantId`
  - `get_access_token(resource)` — cached bearer tokens per resource scope; avoids repeated `az account get-access-token` spawns
  - `bicep_version()` — reports Bicep extension version (`az bicep version`)
  - `run_az(args)` — passthrough for arbitrary `az` subcommands (used by upcoming Bicep deployer)
  - `IAzureTool` capability protocol + `"azure"` in `CAPABILITY_MAP`
  - Registered in `IntegrationFactory`; Tools view shows subscription name and auth status
  - 20 tests, zero regressions against 4712-test suite

- **OPA (Open Policy Agent) integration — ADR 0050 (completed)**
  - `opa` policy type — evaluates Rego rules against strata deployment context
  - Two modes: HTTP REST (`POST /v1/data/{rule}` to running OPA server) and `opa eval` CLI fallback (stateless, no server required)
  - Auto-fallback: if HTTP endpoint unreachable, falls back to CLI mode transparently
  - `OPAIntegration(BaseIntegration)` — `evaluate_http()`, `evaluate_cli()`, unified `evaluate()` entry point
  - `OPAPolicy(BasePolicy)` — serializes `PolicyContext` (platform artifact, configuration, deployment, plan data) to OPA input document; parses violations from result
  - `OPAResult` dataclass — `passed: bool`, `violations: List[str]`, `raw: Any`
  - strata does **not** manage OPA server lifecycle — binary install and server start/stop are the operator's responsibility
  - Registered in `IntegrationFactory` and `PolicyEngine`; `iac_security` capability; help file `strata help --topic opa`
  - 34 tests, zero regressions against 4671-test suite

- **Date/time format standard — ADR 0045 (implemented)**
  - `src/strata/utils/datetime_utils.py` — shared UTC datetime utilities: `now_utc()`, `to_wire_timestamp()`, `format_display_timestamp()`, `parse_iso_timestamp()`, `coerce_to_utc()`
  - All `datetime.now()` (naive) calls replaced with `datetime.now(timezone.utc)` across `base_command.py`, `sbom_build_command.py`, `schema_base_command.py`, `solution_controller.py`
  - `base_command._start_time` / `_end_time` initialised as UTC-aware in `__init__` — prevents `can't subtract offset-naive and offset-aware datetimes` errors
  - Console header timestamp now shows `UTC` suffix
  - `solution_controller` cutoff time (minutes filter) now UTC-aware — fixes silent comparison bug with UTC log entries
  - 21 unit tests for `datetime_utils`

### Changed

- **`datetime.now()` → `datetime.now(timezone.utc)` everywhere** — all internal timing and audit timestamps are now timezone-aware UTC. Wire format (`+00:00` suffix) unchanged for existing consumers.

---

## [1.3.1] — 2026-07-22

### Added

- **Cost Estimation and Visibility — ADR 0031 Phase 1 (completed)**
  - `strata cost show` command — display monthly cost estimate for a deployment using Infracost
  - `strata cost diff` command — show cost impact of terraform plan changes (before/after delta)
  - `strata cost history` command — display historical cost snapshots (up to 50 entries per deployment)
  - `ICostEstimator` capability protocol — integrations can implement cost estimation; Infracost registered as first implementation
  - `InfracostIntegration` class — invokes `infracost breakdown` and `infracost diff`; supports Azure, AWS, GCP; non-fatal if binary not installed (graceful degradation)
  - `CostController` — orchestrates cost estimation; handles multi-provisioner deployments; caches results locally (7-day TTL with content hash key)
  - `CostHistoryStore` — appends cost snapshots to `.strata/cost/{deployment}.cost-history.json`; auto-computes delta from previous snapshot; capped at 50 entries (most-recent kept)
  - `cost.json` artifact — written alongside `platform.json` in build directory with monthly/resource breakdown after `strata cost show`
  - `deploy --dry-run` auto cost diff — after terraform plan, auto-runs infracost diff to show cost impact (non-fatal, never blocks deploy)
  - `cost_threshold` policy type — blocks or warns on deployments exceeding monthly cost limit; environment pattern scoping; reads `cost.json` from build artifacts
  - Configuration YAML integration entries (azure-aks, aws-eks, gcp-gke) — Infracost declared as optional integration with cost capability
  - Full test coverage — 25 unit tests for `CostHistoryStore`, 88+ integration tests for cost commands/controllers/policies

- **VS Code Extension — deployment-centric rework (v1.3.1)**
  - **Deployment context** — active deployment is now a persistent, workspace-scoped selection (stored across sessions); all commands default to it instead of "whatever file is open"
  - **Deployments view** (new) — replaces the flat Files view; shows the selected deployment's full hierarchy: workspace → providers, provisioners, topology, namespaces → environments → configurations → policies (lazy loaded); one-click switch between deployments; `$(target) Set Active` code lens on every `kind: deployment` file
  - **Operations view** (new) — shows runtime status for the active deployment: build cache status, health, drift, lock state, cost snapshot with delta, lazy-loaded outputs per stage, and deploy history
  - **Workspace view merged** — health, readiness phases, profiles, repositories, and tools unified into one collapsible panel (was 3 separate panels: Workspace, Repositories, Tools)
  - **Status bar** updated — shows active deployment name alongside health and profile: `◎ HEALTHY  —  dev  | Phase 5/8  $(cloud)  deploy-prd`
  - **New commands**: `strata.selectDeployment` (Quick Pick from all deployment files), `strata.setActiveDeployment` (set from code lens or tree click), `strata.newFile` (guided scaffolding via `strata new`), `strata.activateProfile` (inline from Workspace view), `strata.showCostHistory` (open cost history terminal)
  - **Inline YAML manifest parsing** — deployment explorer reads workspace/environment/configuration references from YAML directly (no CLI roundtrip) to resolve file links
  - **`CostSnapshot` / `CostHistoryData` interfaces** added to `StrataClient` — wired to `strata cost history` via `getCostHistory()`
  - **Sidebar reduced** from 8 views to 6: removed `strataFiles`, `strataRepositories`, `strataTools`, `strataEnvironment`; added `strataDeployments`, `strataOperations`
  - **CI pipeline fix** — `cp LICENSE src/vscode/LICENSE` step added before `vsce package`; `--skip-license` flag removed so the AGPL-3.0 license is now bundled inside the `.vsix`

### Changed

- **Provider model `engine` field removed** — unused field that was never applied during cost estimation; cost behavior is now determined entirely by integration type and Infracost availability
- **Resource model `unit_cost` field removed** — planned for manual per-resource pricing but not implemented; Infracost integration provides automatic cost calculation instead

---

## [1.2.1] — 2026-07-20

### Changed

- **`strata new --output-file` replaces `--path`** — `--path` / `-p` renamed to `--output-file` for naming consistency with other commands
- **`strata validate run --pattern` replaces `--path`** — option renamed from `--path` to `--pattern` (`-p`) for clarity; describes glob patterns used for cross-manifest overlap validation
- **Exit code 4 for lock conflicts** — `handle_command_exit` now prioritises lock conflict detection before other failure types; `deploy run` and `deploy destroy` exit with code `4` when another process holds the deployment lock
- **`LockConflictError` / `LockTimeoutError` hierarchy** — `LockTimeoutError` is now a subclass of `LockConflictError`, enabling callers to catch either level of locking failure; exit code 4 is emitted for both

### Fixed

- `strata secret mask` — passwords or tokens starting with `-` are now handled correctly when passed as a positional argument in automated scripts (flaky test fixed; use `--` separator before the value when the secret may start with a dash)
- Sphinx docs — `decisions/` directory excluded from GitHub Pages build; removed stale toctree references that caused "not in doctree" warnings

---

## [1.2.0] — 2026-07-16

### Added

- **GitOps Controller Integration — ADR 0041 (completed)**
  - `argocd` and `flux` provisioner types — integrate GitOps controllers as first-class deployment stages with no new CLI commands
  - `SyncBackendModel` — `backend.integration` (names the integration instance) and `backend.remote` (names the git remote for rendered output) on deployment stages
  - `namespace` field on `DeploymentStageModel` — scopes sync provisioner output to a declared workspace namespace
  - `ProvisionerType.ARGOCD` / `ProvisionerType.FLUX` added to the enum; `_SYNC_PROVISIONER_TYPES` frozenset used throughout for sync-aware branching
  - `ReconciliationResult` dataclass — shared health result type: `sync_status`, `health_status`, `last_synced_at`, `revision`, `intended_revision`, `drift`, `message`
  - `SyncBuilder` — reads platform artifact, finds sync stages, renders user-editable Jinja2 templates (`StrictUndefined`), writes output files; wired into `strata build run`
  - `BaseSyncDeployer`, `ArgocdDeployer`, `FluxDeployer` — step-based deployers; apply step commits rendered output to git remote; health step queries controller API (`GET /api/v1/applications/{name}` for ArgoCD, `kubectl get kustomizations` for Flux)
  - `strata deploy health` auto-detects sync stages and queries reconciliation status alongside infrastructure health — no flags required
  - Cross-reference validation in `DeploymentService._validate_sync_stages()`: `backend.integration` must exist in configuration with `sync` capability; `backend.remote` must be in the merged repo map; `namespace` must match a declared workspace namespace
  - `_validate_sync_stages()` tests — 12 test cases in `TestValidateSyncStages`
  - Sync Jinja2 adapter templates scaffolded by `strata sln init` / `sln update`: `.strata/templates/sync/argocd-appset-entry.json.j2`, `flux-kustomization.yaml.j2`, `README.md`
  - `.j2` files skipped by `TemplateProcessor.render()` in scaffold methods — raw Jinja2 templates are copied verbatim so end-users can use template syntax freely

- **Platform artifact convenience fields (ADR 0041 — Decision 8)**
  - 8 new computed fields on `PlatformSpecModel` populated by the platform builder: `name`, `labels`, `annotations`, `layers`, `chart_versions`, `image_versions`, `resolved_variables`, `revision`
  - `revision` — `git rev-parse HEAD` at build time (best-effort, `None` outside git repos)
  - `resolved_variables` — non-secret variables only; secrets never appear in template context
  - `chart_versions` / `image_versions` — flat `name → value` dicts for ergonomic Jinja2 access

### Changed

- **`WorkspaceIacModel.source` is now optional** — required for IaC provisioner types (`terraform`, `ansible`, `helm`, `compose`, `script`) via `validate_provisioner_fields()` model validator; optional for sync types (`argocd`, `flux`) which generate output from the platform artifact
- **`DeploymentService._merged_repo_map()`** — new helper merges configuration-level remote map with solution-level repo map (solution names take precedence); replaces three inline dict merges in `_validate_dynamic()`
- `ansible_deployer.py` / `terraform_deployer.py` — removed redundant `iac.source and` null guards; added `assert iac.source is not None` (model validator guarantees this for non-sync provisioners, satisfies mypy)
- ADR 0041 status → `completed`; ADR 0011 status typo fixed (`Implementated` → `completed`)
- ADR status taxonomy extended: 7 ADRs updated from `proposed` to `partial` (0031, 0034, 0035, 0037, 0039, 0040, 0042)
- `docs/guides/features.md` — added `argocd`/`flux` to provisioner table with GitOps explanation; added Version management and Promotions sections

### Fixed

- ADR 0041 code fences changed from ` ```jinja2 ` to ` ```jinja ` — `jinja2` is not a valid Pygments lexer name; fixes Sphinx docs build warnings

---

## [1.1.1] — 2026-07-15

### Added

- **`strata new --validate` flag**
  - New `--validate` / `-v` flag on `strata new` validates each generated file immediately after creation using `PlatformValidator`
  - Runs `before_validate → validate → after_validate` lifecycle on every produced file (single-file and bundle modes)
  - Validation errors are appended to command output; generated files are preserved for manual correction
  - Exit code reflects combined result of generation + validation

### Changed

- **BaseCommand lifecycle — ADR 0030 (completed)**
  - All command `_run()` overrides migrated to `_execute()` across the entire command layer (~80 files)
  - `execute()` is now a concrete sealed method on `BaseCommand`; subclasses must not override it
  - `INIT_REQUIRED` ClassVar removed; workspace-optional commands call `_initialize_session()` instead of `super()._initialize()`
  - `_initialize_session()` added to `BaseCommand` — mirrors `_initialize()` but does not error when `solution.json` is absent
  - Three regression guards added to `scripts/Check.ps1`: no `INIT_REQUIRED`, no `execute()` overrides, no `_run()` definitions
  - `CONTRIBUTING.md` updated with new command authoring pattern

### Fixed

- `cli_ref.py` — `super()._run()` call inside `_execute()` updated to `super()._execute()` (leftover from ADR 0030 migration)
- `sbom_build_command.py`, `drift_deploy_command.py` — removed unnecessary `f` prefix from string literals with no placeholders (ruff F541)
- `run_new_command.py` — mypy `Optional` reassignment on `context` variable resolved via separate `prompted` variable

### Design & ADR Progress

- **ADR-0030** (Command lifecycle explicitness and thin overrides) — status updated to `completed`
- **ADR-0043** (Tenant offboarding — `strata remove tenant`) — new ADR, status `proposed`

---

## [1.1.0] — 2026-07-14

### Added

- **Environment Provider Overrides (ADR 0036)**
  - `EnvironmentProviderOverrideModel` — environments can now override provider file bindings per provider name, supporting both file swaps and configuration-level property overrides
  - `spec.overrides.providers[].file` — replace entire provider YAML file per environment (enables region/cloud-account variants without workspace duplication)
  - `spec.overrides.providers[].configuration` — overlay specific provider properties without maintaining separate provider files
  - Provider file validation — when an override loads a new provider file, `meta.name` is validated to match the workspace provider name (hard error if mismatched)
  - `EnvironmentService.get_overridden_provider_names()` — returns set of provider names with overrides
  - `EnvironmentService.get_provider_override()` — accessor for override model by provider name
  - `DeploymentService.apply_environment_overrides()` — applies file swaps + configuration overlays during deployment build
  - Full test coverage: model validation tests (file-only, configuration-only, combined), service merge tests, provider file resolution tests
  - Documentation: `docs/config/environment.md#Provider-Overrides` with multi-region examples; `docs/config/provider.md#Environment-Specific-Provider-Overrides` with cross-reference

- **Environment Promotion Strategies** (preparation for ADR 0011)
  - `PromotionStrategyModel` — framework for multi-environment promotion workflows (dev → staging → prod)
  - `spec.promotion` field on environments to define advancement criteria and gates
  - Promotion validator — ensures environment sequences are valid and acyclic
  - CLI ready for future `strata promote` subcommand

- **Version Management & Release Tooling**
  - `scripts/Release.ps1` now supports version parameter: `-Version X.Y.Z` for automated version bumping
  - `VERSION.txt` updated to `1.1.0` with corresponding git tags
  - Changelog versioning aligned with semantic versioning for clarity on minor vs patch releases

- **Tenant Scaffolding Bundle Template**
  - New workspace-local bundle template `.strata/templates/tenant/` enables rapid tenant provisioning for multi-customer deployments
  - `strata new tenant <name>` generates complete tenant structure: tenant config file + dev/qa/prd environments with provider overrides
  - Supports `{{ name }}` variable substitution in generated tenant codes, file paths, and descriptions
  - Eliminates copy-paste for 200+ customer onboarding; teams maintain template in their workspace, not shipped with strata
  - Includes auto-generated README and CHECKLIST for onboarding workflow
  - Fixed `strata new` to exclude `template.yaml` metadata from bundle output
  - Fixed `strata new --list` to properly display workspace bundle templates alongside single-file templates

### Changed

- **`strata new --list` workspace template priority** — bundle directories in `.strata/templates/` now take precedence over same-named single-file templates in display (matching resolution precedence)

- **Provider Override Handling** — provider resolution now includes fallback chain: workspace default → environment override file → environment override configuration
- **Deployment Build Output** — plan summaries now show provider resolution details (file loaded, overrides applied per stage)

### Design & ADR Progress

- **ADR-0036** (Workspace, Provider, and Environment-level Provider Overrides) — status updated to `completed`
- **ADR-0011** (Promotion strategies) — status updated to `in-progress` (model framework added, CLI TBD)

---

## [1.0.1] — 2026-07-09

### Added

- **Tenant — `spec.environments` now applied at build time**
  - `DeploymentService.load_deploy_services` prepends `tenant.spec.environments` to the deployment's own environment list before merging, so tenant tier files (e.g. `environments/tiers/enterprise.yaml`) are applied as a base layer that deployments can override
  - `PlatformTenantModel` now carries an `environments` field so the build artifact records which base files were applied

- **Tenant — `spec.properties` and `spec.custom`**
  - New optional fields on `TenantSpecModel`; merged as base layers into every deployment's `spec.properties` / `spec.custom` — deployment values take precedence on any overlapping key
  - `PlatformTenantModel` carries both fields for artifact traceability
  - `TenantService` exposes `get_properties()` and `get_custom()` accessors

- **`sln deployment` subcommand group**
  - `sln deployment add <path>` — register a deployment YAML file in the solution
  - `sln deployment remove <name>` — remove a registered deployment by name
  - `sln deployment list [--name]` — list registered deployments (JSON output supported)
  - `sln deployment scan [path]` — recursively discover and register `kind: deployment` files

- **`docs/config/tenant.md`** — new reference doc covering schema, field descriptions, `properties`/`custom` vs `configuration` distinction, environment layering order, phase validation rules, and zone policy behaviour

- **Environment provider overrides** (`docs/config/environment.md`)
  - `spec.overrides.providers[].file` — swap the entire provider binding per environment; recommended for targeting different regions or cloud accounts
  - `spec.overrides.providers[].configuration` — override individual provider properties on top of the resolved provider file, without maintaining separate provider files per environment
  - Build plan output now includes provider resolution details (which file was loaded and which overrides were applied per stage)

- **ADR 0026 — Resolved-model cache** (proposed) — SQLite-backed cache for fleet-wide command performance; documents cache key computation, invalidation strategies, per-kind TTL, and VS Code extension integration for background cache warming

### Fixed

- **Tenant `spec.environments` was declared and validated but never applied** — the field existed in the schema since the initial tenant model, paths were checked on disk during Phase 2, but the files were never actually merged into the build pipeline (`get_environments()` was defined but never called)

### Changed

- **Tenant `spec.environments` field description** — clarified to explicitly state these are *base environment files merged before the deployment's own environments* (not a list of environments the tenant belongs to); both the model docstring and the scaffolding templates updated
- **`sln` subcommand set** — `deployment` group added; `test_sln_subcommands_registered` updated accordingly
- **Strata Workspace Agent** — rule added: all temporary files must be written to `.strata/temp/`, not the workspace root; `.strata/temp/` documented in the workspace layout

### Design & ADR Progress

- **ADR-0013** (Auto-generated secrets) — status updated to `completed`
- **ADR-0014** (Onboarding experience) — status updated to `completed`
- **ADR-0026** (Resolved-model cache) — added as `proposed`

---

## [1.0.0] — 2026-07-08

### Added

- **S3 Lock Backend (ADR 0007)**
  - `S3LockBackend` — distributed deployment lock using AWS S3 object conditional writes; supports TTL, lock metadata (holder, acquired_at), force-release
  - Full test suite covering acquire, release, status, force-release, TTL expiry, and concurrent contention scenarios

- **VS Code Extension — Complete Feature Parity with CLI**
  - **Values Inspector** (`strataValues` tree view) — new panel showing all resolved deployment values with secret masking, source tracking, resolved/unresolved indicators, and copy-to-clipboard
  - **Lock Status & Release** — `strata.lockStatus` shows live lock holder and TTL; `strata.releaseLock` force-releases with confirmation dialog; lock badge (🔒) shown on deployment items in Environments panel
  - **Drift Detection** — `strata.envDrift` runs `deploy drift run`; ⚠ drift badge shown on deployment items after detection
  - **SBOM Generation** — `strata.buildSbom` runs `build sbom` with progress notification; offers to open the generated `sbom.json`
  - **Stage-targeted Deploy** — `strata.deployStage` prompts for stage name, supports dry-run; right-click on stage items in Environments panel
  - **Repository Write Operations** — sync (with spinner), remove (with confirmation), add (input boxes for name + path) from the Repositories panel
  - **Audit Filter & Limit** — `strata.auditFilter` cycles all/success/failures; `strata.auditSetLimit` sets entry count (5–200)
  - **Workspace Panel** — `strataWorkspace` tree view fully implemented: active profile, repositories, document paths, tool availability (was entirely stubbed)
  - **Chat Participant** — `/build` and `/deploy` now execute via action buttons (▶ Dry Run / ⚡ Full Build / 🚀 Full Deploy); new `/stage`, `/values`, `/drift` slash commands
  - **Task Provider** — SBOM task added to auto-discovered VS Code tasks per deployment manifest
  - **Editor context menus** — Show Values, Generate SBOM, Lock Status available on `.yaml` files
  - **13 new commands** registered: `deployStage`, `lockStatus`, `releaseLock`, `showValues`, `copyValueKey`, `buildSbom`, `syncRepo`, `addRepo`, `removeRepo`, `auditFilter`, `auditSetLimit`

- **Umbrella JSON Schema (`strata.json`)**
  - `_generate_schemas()` now produces `.strata/schemas/strata.json` alongside per-kind schemas
  - Single `if/then/else` discriminated-union schema routes to the correct per-kind schema based on the `kind:` field value
  - `yaml.schemas` in workspace settings and solution template reduced from 12 separate entries to one `strata.json` entry — kind-based validation regardless of file location
  - Generated automatically on `strata sln init` and `strata sln update`

- **MCP Server** — `_run_command` envelope now derives `success` from `not cmd.has_errors()` instead of `cmd.execute()` return value — fixes `None` success on commands that don't explicitly return a bool

### Changed

- **Exception handling & logging refactor (#187)** — unified exception capture, structured logging, and command execution methods across all BaseCommand subclasses
- `.gitignore` replaced 561-line Visual Studio template with a lean Python/infra-focused file; adds `*.egg-info/`, `src/vscode/out/`, `docs/_build/`, `.coverage*`, `htmlcov/`, `**/.strata/cli.yaml`, `**/.strata/audit.log`, `**/.strata/solution.json`
- `config/azure-aks/.strata/cli.yaml` removed from git tracking (runtime-written file)
- Workspace `yaml.schemas` uses the new umbrella `strata.json` — one entry replaces twelve

### Design & ADR Progress

#### ADR-0013: Auto-generated Secrets — Model Acceptance Criteria Updates
- [x] **Model field**: `SecretStoreModel.rotate: Optional[SecretRotateSpec]` (sibling of `generate:`)
- [x] **Validator 1**: `policy: rotate` without `generate:` → Pydantic validation error
- [x] **Validator 2**: `max_age` is `int` (days, >= 1) with field_validator enforcing range
- [ ] **YAML examples**: All docs examples using integer `max_age` (audit of docs still needed)

**Status**: Model layer 100% complete. Remaining: documentation consistency sweep across ADR-0013 and companion docs.

#### ADR-0011: Promotion Strategies — Phase 3 Design Gaps Identified
Five blocking issues identified for Phase 3 (automation: `start`, `rollback`, `history`):

- **#11** — Phase 1 `status`/`matrix` wrongly describe reading `spec.version`; should read `spec.overrides.remotes[].reference` (correct field)
- **#12** — No deployment discovery mechanism for `promote start --to production`; three options proposed (explicit flags, directory scan, solution registry)
- **#13** — Wave-to-file mapping ambiguous; conflation of `kind: tenant` vs `kind: environment`; three resolution options
- **#14** — Rollback depends on gitignored `.strata/promotions/` activity log; three recovery options proposed
- **#15** — Single-layer configs (`scope: tenant` matches 0 deployments); needs explicit graceful degradation behavior

**Status**: Phase 1 (read-only) and Phase 2 (model + validation) unblocked. Phase 3 deferred pending resolution of #11–#15.

#### ADR-0018: SIEM Integrations — Layer 4 Completed
- [x] **ELK Syslog Integration** (`ElkSiemIntegration`) — dual-protocol: TCP (Logstash) + HTTP (Elasticsearch bulk)
- [x] **OpenTelemetry Integration** (`OtelSiemIntegration`) — OTLP/HTTP JSON; no SDK dependency needed
- [x] **Factory registration** — both types registered as `"elk"` and `"otel"`
- [x] **Tests** — full coverage in `test_elk_siem_integration.py` and `test_otel_siem_integration.py`

**Design note**: Uses integration-reference model (`integration: <name>` in `AuditSinkModel`) rather than built-in sink types. Both can forward to same ELK stack independently of `LogstashHandler` (operational logs via TCP vs. compliance audit events via HTTP).

#### ADR-0022: CEF Syslog Format — Implementation Complete
- [x] **Model field**: `AuditSinkModel.format: Optional[str]` — syslog sink accepts `"json"` (default) or `"cef"`
- [x] **CEF encoder** — `AuditController._format_cef(data)` → CEF:0 header + 6-field extension (rt/src/dst/act/externalId/msg)
  - Severity: `3` (Low) on success, `7` (High) on failure
  - Proper escaping of pipes/backslashes per CEF spec
- [x] **Syslog routing** — `_send_syslog(data, address, fmt)` routes to formatter based on `fmt` parameter
- [x] **CLI flag** — `--siem <name>` on `strata audit export` for on-demand SIEM forwarding by integration name
- [x] **Tests** — `test_syslog_sink_passes_cef_format`, `TestFormatCef` class (header, severity, escaping, extension fields)

**Status**: Ready for v1.0. CEF output validated against CEF 0 specification.

---

## [0.16.1] — 2026-07-06

### Fixed

- Moved `doc` from `[project.optional-dependencies]` to `[dependency-groups]` in `pyproject.toml` — resolves "group doc is not defined" error in `uv sync --group doc` introduced by uv's stricter group/extra distinction
- Updated `Dockerfile.docs` and CI docs job to use `--group doc` instead of `--extra doc`

---

## [0.16.0] — 2026-07-05

### Added

- **Environment Composition: Flat Merge Fix (ADR 0024)**
  - `EnvironmentService.merge_envfiles()` now merges **all 8 spec sections** (previously only 3): variables, secrets, features, properties, custom, lifecycle, audit, and all override subsections
  - Per-section merge strategy documented and implemented: last-wins by key for variables/secrets/features, shallow dict-merge for properties/custom, wholesale last-wins for lifecycle/audit, resource/provider/remote last-wins by name, includes/output_files are additive with deduplication
  - `MergeProvenance` dataclass tracks which environment file contributed each key — populated during merge, carried through `ResolvedValues` to CLI output
  - Multiple environment files enable composition pattern: base + region + environment layers for DRY configuration
  - `strata values list --trace` flag shows provenance: which file each variable/secret/feature originates from in console table and JSON output
  - `merge_order` exposed in `values list` JSON output — list of files in merge sequence
  - 21 comprehensive tests covering all merge strategies, override merging, features by-key semantics, and provenance tracking
- **Environment Composition Guide** (`docs/guides/environment-composition.md`)
  - When and why to compose; merge semantics per section; base + region + prd example with effective-result table
  - `strata values list --trace` usage with console and JSON examples; merge order visualization
  - Common patterns: base + region + environment, shared security policy, tenant overlays
  - Troubleshooting and validation notes
- **Updated merge order documentation**
  - `docs/config/deployment.md` Configuration Merge Order section now includes precise per-section table and `--trace` usage link
  - `docs/config/environment.md` new Multi-file Composition section with strategy table
- **Index coverage** — environment-composition guide added to `docs/index.rst`

### Changed

- `EnvironmentService.merge_envfiles()` now returns `Tuple[EnvironmentModel, MergeProvenance]` instead of just `EnvironmentModel`
- `deployment_service.py` unpacks and stores provenance from merge; exposed via `get_merge_provenance()` getter
- `ResolvedValues` dataclass now includes `variable_sources`, `secret_sources`, `feature_sources`, and `merge_order` dicts
- `ValueController.resolve_values()` populates provenance sources from deployment's merge provenance

### Fixed

- `merge_envfiles()` previously dropped properties, custom, lifecycle, audit, and all override sections from every file after the first
- Features now merge by key (last-wins per flag) instead of whole-file replacement

### Documentation

- Added ADR 0024: Environment Composition — Flat Merge Fix
- New comprehensive composition guide with patterns and examples
- Updated config reference docs with merge semantics and `--trace` usage

### Testing

- 3771 tests passing (3750 existing + 21 new merge/provenance tests), 15 skipped, 1 warning

---

## [0.15.0] — 2026-07-03

### Added

- **Configurable Terraform build output profiles (ADR 0019)**
  - New `output:` block on Terraform provisioners in `workspace.yaml` — controls what `.auto.tfvars.json` files `strata build run` produces
  - `format` modes: `strata` (default, backward compatible), `custom` (emit only what `emits[]` and `files[]` specify), `script` (one user script owns all output), `none` (suppress tfvars output entirely)
  - `emits[]` gate — selectively enable/disable each built-in emit category (`features`, `variables`, `properties`, `workspace`, `providers`, `topologies`, `modules`, `namespaces`, `firewalls`, `dns`, `networks`, `resources`, `tenant`)
  - `files[]` custom file definitions — source mode (pass-through a key from merged properties/custom dict) and script mode (per-file Python/shell script with `STRATA_PLATFORM_PATH`, `STRATA_BUILD_PATH`, `STRATA_OUTPUT_PATH`, `STRATA_OUTPUT_FILE`, `STRATA_WORKSPACE_PATH`, `STRATA_DRY_RUN` env vars)
  - `format: script` top-level — single script receives `STRATA_PROVISIONER` additionally
  - `output_files` override on `EnvironmentOverridesModel` — additive extra file definitions per environment; cannot remove or replace workspace-level definitions; collision warnings logged
  - Backend configuration expression resolution — `${var:KEY}` and `${secret:KEY}` placeholders now resolved from `resolved_values` at deploy time
  - Two-phase emission: `features` and `variables` written at build time (constant/env-store only); integration-backed entries re-written by deployer immediately before `terraform init`
  - Security invariant enforced: secrets are never written to any `.auto.tfvars.json` file regardless of configuration; always injected as `TF_VAR_*` environment variables only
  - 43 new tests covering profile models, `_planned_files`, feature/variable extraction, property merging, deploy-time vars, and backend expression resolution
- **`strata env status`** — renamed from `strata env state` for naming consistency; added `--all` and `--path DIR` flags to scan all deployment manifests in the workspace without requiring a single `-f FILE`
- **`strata audit changes/resend --output json`** — now emits the standard CLI JSON envelope (`{ success, command, execution_id, timestamp, data, messages, errors }`) instead of a raw array, consistent with every other command
- **Audit Trail documentation** — new `## audit` section in `docs/platform/commands.md` covering `audit changes`, `audit resend`, `audit export`, audit path template configuration, and SIEM sink YAML examples for Sentinel, ELK, and OTel
- **VS Code extension — Environments panel** (`strataEnvironment` tree view): shows all deployment manifests with cached build-output status; per-stage drill-down with last-cached timestamp and output count; click to open manifest file
- **VS Code extension — Audit Trail panel** (`strataAudit` tree view): shows the last 20 deploy-log entries with success/failure icons, timestamps, duration, and environment; expands to per-stage results (provisioner, duration, step-level detail), PR enrichment (clickable link), and commit SHA
- **VS Code extension — env commands**: `strata.envStatus` (cached, offline), `strata.envDrift` (terraform plan), `strata.envDoctor` (inline notification with pass/warn/fail counts)
- **VS Code extension — audit commands**: `strata.auditChanges` (terminal), `strata.auditResend` (terminal), `strata.auditExport` (save dialog → JSON or NDJSON file)
- **VS Code extension — code lens** on deployment files: `$(pulse) Status`, `$(diff) Drift`, `$(history) Audit` lenses added after Deploy (Dry Run)
- **26 new tests** for `env status` CLI wiring, multi-deployment scanning, and cache detection (`tests/strata/commands/test_commands_env.py`)

### Changed

- `strata env state` → `strata env status` (command renamed; old name removed)
- `strata audit changes --output json` data shape changed from raw array to `data: { entries: [...], count: N }`
- `strata audit resend --output json` data shape changed from `{"sent": N, "failed": N}` to standard envelope with same payload in `data`

### Fixed

- `_save_terraform_vars` previously wrote identical tfvars to every Terraform provisioner path; now iterates per-provisioner and applies per-provisioner output profile

### Documentation

- Added ADR 0019: Configurable Terraform Build Output
- Updated `docs/config/workspace.md` with full Build Output Profile reference: format modes table, `emits[]` categories, source-mode and script-mode file examples, two new worked examples
- Updated `docs/config/environment.md` with `spec.overrides.output_files[]` documentation

### VS Code Extension

- Workspace snippet updated: Terraform provisioner block now includes `output: / format:` stub with comment showing all valid modes

### Testing

- Full test suite passing: 1060+ passed, 0 failed

---

## [0.14.0] — 2026-06-26

### Added

- **Deployment Audit and Traceability (ADR 0018)**
  - Audit policy/sink configuration model for environment and configuration YAML (`AuditConfigModel`)
  - SIEM capability protocol (`ISiemSink`) and audit capability wiring in integration capabilities map
  - SIEM integrations:
    - `SentinelIntegration` (Azure Logs Ingestion API)
    - `ElkSiemIntegration` (TCP JSON and HTTP bulk)
    - `OtelSiemIntegration` (OTLP/HTTP logs)
  - Shared output directory resolver utility (`OutputWriter`) for structured and versioned outputs
  - `ManifestController` to handle deployment manifest push-to-remote workflow
  - Comprehensive SIEM integration test suite for base behavior and sink-specific behavior

### Changed

- `RunDeployCommand` now resolves and injects integration-backed SIEM sinks for deploy-log forwarding
- `AuditController.forward_to_siem()` now supports both built-in sinks and integration-backed sinks
- Deployment manifest push path now uses `ManifestController` from deploy command flow
- Output path resolution was consolidated through `OutputWriter` across audit/deploy-manifest flows
- Integration model now supports sink-specific `properties` payloads for extensible SIEM configuration

### Fixed

- Fixed deploy-manifest push test expectations and mocking path for manifest remote push
- Restored missing imports/constants that caused broad command/service test failures
- Resolved lint and type issues found during full validation (including variable naming and union-attr checks)
- Corrected docs content that caused Sphinx lexer/parsing failures in ADR documentation

### Documentation

- Added ADR 0018: Deployment Audit and Traceability
- Updated related docs content to keep Sphinx build clean

### Testing

- Full test suite passing: 3382 passed, 3 skipped, 0 failed
- All checks passing via `scripts/Check.ps1`:
  - ruff lint
  - ruff format
  - mypy
  - smoke checks
  - docs index coverage
  - sphinx build

## [0.13.0] — 2026-06-24

### Added

- **Guided Onboarding Experience (ADR 0014)**
  - `strata console` — interactive REPL session with prompt_toolkit (status, check, next, do, new, validate, graph, tools, open, reload, templates, help)
  - `GuideController` extracted from `GuideCommand` for stateful workspace analysis
  - `strata validate graph` — Mermaid dependency graph visualization with live validation status
  - `strata validate --explain` — plain-English summary of what a validated file does
  - Validation error fix suggestions with "did you mean?" for misspelled fields
  - `strata validate --path "**"` — batch validation of all workspace YAML
  - `strata sln init --list` — discover available init templates
  - `strata new --list` — shows bundles with descriptions
  - Rich rendering (panels, tables, progress indicators) in guide REPL
  - Standalone LLM skill file (`docs/skills/strata-onboarding.md`) bundled into init scaffold at `.github/skills/`
  - CI template validation test — all built-in templates validated on every run
  - `config/` formalized as reference example workspace with README annotations and CI validation
  - Contributing guide section for adding community example workspaces

### Fixed

- Template bundles: replaced invalid `type:` fields on stages with `provisioner:` (Pydantic `extra="forbid"` compliance)
- Validation error messages: `extra_forbidden` now names the offending field
- Template scaffold: `deploy.yml` wrapped in `{% raw %}` to prevent Jinja2 conflicts with GitHub Actions `${{ }}` expressions
- Template scaffold: `_substitute()` now handles both `${key}` and `{{ key }}` placeholder syntax
- Model tests: updated references from deleted `config/xyz-configuration/` to new cloud-provider examples

### Documentation

- Added ADR 0014 (Guided Onboarding and Cold-Start Experience)
- Updated CONTRIBUTING.md with example workspace contribution guidelines

---

## [0.12.0] — 2026-06-23

### Added

- **Secret generation utilities (`strata/utils/secret_generator.py`)**
  - `generate_secret(fmt, length)` — cryptographically secure generation for formats: `urlsafe`, `hex`, `alphanumeric`, `password`, `numeric`, `base64`, `uuid4`, `uuid7`
  - `mask_secret(value, show, char)` — safe masking for log/output display; moved from `commands/` into shared utils so controllers can import without violating layer rules
  - `generate_secret_command.py` and `mask_secret_command.py` converted to re-export shims

- **Auto-generated secrets (ADR 0013)**
  - `SecretGenerateSpec` on `SecretStoreModel` — declare a generator spec alongside the secret reference
  - `ValueController._resolve_secret` — generate-on-missing: if a secret is absent and `generate:` is declared, strata generates a value, writes it to the backing store, and returns it
  - Race-safe: if `set_secret` fails but a concurrent write is detected via re-read, the existing value is used without error

- **Seed-on-missing for variables and feature flags**
  - `VariableStoreModel.default` — if a variable key is absent from an integration-backed store, strata writes the declared default and returns it
  - `FeatureStoreModel.default` — same pattern for feature flags; default parsed as `"true"`/`"false"` string to boolean
  - Race-safe re-read fallback on write failure for both types

### Changed

- `ValueController._resolve_variable`, `_resolve_secret`, `_resolve_feature` — return signature extended from `(value, error)` to `(value, error, note)` to carry seed/generate annotations without polluting error lists
- `ResolvedValues.for_stage` — filters `secret_notes` alongside secrets when building a stage-scoped copy

### Fixed

- Error message for `password` format minimum length corrected from `"--length >= 4"` to `"length >= 4"` in both CLI output and test assertions

### Documentation

- `docs/guides/faq.md` — restructured as high-level explainer (what is strata, how it works with Terraform/Ansible/Helm)
- `docs/guides/config-faq.md` — new; configuration-specific questions (SSH key setup, existing Terraform state adoption, multi-stage deployment YAML, rollback procedure)
- `docs/guides/features.md` — new; practical capability overview aimed at DevOps engineers evaluating strata
- `docs/decisions/0013-auto-generated-secrets.md` — ADR for auto-generated secret design

### Testing

- `tests/strata/utils/test_utils_secret_generator.py` — new; covers all `generate_secret` formats and `mask_secret` edge cases
- `tests/strata/commands/secret/` — removed; unit tests relocated to `utils/`, CLI tests consolidated into `test_commands_secret.py`
- `tests/strata/commands/test_commands_secret.py` — renamed from `test_cli_secret.py` to match project convention
- `tests/strata/controllers/test_controllers_value.py` — updated all `_resolve_*` call sites to unpack 3-tuple `(val, err, _)`

---

## [0.11.0] — 2026-06-23

### Added

- **Promotion Strategies System (ADR 0011)**
  - Named progressions: ordered lists of environments for version promotion
  - Named strategies: policies that govern promotion waves and guardrails
  - Wave assignment on deployments via `spec.promotion.wave` (iteration, match_labels, or default)
  - Scope predicates: layer-based filtering for promotion targets
  - CLI command group: `strata promote` (start, rollback, status, matrix, history, log)
  - Activity log: `.strata/promotions/` for audit trail (gitignored)
  - Promotion-record in artifact store for state tracking

### Changed

- **Tenant Naming (ADR 0012) — BREAKING CHANGE**
  - Renamed concept: `customer` → `tenant`
  - Kind: `customer` → `tenant`; Model: `CustomerModel` → `TenantModel`; Service: `CustomerService` → `TenantService`
  - Policy: `customer_zone` → `tenant_zone`; Directory: `customers/` → `tenants/`
  - Field: `spec.customer` → `spec.tenant`; Properties: `properties.customer` → `properties.tenant`
  - Terraform variables: `customer.auto.tfvars.json` → `tenant.auto.tfvars.json`
  - Ansible hostvars: `strata_customer` → `strata_tenant`

### Migration Guide (v0.10.0 → v0.11.0)

```bash
mv customers/ tenants/
# Update kind: customer → kind: tenant and spec.customer → spec.tenant in all YAML files
```

### Documentation

- Updated all platform docs to reflect tenant terminology
- Added ADR 0011 (Promotion Strategies), ADR 0012 (Rename Customer → Tenant)
- Updated `at-scale.md` with multi-tenant design patterns

---

## [0.10.0] — 2026-06-22

### Added

- **`strata deploy show`** — new command that loads and displays the fully resolved deployment configuration (workspace, environments, variables, secrets, features) without executing. Useful for auditing what a deployment will use before running it.
- **`strata deploy list`** — new command that recursively scans a directory for `kind: deployment` YAML files and emits a flat table of all deployments with their layer fields promoted to top-level columns. Designed for CI matrix generation — pipe the JSON output directly into a GitHub Actions matrix strategy via `jq -c '.data.deployments'`.
- **`strata new` bundle templates** — `strata new <template>` now resolves bundle templates (directories) in addition to single-file templates. A bundle directory mirrors the desired output tree; `${var}` substitution is applied to both file contents and path segments. Workspace bundles override package bundles by the same name.
- **Overlap validation (`strata validate --path`)** — new `--path GLOB` option validates multiple deployment manifests for cross-manifest conflicts: duplicate artifact paths, Terraform backend collisions, and namespace overlaps across deployment layers. The non-overlap guarantee is now machine-checkable.
- **Remote reference overrides** — environment files now support `spec.overrides.remotes[]` to pin a specific remote to a version, tag, or branch for that environment only. The base reference is defined once in the configuration remote; deviations are explicit per-environment overrides. `BaseBuildCommand` checks out remotes to their effective reference at build time.
- **`OverlapController`** — new controller that orchestrates cross-manifest overlap checks (artifact paths, Terraform backends, namespaces). Used by `strata validate --path`.
- **`RepositoryController.ensure_remote_refs`** — new method that checks out all remotes in a deployment to their effective reference before a build begins.
- **`GitIntegration`** — new methods: `fetch`, `checkout`, `resolve_commit_sha` for fine-grained remote management.
- **`NamespaceType` enum** — `dedicated` vs `shared` namespace types added to the namespace model, affecting overlap validation behavior (shared namespaces are excluded from uniqueness checks).
- **Docs:** New guide section "Variable Flow: Customer Metadata → Terraform" in `docs/guides/at-scale.md` — documents the full chain from `customer.yaml spec.configuration` through environment variable stores to `TF_VAR_*` injection with three concrete patterns (tier-wide constants, per-customer overrides, CI-injected values).
- **Docs:** New section "Deploying ArgoCD ApplicationSets" in `docs/config/deployment.md` — covers the recommended pattern for managing ArgoCD via `server.additionalApplications` / `extraObjects` helm values, with full workspace + environment + override YAML examples.
- **Docs:** `strata deploy list` documented in `docs/platform/commands.md` with usage, option table, JSON output shape, and a complete GitHub Actions matrix workflow snippet.
- **Docs:** Workspace-per-layer pattern documented in `docs/guides/at-scale.md` (three workspaces: bootstrap, infrastructure, application; why one workspace per layer; 400-deployment example).

### Changed

- **`strata deploy output`** — unified output handling; stored artifact support enhanced.
- **Environment model** — `spec.overrides.remotes[]` field added with `RemoteOverrideModel` (name, reference, description). Duplicate remote names within one environment rejected at parse time.
- **`DeploymentService`** — applies remote reference overrides when resolving environments; effective reference for each remote is the environment override if present, otherwise the configuration default.

### Dependencies (GitHub Actions)

- `astral-sh/setup-uv` bumped from v2 to v7
- `actions/setup-python` bumped from v4 to v6
- `peaceiris/actions-gh-pages` bumped from v3 to v4
- `actions/checkout` bumped from v4 to v7

---

## [0.9.3] — 2026-06-20

### Added

- **HelmBuilder:** `meta.yaml` now includes chart coordinates (`chartName`, `chartVersion`, `chartRepository`) for registry-based modules. Build artifacts are fully self-contained — deployers and external tools can drive `helm upgrade` without re-reading the module spec.
- **HelmBuilder:** `spec.configuration` (module-level) is now merged into `values.yaml`. For service-less modules this creates the values file; for modules with services it merges on top as overrides.
- **HelmBuilder:** Service-less helm modules (registry chart + values file pattern) now produce `meta.yaml` correctly. Previously these modules were silently skipped.
- **HelmDeployer:** `--wait`, `--atomic`, and `--timeout 5m` flags added to `helm upgrade --install`. Deploys now block until pods are healthy and auto-rollback on failure.
- **Docs:** New guide `docs/guides/helm-modules.md` covering the full helm lifecycle (define, build, deploy, GitOps integration).

### Changed

- **HelmDeployer:** Chart coordinates are now read from `meta.yaml` instead of `module.spec.source` at deploy time. The deployer no longer depends on the module YAML for chart resolution — only the build artifact.
- **TerraformIntegration:** All integration methods (`init`, `validate`, `plan`, `apply`, `destroy`) now forward `**kwargs` to `_run_integration`, enabling `line_callback` for streaming output.
- **TerraformIntegration:** `plan()` passes `ok_returncodes={2}` when using `-detailed-exitcode`, suppressing the spurious "Integration command failed" warning for exit code 2 (success with changes).
- **BaseIntegration:** `_run_integration` accepts `ok_returncodes: Optional[set]` parameter to suppress warnings for expected non-zero exit codes.
- **Deploy verbose output:** Streaming output now prefixed with deployer tool name and `│` gutter (e.g. `terraform │ ...`), with cyan for stdout and yellow for stderr.

---

## [0.9.2] — 2026-06-19

### Fixed

- Ansible builder now skips writing variable files for empty sections (providers, topologies, resources, modules, namespaces, firewalls, DNS, networks). Previously all 9 files were written unconditionally, producing empty `strata_*.yml` files for features not configured in a deployment.
- `test_version.py` fixture replaced fragile `../../VERSION.txt` relative path with `Path(__file__)`-anchored navigation; added `.strip()` to handle trailing newline in `VERSION.txt`. Fixes version test failures in CI.
- Removed invalid `sync` input from `astral-sh/setup-uv@v8.1.0` in `install-python` action, eliminating a warning on every CI job.

---

## [0.9.1] — 2026-06-19

### Changed

- SBOM collector warnings (floating tags, parse errors) are now silent by default during `strata build run`. The SBOM is still generated with full fidelity — use `--verbose` or run `strata build sbom` explicitly to see advisories. Configured policies continue to evaluate component properties regardless of warning visibility.

---

## [0.9.0] — 2026-06-18

### Breaking Changes

- **Configuration YAML schema:** `spec.repositories` renamed to `spec.remotes` in configuration files (ADR 0010). Existing configuration YAML files must update the field name. Solution repos (`solution.json → spec.repositories`) are unchanged.

### Added

- Self-SBOM generation in CI (`ci-build.yml` Job 3): `strata build sbom --scan .` runs against its own source tree, producing a CycloneDX 1.6 `sbom.json` artifact (dogfooding)
- `sbom.json` artifact attached to every GitHub Release via `ci-release.yml`
- `workflow_dispatch` trigger on `ci-docs.yml` for manual doc builds and testing
- Edge image jobs now gate on SBOM job success (`needs: [build, docs, sbom]`)
- ADR 0010: Decision record for renaming configuration repositories to remotes

### Changed

- `RepositoryModel` renamed to `RemoteModel` (backwards-compatible alias retained)
- `RepositoryType` enum renamed to `RemoteType` (backwards-compatible alias retained)
- `ConfigurationService.get_repositories()` → `get_remotes()`
- `ConfigurationService.get_repo_map()` → `get_remote_map()`
- `ConfigurationModel.get_repo_map()` → `get_remote_map()`
- `SourceModel.repository` field description corrected to reference solution repos

### Fixed

- **Bug:** Workspace deep validation checked `configuration_model.spec.repositories` (manifest backends) instead of solution repos for provisioner `source.repository` references — repos registered via `strata repo add` now validate correctly

---

## [0.8.2] — 2026-06-17

### Fixed

- CI docs workflow: Added `--group doc` to `uv sync` to include Sphinx dependencies
- CI docs workflow: Removed impossible `if` condition that prevented deployment on tag triggers

---

## [0.8.1] — 2026-06-17

### Added

- GitHub Pages CI workflow for automated documentation deployment on release tags
- Enhanced workspace volume model with optional fields: `size`, `mount_path`, `driver`, `mode`, `configuration`

### Fixed

- Issue #109: Allow any string for topology volume type (removed restrictive enum)
- Clarified `access_mode` (container-level concurrency) vs `mode` (filesystem permissions) in volume model

### Changed

- Documentation now deployed to GitHub Pages (`https://huybrechtsxyz.github.io/strata`)
- Support link updated to GitHub Issues (`https://github.com/huybrechtsxyz/strata/issues`)
- Updated `DOCS_URL` and `SUPPORT_URL` constants in `src/strata/utils/config.py`

---

## [0.8.0] — 2026-06-17

### Added

- **CVE audit (`strata build sbom --audit`)** — vulnerability scanning step that runs after SBOM generation using a locally-installed scanner (Trivy preferred, Grype as fallback). No external API calls — the scanner runs offline against the generated `sbom.json`.
  - `--audit` flag enables scanning; no-op with a warning when no scanner is in PATH.
  - `--severity LEVEL` (default `MEDIUM`) sets the minimum severity to report (`CRITICAL` | `HIGH` | `MEDIUM` | `LOW` | `UNKNOWN`).
  - `--fail-on LEVEL` exits with code 3 when findings at or above the given severity exist. Without `--fail-on`, the audit is advisory only.
  - Console output: severity summary table + top 10 findings.
  - NDJSON output: each finding emitted as a `data` event with an `audit_finding` payload.
  - JSON/text output: `audit` key added to the result envelope with severity counts.
- **`CveScannerIntegration`** — new integration (`integrations/cve_scanner.py`) wrapping Trivy and Grype. Auto-detects whichever backend is in PATH. Exposes `scan_sbom(path, severity_threshold, timeout) → CveAuditResultModel`.
- **`CveFindingModel` / `CveAuditResultModel`** — Pydantic models for structured CVE scan results added to `models/sbom_model.py`.
- **`sbom_license` policy** — new built-in policy type that enforces license allow/deny lists on SBOM components at the `build` phase. Reads each component's `strata:license` property (set by collectors or lockfile parsers). Supports fnmatch globs (`BSD-*`, `GPL-*`). Deny always wins over allow when both lists are configured. `unknown_action` setting controls behaviour for components without license metadata (`allow` | `warn` | `deny`; default `warn`).
- **Expanded lockfile parser support** — six new built-in parsers added to `DependencyFileCollector`:
  - `NugetPackagesLockParser` — `packages.lock.json` (.NET / NuGet)
  - `PackagesConfigParser` — `packages.config` (.NET / NuGet)
  - `MavenPomParser` — `pom.xml` (Java / Maven)
  - `GradleLockParser` — `gradle.lockfile` (Java / Maven)
  - `GemfileLockParser` — `Gemfile.lock` (Ruby / gem)
  - `CargoLockParser` — `Cargo.lock` (Rust / cargo)
  - `ComposerLockParser` — `composer.lock` (PHP / Packagist)
- **Lockfile parser package refactor** — `builders/sbom/lockfile_parsers/` restructured from a single file into a package with one module per ecosystem; all parsers re-exported from `__init__.py` for backward compatibility.
- **Auto-discovery from `.strata/lockfile_parsers/`** — any `.py` file dropped in `.strata/lockfile_parsers/` at the workspace root is imported automatically before the first SBOM build. No `collectors.yaml` entry required. Files prefixed with `_` are skipped.
- **NDJSON datalines for `strata build sbom --scan`** — with `--output ndjson`, one `data` event is emitted per discovered SBOM component in addition to the terminal `complete` event.

### Changed

- `SbomBuildCommand` constructor extended with `audit`, `audit_severity`, and `fail_on` parameters wired from the new CLI flags.

---

## [0.7.0] — 2026-06-16

### Added

- **`strata policy check`** — standalone command to evaluate all declared policies for a given deployment file without running a deploy. Accepts `--file` / `-f` (required). Reports each policy result with pass/fail, enforcement level, and any violations. Exits with code 3 when one or more `deny` policies fail; exits 0 when all policies pass or only `warn`/`audit` policies are violated.
- **`strata deploy outputs`** — reads stored deployment output artifact files written by a previous `strata deploy run`. Accepts `--stage`, `--key`, `--version`, and `--all-versions`. Resolves artifacts from `{work_path}/{outputs.path}/{deployment_name}/{version}/{stage}.json`; supports filtering to a single key for scripting.
- **Deploy phase policy hook** — `RunDeployCommand._evaluate_phase_policies("deploy", …)` is now evaluated after Terraform plan succeeds and before apply runs. This extends the existing plan-phase gate so policies annotated with `phase: deploy` can block the apply step independently.
- **`ManifestOutputsReferenceModel`** — new Pydantic model that records the workspace-relative path, stage name, version, and `written_at` ISO-8601 timestamp of a stored outputs artifact. Added as `ManifestStageModel.outputs_artifact` so each stage in the deployment manifest carries a typed reference to its output file when one was written.
- **`NamingPolicy` `targets` parameter** — the `naming_pattern` built-in policy now accepts an optional `targets` list in its `configuration` block, defaulting to `["config_name"]` for full backward compatibility. Available targets: `config_name`, `deployment_name`, `stage_names`, `workspace_name`, `topology_names`, `resource_names`, `namespace_names`, `provisioner_names`, `module_names`, `volume_names`. Targets whose required service is absent from the evaluation context are silently skipped. Unknown target names produce a policy violation so misconfiguration is caught early.

### Changed

- `RunDeployCommand._evaluate_plan_policies` renamed to `_evaluate_phase_policies(phase, stage, deployer)` — the method now accepts an explicit phase string so it can serve both `plan` and `deploy` gates without duplication.

---

## [0.6.0] — 2026-06-15

### Added

- **Policy engine** — declarative deployment guardrails evaluated at validate, build, and plan phases.
  - `PolicyModel` — Pydantic model for policy declarations in `configuration.spec.policies` (`name`, `type`, `phase`, `enforcement`, `enabled`, `description`, `configuration`).
  - `BasePolicy` / `PolicyContext` / `PolicyResult` — abstract base, evaluation context dataclass, and typed result dataclass.
  - `PolicyEngine` — coordinator that instantiates built-in and custom policy types, evaluates a list of enabled policies for a given phase, and accumulates results.
  - Four built-in policy types:
    - `customer_zone` — denies deploy when the target cluster is not in an allowed customer zone.
    - `required_tags` — verifies that every namespace in the built platform artifact carries the configured required labels.
    - `naming_pattern` — validates `meta.name` of the active configuration against a required regex pattern.
    - `script` — delegates to an external script or tool (OPA, Checkov, custom) via subprocess; passes JSON context on stdin and reads violations from stdout/stderr.
  - **Validate phase hook** — `ValidateCommand` evaluates all `phase: validate` policies after structural validation; violations with `enforcement: deny` promote the command to exit code 3.
  - **Build phase hook** — `RunBuildCommand` evaluates all `phase: build` policies after a successful SBOM build step.
  - **Plan phase hook** — `RunDeployCommand` evaluates all `phase: plan` policies after Terraform plan output is available.
  - **Policies in platform artifact** — `PlatformSpecModel.policies` field carries the full policy declaration list into `platform.json` so deploy-time inspection doesn't require the source configuration.
  - **Policy results in deployment manifest** — `ManifestPolicyResultModel` captures per-policy outcomes; `DeploymentManifestSpecModel.policy_results` accumulates them across all evaluated phases, making audit data available in the signed manifest.
- **`PolicyController`** — `get_declared_policies(configuration_service)` extracts the policy list; `get_deployment_phases(deployment_service)` infers which lifecycle phases a deployment's stages can trigger via keyword matching on provisioner and topology names.
- **`strata policy list`** — introspection command that lists every policy declared in the active configuration. Columns: Name, Type, Phase, Enforcement, Enabled. `deny` enforcement highlighted red, `warn` yellow. Summary line reports enabled/disabled counts. Accepts `--file` / `-f` to load a deployment YAML and annotate the output with which lifecycle phases that deployment can trigger. Supports `--output json` / `--output text` / `--output ndjson` for machine consumption.

---

## [0.5.0] — 2026-06-12

### Added

- **SBOM generation** — CycloneDX 1.6 JSON Software Bill of Materials written automatically after every `strata build run`, stored as `sbom.json` alongside `platform.json` in the deployment build directory.
- **`strata build sbom`** — standalone command to regenerate the SBOM from an existing `platform.json` without a full rebuild.
- **Extensible collector pattern** — `BaseSbomCollector` abstract base with four built-in collectors:
  - `ContainerImageCollector` — scans `platform.spec.modules[].services[].image` for container images (`pkg:docker/…` PURLs). Floating tags (`latest`, `main`, `dev`, etc.) are flagged with a `strata:tag-stability=floating` CycloneDX property and a `WARNING` log.
  - `HelmChartCollector` — collects Helm charts from provisioners with `type: helm` (`pkg:helm/…` PURLs, with `repository_url` qualifier when a repository is set).
  - `TerraformProviderCollector` — parses `required_providers {}` blocks from `*.tf` files in the build directory via `python-hcl2` (`pkg:terraform/…` PURLs).
  - `AnsibleCollectionCollector` — reads `requirements.yml` files in the build directory for collections and roles (`pkg:ansible/…` PURLs).
- **`SbomReferenceModel`** — Pydantic model recording the SBOM path, format, SHA-256 digest, and component count. Stored on `DeploymentManifestSpecModel.artifacts.sbom`.
- **`utils/sbom_utils.py`** — pure-function PURL helpers and floating-tag detection with no dependency on the SBOM library (fully unit-testable).
- **`utils/ansible_utils.py`** — shared `find_ansible_requirements_file()` utility used by both `AnsibleCollectionCollector` and `AnsibleDeployer` to eliminate duplicated discovery logic.
- `cyclonedx-python-lib >=7.0,<9` and `packageurl-python >=0.11,<2` added as runtime dependencies.

### Changed

- `DeploymentManifestSpecModel.artifacts.sbom` field typed as `Optional[SbomReferenceModel]` (was `Optional[Dict[str, Any]]`).
- `AnsibleDeployer._get_requirements_file()` now delegates to `find_ansible_requirements_file()` from `utils/ansible_utils`.
- `strata build run` full pipeline now includes SBOM generation as step 6 (after Helm builder).

### Documentation

- Added `SbomBuilder` to `docs/platform/builders.md` — output location, collector table, floating-tag behaviour, three-phase pipeline, and extension guide.
- Added `strata build sbom` to `docs/platform/commands.md`; updated `build run` description to list all pipeline steps.
- Added `sbom_utils.py` and `ansible_utils.py` sections to `docs/platform/utilities.md`.

---

## [0.2.0] — 2026-06-09

### Added

- **`strata env status`** — show live infrastructure status per deployment stage (resources, serial, outputs from `terraform show -json`). Supports `--offline` for cached-only mode. `--all` / `--path DIR` scan multiple deployment manifests and show a one-line summary per deployment (offline/cache-based).
- **`strata env drift`** — detect drift between desired config and live infrastructure using `terraform plan -detailed-exitcode`. Reports create/update/delete/replace counts per stage.
- **`strata values set`** — write a value to its configured store backend. Dispatches to constant (print location), environment (export instruction), github (`gh secret set`), or integration backends. Supports `--value`, `--from-file`, and `--stdin` for multiline input.
- **`strata values resolve`** — diagnose value resolution paths without revealing actual values. Walks the resolution chain with checkpoints per store type. `--probe` flag attempts actual backend resolution (pass/fail only).
- **Environment module overrides** — new `spec.overrides.modules` schema in environment YAML for pinning container images, Helm chart versions, and module enabled state per environment without modifying module definitions.
  - `services` list with `name` + `image` for per-service image pinning.
  - `chart_version` field for Helm chart version overrides.
  - Optional `resource`, `namespace`, `slot_type` qualifiers for scoping when a module appears in multiple places.
  - Specificity-based matching: most specific override wins.
  - Validates: mutual exclusivity of resource/namespace, unique service names.

### Changed

- Renamed internal `commands/env/` package to `commands/envs/` to avoid collision with the reserved word `env`.
- `EnvironmentModuleOverrideModel` redesigned: `module` is now the primary key (required), `resource` is optional (was required).
- `get_module_override()` service method uses specificity scoring instead of exact tuple match.
- `get_overridden_module_keys()` returns `(module, resource, namespace, slot_type)` tuples.
- Deployment service applies module overrides by scanning all matching workspace resources rather than requiring exact resource targeting.

### Documentation

- Added `Overrides` section to `docs/config/environment.md` with full module override schema and examples.
- Added cross-reference in `docs/config/module.md` pointing to environment overrides for image pinning.
- Added `values set` and `values resolve` to `docs/platform/commands.md` and `docs/platform/workflow.md`.
- Created `.archive/releases.md` — release & version management analysis for multi-repo deployment orchestration.

---

## [0.1.1] — 2026-06-07

### Added

- **Cross-module `depends_on`** — `@module/service` syntax for dependencies between modules in the same namespace. Validated at build time. Shorthand `@module` when module name equals service name.
- `strata output` — show Terraform outputs from cache or live backend.

### Changed

- `ComposeBuilder` uses two-pass build: first pass builds a namespace service registry, second pass resolves all `depends_on` refs (intra-module and cross-module).

---

## [0.1.0] — 2026-05-14

_First real release. Everything before this was iterative scaffolding toward a working platform._

### Core Platform

- Layered architecture: `commands/` → `controllers/` → `services/` → `integrations/` → `models/` → `utils/`.
- Pydantic v2 models for all YAML document kinds: workspace, environment, deployment, configuration, resource, namespace, module, provider, firewall.
- Click CLI with flat `strata <group> <command>` structure.
- Exit codes: `0` success, `1` system failure, `2` usage error, `3` validation failure.
- Structured logging via `structlog`. Audit logging subsystem.
- Project renamed from `xyz-platform` to `strata`.

### CLI Commands

- `strata validate` — validate any YAML config file with structured error output.
- `strata build run` / `plan` / `clean` — artifact generation pipeline. `plan` is dry-run.
- `strata deploy run` / `destroy` / `status` / `health` — deployment lifecycle.
- `strata diff` — preview environment changes before deploying.
- `strata sln init` / `clean` / `status` / `export` — workspace lifecycle.
- `strata repo` — register, clone, sync external repositories.
- `strata profile` — manage environment profiles.
- `strata config` — persist CLI preferences to `.strata/cli.yaml`.
- `strata vars` — variable inspection.
- `strata log` — audit log listing and configuration.
- `strata tools` — list and check external integration availability.
- `strata schema` — export JSON schema for all document kinds.
- `--file` / `-f` option across build, deploy, validate commands (`STRATA_FILE` env var).

### Compose Builder

- Multi-service modules — `spec.services` with per-service image, ports, mounts, healthcheck, environment, `depends_on`.
- One `docker-compose.yml` per namespace. All compose modules merged into a single file.
- Service name prefixing: `{module}-{service}` (omitted when equal).
- Pass-through mode — `spec.compose_file` copies an external compose file verbatim.
- Module file copy — `spec.files` with glob, `@repo/` refs, and template substitution.
- `.env` file generated at deploy time with resolved secrets/variables.

### Integrations

- Terraform: `init` / `plan` / `apply` / `destroy` / `output` via subprocess.
- Secret resolution: Bitwarden, Azure Key Vault, HashiCorp Vault, environment variables.

### DevOps

- GitHub Actions composite actions: `setup-strata`, `validate`, `build-run`, `build-plan`, `diff`, `deploy-run`, `deploy-destroy`, `run`.
- CI workflows: `install-python`, `test-python`.
- Docker images: `Dockerfile.cli` (production), `Dockerfile.docs` (documentation site).
- Scaffold templates for `strata sln init --template <name>`.

### Documentation

- Platform docs: architecture, builders, deployers, CLI reference, getting started.
- Operator guides: cookbook, cross-env patterns, troubleshooting, FAQ.
- Config reference: all YAML document kinds.
- Shell completion (Bash, Zsh, Fish).

<!--
To release a new version:
- Move entries from [Unreleased] into a new ## [x.y.z] — YYYY-MM-DD section.
- Update VERSION.txt to match.
- Tag: git tag vx.y.z && git push origin vx.y.z
-->

## Legend

- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** in case of vulnerabilities
- **Infrastructure** for CI/CD and build changes
- **Documentation** for doc-only changes
